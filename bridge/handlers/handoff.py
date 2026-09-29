"""
One-shot file handoff between competencies.

Two halves, both meant for tier-2 handlers:

- publish_file(): register a file (bytes or a path on the container disk) and
  get back a public URL served by this bridge at GET /files/<token>. Tokens
  are unguessable, expire after a short TTL and are single-use by default, so
  the URL is a capability that another competency (e.g. manage_file with
  source=url) can redeem exactly once. File bytes never travel through the
  model context.

- fetch_url(): download a public https URL server-side with SSRF guards:
  scheme and resolved host are validated on the initial URL and on every
  redirect hop, and the size cap is enforced both on Content-Length and while
  streaming the body. Validation resolves DNS separately from the actual
  connection, so a hostile nameserver flipping records between the two lookups
  is not fully excluded — same trade-off as the platform's manage_file URL
  download, acceptable for fetching from trusted competency ingresses.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import secrets
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

# ─── Runtime configuration (module-level, monkeypatchable in tests) ───────────
HANDOFF_TTL_SECONDS = float(os.getenv("HANDOFF_TTL_SECONDS", "600"))
HANDOFF_MAX_FETCH_BYTES = int(float(os.getenv("HANDOFF_MAX_FETCH_MB", "200")) * 1024 * 1024)
HANDOFF_FETCH_TIMEOUT_SECONDS = float(os.getenv("HANDOFF_FETCH_TIMEOUT_SECONDS", "120"))
BRIDGE_PUBLIC_BASE_URL = os.getenv("BRIDGE_PUBLIC_BASE_URL", "")

_MAX_REDIRECTS = 3


class HandoffError(Exception):
    """Raised for invalid publish/fetch requests; the message is safe to show the model."""


# ─── Publish side: token store served by GET /files/<token> ───────────────────

@dataclass
class PublishedFile:
    path: Path | None
    data: bytes | None
    file_name: str
    media_type: str
    expires_at: float
    single_use: bool


class HandoffStore:
    """In-memory registry of published files, keyed by unguessable token."""

    def __init__(self) -> None:
        self._entries: dict[str, PublishedFile] = {}
        self._lock = asyncio.Lock()

    async def publish(self, entry: PublishedFile) -> str:
        token = secrets.token_urlsafe(32)
        async with self._lock:
            self._prune_expired()
            self._entries[token] = entry
        return token

    async def take(self, token: str) -> PublishedFile | None:
        """Return the entry for a live token, consuming it when single-use."""
        async with self._lock:
            self._prune_expired()
            entry = self._entries.get(token)
            if entry is None:
                return None
            if entry.single_use:
                del self._entries[token]
            return entry

    def _prune_expired(self) -> None:
        now = time.monotonic()
        for token in [t for t, e in self._entries.items() if e.expires_at <= now]:
            del self._entries[token]


store = HandoffStore()


def public_base_url() -> str:
    """The https base URL another competency can reach this bridge at.

    Explicit BRIDGE_PUBLIC_BASE_URL wins; otherwise fall back to the FQDN
    Azure Container Apps exposes through its standard environment variables.
    """
    if BRIDGE_PUBLIC_BASE_URL:
        return BRIDGE_PUBLIC_BASE_URL.rstrip("/")
    app_name = os.getenv("CONTAINER_APP_NAME", "")
    dns_suffix = os.getenv("CONTAINER_APP_ENV_DNS_SUFFIX", "")
    if app_name and dns_suffix:
        return f"https://{app_name}.{dns_suffix}"
    raise HandoffError(
        "The bridge does not know its public URL: set BRIDGE_PUBLIC_BASE_URL."
    )


async def publish_file(
    *,
    data: bytes | None = None,
    path: str | Path | None = None,
    file_name: str,
    media_type: str = "application/octet-stream",
    ttl_seconds: float | None = None,
    single_use: bool = True,
) -> str:
    """Publish a file and return the public one-shot URL for it."""
    if (data is None) == (path is None):
        raise HandoffError("Provide exactly one of data or path.")
    resolved_path: Path | None = None
    if path is not None:
        resolved_path = Path(path)
        if not resolved_path.is_file():
            raise HandoffError(f"No such file to publish: '{resolved_path}'.")
    ttl = HANDOFF_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    token = await store.publish(
        PublishedFile(
            path=resolved_path,
            data=data,
            file_name=file_name,
            media_type=media_type,
            expires_at=time.monotonic() + ttl,
            single_use=single_use,
        )
    )
    return f"{public_base_url()}/files/{token}"


# ─── Fetch side: SSRF-guarded server-side download ────────────────────────────

@dataclass
class FetchedFile:
    """A fetched file, either in memory (`data`) or staged on disk (`path`).

    Handlers that only forward the bytes elsewhere should ask for `to_path`:
    a bridge container is sized for its own work, and holding a large transfer
    in RAM is the difference between a slow copy and an OOM kill.
    """

    file_name: str
    media_type: str
    data: bytes | None = None
    path: Path | None = None

    def read_bytes(self) -> bytes:
        """The content, reading it back from disk when the fetch was staged."""
        if self.data is not None:
            return self.data
        if self.path is not None:
            return self.path.read_bytes()
        raise HandoffError("Fetched file carries neither data nor path.")


async def _resolve_host(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.run_in_executor(
        None, lambda: socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    )
    return [info[4][0] for info in infos]


def _same_environment_suffix() -> str:
    """The DNS suffix shared by the competencies deployed next to this one.

    Container Apps gives every environment its own suffix with a random token
    in it (`politeflower-70dfddde.francecentral.azurecontainerapps.io`), and
    injects it into each container. A hostname under that suffix therefore
    belongs to this dorg's own environment: it cannot be forged from outside.
    """
    return os.getenv("CONTAINER_APP_ENV_DNS_SUFFIX", "").strip().lower()


def trusted_handoff_hosts() -> frozenset[str]:
    """Extra hosts the operator vouches for, on top of the sibling services.

    Escape hatch for a peer that lives in *another* environment. The normal
    case — the competencies of the same dorg — is recognised automatically by
    `_same_environment_suffix`, so nobody has to paste an internal hostname
    into the Console for it.
    """
    raw = os.getenv("HANDOFF_TRUSTED_HOSTS", "")
    return frozenset(host.strip().lower() for host in raw.split(",") if host.strip())


def _is_trusted_peer(host: str) -> bool:
    """True for a competency of this same dorg, or an explicitly listed host."""
    host = host.lower()
    suffix = _same_environment_suffix()
    if suffix and host.endswith("." + suffix):
        return True
    return host in trusted_handoff_hosts()


async def validate_public_https_url(url: str) -> httpx.URL:
    """Accept only https URLs whose host resolves exclusively to public addresses.

    A competency of this same dorg is exempt from the address rule — never from
    the https requirement — because side-by-side services resolve each other's
    public FQDN to an internal address. This runs again on every redirect, so a
    trusted host cannot bounce the fetch onto an untrusted one.
    """
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise HandoffError(f"Only https URLs can be fetched, got '{parsed.scheme or 'none'}'.")
    host = parsed.hostname
    if not host:
        raise HandoffError("The URL has no host.")
    if _is_trusted_peer(host):
        return httpx.URL(url)
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = await _resolve_host(host)
        except OSError as exc:
            raise HandoffError(f"Cannot resolve host '{host}': {exc}.") from exc
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise HandoffError(
                f"Host '{host}' resolves to a non-public address; refusing to fetch. "
                "Competencies of this same dorg are recognised automatically; a peer in "
                "another environment has to be listed in HANDOFF_TRUSTED_HOSTS."
            )
    return httpx.URL(url)


def _file_name_from_response(url: httpx.URL, response: httpx.Response) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", disposition, re.IGNORECASE)
    if match:
        candidate = Path(unquote(match.group(1).strip())).name
        if candidate:
            return candidate
    candidate = Path(unquote(url.path)).name
    return candidate or "download"


async def fetch_url(
    url: str,
    *,
    max_bytes: int | None = None,
    timeout_seconds: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    to_path: str | Path | None = None,
) -> FetchedFile:
    """Fetch one file from a public https URL, following at most _MAX_REDIRECTS
    redirects and re-validating scheme + resolved host on every hop."""
    cap = HANDOFF_MAX_FETCH_BYTES if max_bytes is None else max_bytes
    timeout = HANDOFF_FETCH_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    target = await validate_public_https_url(url)

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, transport=transport
    ) as client:
        hops = 0
        while True:
            request = client.build_request("GET", target)
            response = await client.send(request, stream=True)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    raise HandoffError(f"Redirect from '{target}' carries no location.")
                hops += 1
                if hops > _MAX_REDIRECTS:
                    raise HandoffError(f"Too many redirects (>{_MAX_REDIRECTS}) fetching '{url}'.")
                target = await validate_public_https_url(str(target.join(location)))
                continue
            break

        try:
            if response.status_code != 200:
                raise HandoffError(f"GET '{target}' returned HTTP {response.status_code}.")
            declared = response.headers.get("content-length")
            if declared is not None and int(declared) > cap:
                raise HandoffError(
                    f"Remote file is {int(declared)} bytes, above the {cap}-byte transfer cap."
                )
            chunks: list[bytes] = []
            received = 0
            destination = Path(to_path) if to_path is not None else None
            handle = destination.open("wb") if destination is not None else None
            try:
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > cap:
                        raise HandoffError(
                            f"Remote file exceeds the {cap}-byte transfer cap."
                        )
                    if handle is not None:
                        handle.write(chunk)
                    else:
                        chunks.append(chunk)
            except BaseException:
                if handle is not None:
                    handle.close()
                    handle = None
                    destination.unlink(missing_ok=True)  # type: ignore[union-attr]
                raise
            finally:
                if handle is not None:
                    handle.close()
        finally:
            await response.aclose()

    media_type = response.headers.get("content-type", "application/octet-stream")
    media_type = media_type.split(";", 1)[0].strip() or "application/octet-stream"
    file_name = _file_name_from_response(target, response)
    if to_path is not None:
        return FetchedFile(file_name=file_name, media_type=media_type, path=Path(to_path))
    return FetchedFile(file_name=file_name, media_type=media_type, data=b"".join(chunks))
