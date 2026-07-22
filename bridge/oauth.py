"""
OAuth 2.0 refresh-token grant for upstream MCP servers (UPSTREAM_AUTH_MODE=oauth).

The bridge holds a long-lived refresh token (minted once, interactively, via
`python -m cli oauth-bootstrap`) and exchanges it for short-lived access tokens
at runtime. Access tokens are cached in-process and refreshed shortly before
expiry; concurrent requests share a single refresh call.

Providers that rotate refresh tokens (single-use, e.g. HubSpot) return a new
refresh_token with each response; the bridge keeps the latest one in memory and
uses it for subsequent refreshes. The env value only seeds the first exchange,
so after a container restart it may already be consumed and need re-minting.
"""

from __future__ import annotations

import asyncio
import os
import time

import httpx

# ─── Runtime configuration (ENV only — never bake secrets into the image) ─────
UPSTREAM_OAUTH_TOKEN_URL = os.getenv(
    "UPSTREAM_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token"
)
UPSTREAM_OAUTH_CLIENT_ID = os.getenv("UPSTREAM_OAUTH_CLIENT_ID", "")
UPSTREAM_OAUTH_CLIENT_SECRET = os.getenv("UPSTREAM_OAUTH_CLIENT_SECRET", "")
UPSTREAM_OAUTH_REFRESH_TOKEN = os.getenv("UPSTREAM_OAUTH_REFRESH_TOKEN", "")
UPSTREAM_OAUTH_SCOPES = os.getenv("UPSTREAM_OAUTH_SCOPES", "")
UPSTREAM_OAUTH_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_OAUTH_TIMEOUT_SECONDS", "30"))

# Refresh this many seconds before the access token actually expires.
_EXPIRY_SKEW_SECONDS = 120

_cache: dict[str, object] = {"access_token": "", "expires_at": 0.0, "refresh_token": ""}
_lock = asyncio.Lock()


class OAuthError(RuntimeError):
    """Raised when an access token cannot be obtained."""


def invalidate() -> None:
    """Drop the cached access token (e.g. after an upstream 401)."""
    _cache["access_token"] = ""
    _cache["expires_at"] = 0.0


def _cached_token() -> str:
    token = _cache["access_token"]
    if token and time.time() < float(_cache["expires_at"]) - _EXPIRY_SKEW_SECONDS:
        return str(token)
    return ""


async def _post_token(data: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=UPSTREAM_OAUTH_TIMEOUT_SECONDS) as client:
        return await client.post(UPSTREAM_OAUTH_TOKEN_URL, data=data)


async def _refresh() -> str:
    if not (
        UPSTREAM_OAUTH_CLIENT_ID
        and UPSTREAM_OAUTH_CLIENT_SECRET
        and UPSTREAM_OAUTH_REFRESH_TOKEN
    ):
        raise OAuthError(
            "UPSTREAM_AUTH_MODE=oauth requires UPSTREAM_OAUTH_CLIENT_ID, "
            "UPSTREAM_OAUTH_CLIENT_SECRET and UPSTREAM_OAUTH_REFRESH_TOKEN."
        )

    data = {
        "grant_type": "refresh_token",
        "client_id": UPSTREAM_OAUTH_CLIENT_ID,
        "client_secret": UPSTREAM_OAUTH_CLIENT_SECRET,
        "refresh_token": str(_cache.get("refresh_token") or "") or UPSTREAM_OAUTH_REFRESH_TOKEN,
    }
    if UPSTREAM_OAUTH_SCOPES:
        data["scope"] = UPSTREAM_OAUTH_SCOPES

    resp = await _post_token(data)

    if resp.status_code != 200:
        error_code = ""
        try:
            error_code = str(resp.json().get("error", ""))
        except ValueError:
            pass
        if error_code == "invalid_grant":
            raise OAuthError(
                "OAuth refresh token expired or revoked (invalid_grant). "
                "Re-run `python -m cli oauth-bootstrap` and update "
                "UPSTREAM_OAUTH_REFRESH_TOKEN."
            )
        raise OAuthError(
            f"OAuth token refresh failed: HTTP {resp.status_code} {error_code}".rstrip()
        )

    payload = resp.json()
    token = payload.get("access_token")
    if not token:
        raise OAuthError("OAuth token response is missing access_token.")

    _cache["access_token"] = token
    _cache["expires_at"] = time.time() + float(payload.get("expires_in", 3600))
    if payload.get("refresh_token"):
        _cache["refresh_token"] = str(payload["refresh_token"])
    return str(token)


async def get_access_token(force_refresh: bool = False) -> str:
    """Return a valid access token, refreshing via the token endpoint if needed."""
    if not force_refresh:
        token = _cached_token()
        if token:
            return token
    async with _lock:
        if not force_refresh:
            token = _cached_token()
            if token:
                return token
        return await _refresh()
