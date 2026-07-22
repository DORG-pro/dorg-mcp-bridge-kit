"""
One-time interactive OAuth 2.0 authorization-code flow to mint a refresh token.

Opens the provider's consent page in a browser, catches the redirect on a
local HTTP listener, exchanges the code and prints the refresh token to
stdout. Nothing is written to disk; the operator pastes the refresh token
into the competency env (UPSTREAM_OAUTH_REFRESH_TOKEN).

Defaults target Google (`access_type=offline&prompt=consent` forces a fresh
refresh token on every run).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import sys
import time
import urllib.parse
import webbrowser
from typing import Any

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def build_auth_url(
    auth_url: str,
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    code_challenge: str | None = None,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{auth_url}?{urllib.parse.urlencode(params)}"


def exchange_code(
    token_url: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    timeout: float = 30.0,
    code_verifier: str | None = None,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(token_url, data=data)
    resp.raise_for_status()
    return resp.json()


def _wait_for_callback(server: http.server.HTTPServer, received: dict, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    server.timeout = 1.0
    while not received and time.monotonic() < deadline:
        server.handle_request()
    if not received:
        raise TimeoutError(f"No OAuth callback received within {timeout_seconds:.0f}s.")


def run_bootstrap(
    client_id: str,
    client_secret: str,
    scopes: str,
    auth_url: str = GOOGLE_AUTH_URL,
    token_url: str = GOOGLE_TOKEN_URL,
    port: int = 0,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    state = secrets.token_urlsafe(16)
    # PKCE (S256): required by some providers (HubSpot, Canva), ignored by the rest.
    code_verifier = secrets.token_urlsafe(48)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    received: dict[str, str] = {}

    class _CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            received.update(
                {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h3>Authorization received.</h3>"
                b"<p>You can close this tab and return to the terminal.</p></body></html>"
            )

        def log_message(self, *args: object) -> None:  # silence request logging
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    try:
        redirect_uri = f"http://localhost:{server.server_address[1]}/callback"
        url = build_auth_url(auth_url, client_id, redirect_uri, scopes, state, code_challenge)
        print(
            "Opening the browser for consent. If it does not open, visit:\n"
            f"  {url}\n"
            f"(waiting for the redirect on {redirect_uri})",
            file=sys.stderr,
        )
        webbrowser.open(url)
        _wait_for_callback(server, received, timeout_seconds)
    finally:
        server.server_close()

    if received.get("state") != state:
        raise RuntimeError("OAuth callback state mismatch — aborting (possible CSRF).")
    if "error" in received:
        raise RuntimeError(f"Authorization failed: {received['error']}")
    code = received.get("code")
    if not code:
        raise RuntimeError("OAuth callback did not include an authorization code.")

    tokens = exchange_code(
        token_url, client_id, client_secret, code, redirect_uri, code_verifier=code_verifier
    )
    if "refresh_token" not in tokens:
        raise RuntimeError(
            "Token response has no refresh_token. For Google this usually means the "
            "consent screen skipped re-approval — revoke the app's access at "
            "https://myaccount.google.com/permissions and retry."
        )
    return tokens
