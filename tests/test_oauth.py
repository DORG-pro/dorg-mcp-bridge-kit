"""Tests for the oauth upstream auth mode (offline — no real HTTP)."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest

_BRIDGE = Path(__file__).resolve().parents[1] / "bridge"
if str(_BRIDGE) not in sys.path:
    sys.path.insert(0, str(_BRIDGE))

import oauth
import server


@pytest.fixture()
def oauth_env(monkeypatch):
    monkeypatch.setattr(oauth, "_cache", {"access_token": "", "expires_at": 0.0})
    monkeypatch.setattr(oauth, "UPSTREAM_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setattr(oauth, "UPSTREAM_OAUTH_CLIENT_SECRET", "sec")
    monkeypatch.setattr(oauth, "UPSTREAM_OAUTH_REFRESH_TOKEN", "rt")
    return oauth


def _token_response(status=200, payload=None):
    return httpx.Response(
        status,
        json=payload if payload is not None else {"access_token": "tok-1", "expires_in": 3600},
        request=httpx.Request("POST", "http://token.test"),
    )


def test_refresh_then_cache(oauth_env, monkeypatch):
    calls = {"n": 0}

    async def fake_post(data):
        calls["n"] += 1
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "rt"
        return _token_response()

    monkeypatch.setattr(oauth, "_post_token", fake_post)

    assert asyncio.run(oauth.get_access_token()) == "tok-1"
    assert asyncio.run(oauth.get_access_token()) == "tok-1"
    assert calls["n"] == 1


def test_expired_token_is_refreshed(oauth_env, monkeypatch):
    async def fake_post(data):
        return _token_response(payload={"access_token": "tok-2", "expires_in": 3600})

    monkeypatch.setattr(oauth, "_post_token", fake_post)
    oauth._cache.update({"access_token": "tok-old", "expires_at": time.time() + 30})

    # 30s left < 120s skew -> must refresh
    assert asyncio.run(oauth.get_access_token()) == "tok-2"


def test_invalidate_drops_cache(oauth_env):
    oauth._cache.update({"access_token": "tok", "expires_at": time.time() + 3600})
    oauth.invalidate()
    assert oauth._cache["access_token"] == ""


def test_concurrent_requests_share_one_refresh(oauth_env, monkeypatch):
    calls = {"n": 0}

    async def fake_post(data):
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return _token_response()

    monkeypatch.setattr(oauth, "_post_token", fake_post)

    async def burst():
        return await asyncio.gather(*(oauth.get_access_token() for _ in range(5)))

    tokens = asyncio.run(burst())
    assert tokens == ["tok-1"] * 5
    assert calls["n"] == 1


def test_rotated_refresh_token_is_used(oauth_env, monkeypatch):
    seen = []

    async def fake_post(data):
        seen.append(data["refresh_token"])
        n = len(seen)
        return _token_response(
            payload={"access_token": f"tok-{n}", "expires_in": 3600, "refresh_token": f"rt-{n}"}
        )

    monkeypatch.setattr(oauth, "_post_token", fake_post)

    assert asyncio.run(oauth.get_access_token()) == "tok-1"
    oauth.invalidate()
    assert asyncio.run(oauth.get_access_token()) == "tok-2"
    # env token seeds the first exchange only; the rotated token is used afterwards
    assert seen == ["rt", "rt-1"]


def test_invalid_grant_has_actionable_error(oauth_env, monkeypatch):
    async def fake_post(data):
        return _token_response(status=400, payload={"error": "invalid_grant"})

    monkeypatch.setattr(oauth, "_post_token", fake_post)

    with pytest.raises(oauth.OAuthError, match="oauth-bootstrap"):
        asyncio.run(oauth.get_access_token())


def test_missing_config_raises(oauth_env, monkeypatch):
    monkeypatch.setattr(oauth, "UPSTREAM_OAUTH_REFRESH_TOKEN", "")
    with pytest.raises(oauth.OAuthError, match="UPSTREAM_OAUTH_REFRESH_TOKEN"):
        asyncio.run(oauth.get_access_token())


# ─── server integration ────────────────────────────────────────────────────────

def test_auth_headers_oauth_mode(monkeypatch):
    monkeypatch.setattr(server, "UPSTREAM_AUTH_MODE", "oauth")

    async def fake_token(force_refresh=False):
        return "tok-xyz"

    monkeypatch.setattr(oauth, "get_access_token", fake_token)
    headers = asyncio.run(server._upstream_auth_headers())
    assert headers == {"Authorization": "Bearer tok-xyz"}


def test_auth_headers_bearer_mode_unchanged(monkeypatch):
    monkeypatch.setattr(server, "UPSTREAM_AUTH_MODE", "bearer")
    monkeypatch.setattr(server, "UPSTREAM_BEARER_TOKEN", "static-tok")
    headers = asyncio.run(server._upstream_auth_headers())
    assert headers == {"Authorization": "Bearer static-tok"}


def test_auth_headers_api_key_mode_unchanged(monkeypatch):
    monkeypatch.setattr(server, "UPSTREAM_AUTH_MODE", "api_key")
    monkeypatch.setattr(server, "UPSTREAM_API_KEY", "k-123")
    headers = asyncio.run(server._upstream_auth_headers())
    assert headers[server.MANIFEST.upstream_auth_header or server.UPSTREAM_AUTH_HEADER] == "k-123"


def test_forward_upstream_retries_once_on_401_in_oauth_mode(monkeypatch):
    monkeypatch.setattr(server, "UPSTREAM_AUTH_MODE", "oauth")
    monkeypatch.setattr(server, "UPSTREAM_MCP_ENDPOINT", "http://upstream.test/mcp")

    invalidated = {"n": 0}
    monkeypatch.setattr(oauth, "invalidate", lambda: invalidated.__setitem__("n", invalidated["n"] + 1))

    responses = [
        httpx.Response(401, request=httpx.Request("POST", "http://upstream.test/mcp")),
        httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
            request=httpx.Request("POST", "http://upstream.test/mcp"),
        ),
    ]

    async def fake_post(rpc):
        return responses.pop(0)

    monkeypatch.setattr(server, "_post_upstream", fake_post)

    result = asyncio.run(server._forward_upstream({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    assert result["result"] == {"ok": True}
    assert invalidated["n"] == 1
    assert not responses


def test_forward_upstream_no_retry_in_api_key_mode(monkeypatch):
    monkeypatch.setattr(server, "UPSTREAM_AUTH_MODE", "api_key")
    monkeypatch.setattr(server, "UPSTREAM_MCP_ENDPOINT", "http://upstream.test/mcp")

    async def fake_post(rpc):
        return httpx.Response(401, request=httpx.Request("POST", "http://upstream.test/mcp"))

    monkeypatch.setattr(server, "_post_upstream", fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(server._forward_upstream({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
