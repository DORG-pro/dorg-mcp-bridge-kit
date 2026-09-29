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


# ── Streaming: serving and fetching without holding the file in RAM ──────────
# A bridge container is sized for its own work (the iCloud one runs on 1 GiB),
# so a transfer that fits on disk must not have to fit in memory as well.


def test_large_published_file_is_streamed_not_buffered(client, tmp_path, monkeypatch):
    """Serving a published path must not read the whole file into memory."""
    import asyncio

    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (5 * 1024 * 1024))

    letti: list[Path] = []
    originale = Path.read_bytes

    def spia(self, *a, **k):
        letti.append(self)
        return originale(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", spia)

    url = asyncio.run(handoff.publish_file(path=big, file_name="big.bin"))
    token = url.rsplit("/", 1)[1]
    resp = client.get(f"/files/{token}")

    assert resp.status_code == 200
    assert len(resp.content) == 5 * 1024 * 1024
    assert big not in letti, "il file pubblicato è stato letto interamente in memoria"


@pytest.mark.asyncio
async def test_fetch_url_can_stage_to_disk(monkeypatch, tmp_path):
    """With to_path the bytes land on disk and never sit in the dataclass."""
    _public_resolver(monkeypatch)
    payload = b"y" * (2 * 1024 * 1024)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=payload, headers={"content-type": "application/pdf"}
        )
    )

    destinazione = tmp_path / "staged.pdf"
    fetched = await handoff.fetch_url(
        "https://files.example.com/report.pdf", transport=transport, to_path=destinazione
    )

    assert fetched.data is None
    assert fetched.path == destinazione
    assert destinazione.read_bytes() == payload
    assert fetched.media_type == "application/pdf"
    assert fetched.read_bytes() == payload


@pytest.mark.asyncio
async def test_fetch_url_to_disk_still_enforces_the_cap(monkeypatch, tmp_path):
    """The cap holds while streaming, and a refused fetch leaves no file behind."""
    _public_resolver(monkeypatch)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"z" * 4096))

    destinazione = tmp_path / "troppo-grande.bin"
    with pytest.raises(handoff.HandoffError):
        await handoff.fetch_url(
            "https://files.example.com/big.bin",
            transport=transport,
            to_path=destinazione,
            max_bytes=1024,
        )

    assert not destinazione.exists(), "un fetch rifiutato non deve lasciare file a metà"


@pytest.mark.asyncio
async def test_fetch_url_without_to_path_keeps_returning_bytes(monkeypatch):
    """The in-memory contract stays intact for the small-file callers."""
    _public_resolver(monkeypatch)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=b"piccolo", headers={"content-type": "text/plain"}
        )
    )

    fetched = await handoff.fetch_url(
        "https://files.example.com/small.txt", transport=transport
    )

    assert fetched.path is None
    assert fetched.data == b"piccolo"
    assert fetched.read_bytes() == b"piccolo"


# ─── HANDOFF_TRUSTED_HOSTS ─────────────────────────────────────────────────────
# Due competenze schierate nello stesso ambiente Container Apps risolvono il
# FQDN pubblico l'una dell'altra su un indirizzo interno: la regola anti-SSRF
# bocciava proprio il trasferimento che esiste per rendere sicuro. L'esenzione
# vale solo per gli host che l'operatore ha elencato.


@pytest.mark.asyncio
async def test_trusted_host_is_accepted_despite_private_resolution(monkeypatch):
    async def resolve_private(host):
        return ["10.0.0.42"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve_private)
    monkeypatch.setenv("HANDOFF_TRUSTED_HOSTS", "files.internal.example.com")

    url = await handoff.validate_public_https_url("https://files.internal.example.com/files/tok")

    assert str(url) == "https://files.internal.example.com/files/tok"


@pytest.mark.asyncio
async def test_trust_is_per_host_not_blanket(monkeypatch):
    """Elencare un host non apre la porta a tutti gli altri host interni."""

    async def resolve_private(host):
        return ["10.0.0.43"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve_private)
    monkeypatch.setenv("HANDOFF_TRUSTED_HOSTS", "files.internal.example.com")

    with pytest.raises(handoff.HandoffError):
        await handoff.validate_public_https_url("https://altro.internal.example.com/files/tok")


@pytest.mark.asyncio
async def test_a_trusted_host_still_cannot_downgrade_to_http(monkeypatch):
    monkeypatch.setenv("HANDOFF_TRUSTED_HOSTS", "files.internal.example.com")

    with pytest.raises(handoff.HandoffError):
        await handoff.validate_public_https_url("http://files.internal.example.com/files/tok")


@pytest.mark.asyncio
async def test_a_trusted_host_cannot_redirect_onto_an_untrusted_one(monkeypatch):
    """L'host fidato non deve diventare un trampolino verso la rete interna."""

    async def resolve_private(host):
        return ["10.0.0.44"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve_private)
    monkeypatch.setenv("HANDOFF_TRUSTED_HOSTS", "files.internal.example.com")

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302, headers={"location": "https://metadata.internal.example.com/latest"}
        )
    )

    with pytest.raises(handoff.HandoffError):
        await handoff.fetch_url(
            "https://files.internal.example.com/files/tok", transport=transport
        )


@pytest.mark.asyncio
async def test_the_list_tolerates_spacing_and_casing(monkeypatch):
    async def resolve_private(host):
        return ["10.0.0.45"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve_private)
    monkeypatch.setenv("HANDOFF_TRUSTED_HOSTS", " Files.Internal.Example.COM , altro.example.com ")

    url = await handoff.validate_public_https_url("https://files.internal.example.com/files/tok")

    assert str(url) == "https://files.internal.example.com/files/tok"


@pytest.mark.asyncio
async def test_without_the_variable_nothing_is_trusted(monkeypatch):
    async def resolve_private(host):
        return ["10.0.0.46"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve_private)
    monkeypatch.delenv("HANDOFF_TRUSTED_HOSTS", raising=False)

    with pytest.raises(handoff.HandoffError) as err:
        await handoff.validate_public_https_url("https://files.internal.example.com/files/tok")

    # l'errore deve dire all'operatore cosa fare, non solo che ha fallito
    assert "HANDOFF_TRUSTED_HOSTS" in str(err.value)


# ─── Competenze dello stesso dorg ──────────────────────────────────────────────
# Il caso normale non deve chiedere niente all'operatore: due competenze dello
# stesso dorg condividono il suffisso DNS dell'ambiente Container Apps, che
# contiene un token casuale e non e' falsificabile dall'esterno.

SUFFISSO = "politeflower-70dfddde.francecentral.azurecontainerapps.io"


@pytest.fixture
def stesso_ambiente(monkeypatch):
    async def resolve_private(host):
        return ["10.0.0.7"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve_private)
    monkeypatch.setenv("CONTAINER_APP_ENV_DNS_SUFFIX", SUFFISSO)
    monkeypatch.delenv("HANDOFF_TRUSTED_HOSTS", raising=False)


@pytest.mark.asyncio
async def test_a_sibling_competency_needs_no_configuration(stesso_ambiente):
    url = await handoff.validate_public_https_url(f"https://files-dorg-david.{SUFFISSO}/files/tok")

    assert str(url) == f"https://files-dorg-david.{SUFFISSO}/files/tok"


@pytest.mark.asyncio
async def test_another_environment_is_not_a_sibling(stesso_ambiente):
    """Il suffisso di un altro ambiente non deve passare: il token e' diverso."""
    altrove = "blackhill-c33eccd5.swedencentral.azurecontainerapps.io"

    with pytest.raises(handoff.HandoffError):
        await handoff.validate_public_https_url(f"https://files-dorg-proctor2.{altrove}/files/tok")


@pytest.mark.asyncio
async def test_a_lookalike_suffix_is_refused(stesso_ambiente):
    """Il confronto e' sul punto di separazione, non su una sottostringa:
    'cattivo-politeflower-...' non e' dentro '.politeflower-...'."""
    with pytest.raises(handoff.HandoffError):
        await handoff.validate_public_https_url(f"https://cattivo-{SUFFISSO}/files/tok")


@pytest.mark.asyncio
async def test_a_domain_that_merely_ends_with_the_suffix_text_is_refused(stesso_ambiente):
    """Un dominio controllato da terzi non diventa fidato aggiungendo il
    suffisso come sotto-dominio del proprio."""
    with pytest.raises(handoff.HandoffError):
        await handoff.validate_public_https_url(f"https://{SUFFISSO}.attaccante.example/files/tok")


@pytest.mark.asyncio
async def test_a_sibling_still_cannot_downgrade_to_http(stesso_ambiente):
    with pytest.raises(handoff.HandoffError):
        await handoff.validate_public_https_url(f"http://files-dorg-david.{SUFFISSO}/files/tok")


@pytest.mark.asyncio
async def test_a_sibling_cannot_redirect_onto_the_internal_network(stesso_ambiente):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302, headers={"location": "https://metadata.interna.example/latest"}
        )
    )

    with pytest.raises(handoff.HandoffError):
        await handoff.fetch_url(f"https://files-dorg-david.{SUFFISSO}/files/tok", transport=transport)


@pytest.mark.asyncio
async def test_outside_container_apps_nothing_is_trusted_for_free(monkeypatch):
    """Senza il suffisso iniettato dalla piattaforma si torna alla sola regola
    sull'indirizzo: nessuna esenzione implicita."""
    async def resolve_private(host):
        return ["10.0.0.8"]

    monkeypatch.setattr(handoff, "_resolve_host", resolve_private)
    monkeypatch.delenv("CONTAINER_APP_ENV_DNS_SUFFIX", raising=False)
    monkeypatch.delenv("HANDOFF_TRUSTED_HOSTS", raising=False)

    with pytest.raises(handoff.HandoffError):
        await handoff.validate_public_https_url(f"https://files-dorg-david.{SUFFISSO}/files/tok")
