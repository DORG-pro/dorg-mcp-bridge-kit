"""Tests for the one-shot file handoff (publish + /files route + SSRF-guarded fetch)."""

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))

from handlers import handoff  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setattr(handoff, "store", handoff.HandoffStore())
    monkeypatch.setattr(handoff, "BRIDGE_PUBLIC_BASE_URL", "https://bridge.example.com")


@pytest.fixture()
def client():
    import server

    return TestClient(server.app)


# ─── publish_file + GET /files/<token> ────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_requires_exactly_one_source(tmp_path):
    with pytest.raises(handoff.HandoffError):
        await handoff.publish_file(file_name="x.bin")
    f = tmp_path / "x.bin"
    f.write_bytes(b"x")
    with pytest.raises(handoff.HandoffError):
        await handoff.publish_file(data=b"x", path=f, file_name="x.bin")


@pytest.mark.asyncio
async def test_publish_missing_path_rejected(tmp_path):
    with pytest.raises(handoff.HandoffError):
        await handoff.publish_file(path=tmp_path / "missing.bin", file_name="missing.bin")


def test_published_bytes_served_once(client):
    import asyncio

    url = asyncio.run(
        handoff.publish_file(data=b"PAYLOAD", file_name="report.pdf", media_type="application/pdf")
    )
    assert url.startswith("https://bridge.example.com/files/")
    token = url.rsplit("/", 1)[1]

    first = client.get(f"/files/{token}")
    assert first.status_code == 200
    assert first.content == b"PAYLOAD"
    assert first.headers["content-type"].startswith("application/pdf")
    assert 'filename="report.pdf"' in first.headers["content-disposition"]

    second = client.get(f"/files/{token}")
    assert second.status_code == 404


def test_published_path_served_from_disk(client, tmp_path):
    import asyncio

    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00\x01\x02")
    url = asyncio.run(
        handoff.publish_file(path=f, file_name="data.bin")
    )
    token = url.rsplit("/", 1)[1]
    resp = client.get(f"/files/{token}")
    assert resp.status_code == 200
    assert resp.content == b"\x00\x01\x02"


def test_expired_token_rejected(client):
    import asyncio

    url = asyncio.run(
        handoff.publish_file(data=b"x", file_name="x.bin", ttl_seconds=-1)
    )
    token = url.rsplit("/", 1)[1]
    assert client.get(f"/files/{token}").status_code == 404


def test_unknown_token_rejected(client):
    assert client.get("/files/not-a-token").status_code == 404


def test_public_base_url_fallback_to_container_apps(monkeypatch):
    monkeypatch.setattr(handoff, "BRIDGE_PUBLIC_BASE_URL", "")
    monkeypatch.setenv("CONTAINER_APP_NAME", "mcp-abc")
    monkeypatch.setenv("CONTAINER_APP_ENV_DNS_SUFFIX", "nicehill-1.westeurope.azurecontainerapps.io")
    assert handoff.public_base_url() == (
        "https://mcp-abc.nicehill-1.westeurope.azurecontainerapps.io"
    )


def test_public_base_url_missing_raises(monkeypatch):
    monkeypatch.setattr(handoff, "BRIDGE_PUBLIC_BASE_URL", "")
    monkeypatch.delenv("CONTAINER_APP_NAME", raising=False)
    monkeypatch.delenv("CONTAINER_APP_ENV_DNS_SUFFIX", raising=False)
    with pytest.raises(handoff.HandoffError):
        handoff.public_base_url()


# ─── URL validation ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_rejects_http():
    with pytest.raises(handoff.HandoffError):
        await handoff.validate_public_https_url("http://example.com/f.bin")


@pytest.mark.asyncio
async def test_validate_rejects_private_ip_literal():
    for bad in ("https://127.0.0.1/x", "https://10.0.0.5/x", "https://[::1]/x"):
        with pytest.raises(handoff.HandoffError):
            await handoff.validate_public_https_url(bad)


@pytest.mark.asyncio
async def test_validate_rejects_private_resolution(monkeypatch):
    async def resolve_private(host):
        return ["192.168.1.20"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve_private)
    with pytest.raises(handoff.HandoffError):
        await handoff.validate_public_https_url("https://internal.example.com/f")


@pytest.mark.asyncio
async def test_validate_accepts_public_resolution(monkeypatch):
    async def resolve_public(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve_public)
    url = await handoff.validate_public_https_url("https://files.example.com/report.pdf")
    assert str(url) == "https://files.example.com/report.pdf"


# ─── fetch_url ─────────────────────────────────────────────────────────────────


def _public_resolver(monkeypatch):
    async def resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve)


@pytest.mark.asyncio
async def test_fetch_url_returns_bytes_and_metadata(monkeypatch):
    _public_resolver(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"FILEBYTES",
            headers={
                "content-type": "application/pdf; charset=binary",
                "content-disposition": 'attachment; filename="doc.pdf"',
            },
        )
    )
    fetched = await handoff.fetch_url("https://files.example.com/x", transport=transport)
    assert fetched.data == b"FILEBYTES"
    assert fetched.file_name == "doc.pdf"
    assert fetched.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_fetch_url_follows_validated_redirect(monkeypatch):
    _public_resolver(monkeypatch)

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://files.example.com/final.bin"})
        return httpx.Response(200, content=b"OK")

    fetched = await handoff.fetch_url(
        "https://files.example.com/start", transport=httpx.MockTransport(handler)
    )
    assert fetched.data == b"OK"
    assert fetched.file_name == "final.bin"


@pytest.mark.asyncio
async def test_fetch_url_rejects_redirect_to_http(monkeypatch):
    _public_resolver(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "http://files.example.com/f"})
    )
    with pytest.raises(handoff.HandoffError):
        await handoff.fetch_url("https://files.example.com/start", transport=transport)


@pytest.mark.asyncio
async def test_fetch_url_rejects_too_many_redirects(monkeypatch):
    _public_resolver(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "https://files.example.com/loop"})
    )
    with pytest.raises(handoff.HandoffError):
        await handoff.fetch_url("https://files.example.com/loop", transport=transport)


@pytest.mark.asyncio
async def test_fetch_url_rejects_declared_oversize(monkeypatch):
    _public_resolver(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=b"x" * 10, headers={"content-length": "10"}
        )
    )
    with pytest.raises(handoff.HandoffError):
        await handoff.fetch_url("https://files.example.com/f", max_bytes=5, transport=transport)


@pytest.mark.asyncio
async def test_fetch_url_rejects_streamed_oversize(monkeypatch):
    _public_resolver(monkeypatch)

    def handler(request):
        resp = httpx.Response(200, content=b"x" * 10)
        resp.headers.pop("content-length", None)
        return resp

    with pytest.raises(handoff.HandoffError):
        await handoff.fetch_url(
            "https://files.example.com/f", max_bytes=5, transport=httpx.MockTransport(handler)
        )


@pytest.mark.asyncio
async def test_fetch_url_non_200_raises(monkeypatch):
    _public_resolver(monkeypatch)
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    with pytest.raises(handoff.HandoffError):
        await handoff.fetch_url("https://files.example.com/f", transport=transport)


@pytest.mark.asyncio
async def test_fetch_url_filename_falls_back_to_url_path(monkeypatch):
    _public_resolver(monkeypatch)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"z"))
    fetched = await handoff.fetch_url(
        "https://files.example.com/folder/archive.zip", transport=transport
    )
    assert fetched.file_name == "archive.zip"
