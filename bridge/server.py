"""
Dorg MCP bridge — static runtime, manifest-driven routing.

Tier-1 (declarative): tool rename, forced_arguments, upstream_injections,
  argument_aliases — all from competency.manifest.json.
Tier 2 (custom code): tools with `handler` field delegate to handlers.py.

Runtime config: ENV for upstream endpoint/credentials + MANIFEST_PATH.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

import oauth
from handlers import HandlerContext, get_handler, get_handler_schemas, load_handlers
from handlers import handoff
from manifest import ORCHESTRATOR_INJECTED_KEYS, ManifestConfig, ManifestToolRegistry

# ─── Runtime configuration (ENV only — never bake secrets into the image) ─────
UPSTREAM_MCP_ENDPOINT = os.getenv("UPSTREAM_MCP_ENDPOINT", "")
UPSTREAM_API_KEY = os.getenv("UPSTREAM_API_KEY", "")
UPSTREAM_BEARER_TOKEN = os.getenv("UPSTREAM_BEARER_TOKEN", "")
UPSTREAM_AUTH_HEADER = os.getenv("UPSTREAM_AUTH_HEADER", "X-Api-Key")
UPSTREAM_AUTH_MODE = os.getenv("UPSTREAM_AUTH_MODE", "api_key").strip().lower()
BRIDGE_AUTH_TOKEN = os.getenv("BRIDGE_AUTH_TOKEN", "")
SERVER_NAME = os.getenv("BRIDGE_SERVER_NAME", "dorg-mcp-bridge")
SERVER_VERSION = os.getenv("BRIDGE_SERVER_VERSION", "1.0.0")
PROTOCOL_VERSION = "2024-11-05"
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "60"))

MANIFEST = ManifestConfig.load()
COMPETENCY_ID = os.getenv("COMPETENCY_ID", MANIFEST.competency_id)
registry = ManifestToolRegistry(MANIFEST)

load_handlers()


def _normalize_log_level(value: str) -> str:
    aliases = {
        "trace": "DEBUG",
        "debug": "DEBUG",
        "information": "INFO",
        "info": "INFO",
        "warning": "WARNING",
        "warn": "WARNING",
        "error": "ERROR",
        "critical": "CRITICAL",
        "fatal": "CRITICAL",
    }
    return aliases.get(value.strip().lower(), value.strip().upper())


logging.basicConfig(level=_normalize_log_level(os.getenv("LOG_LEVEL", "INFO")))
logger = logging.getLogger(SERVER_NAME)

app = FastAPI(title="Dorg MCP bridge", version=SERVER_VERSION)

_DOC_PATH = Path(os.getenv("DOCUMENTATION_PATH", "DOCUMENTATION.md"))
_ICON_PATH = Path(os.getenv("ICON_PATH", "icon.png"))


async def _upstream_auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    auth_header = MANIFEST.upstream_auth_header or UPSTREAM_AUTH_HEADER
    if UPSTREAM_AUTH_MODE == "oauth":
        headers["Authorization"] = f"Bearer {await oauth.get_access_token()}"
    elif UPSTREAM_AUTH_MODE == "bearer" and UPSTREAM_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {UPSTREAM_BEARER_TOKEN}"
    elif UPSTREAM_API_KEY:
        headers[auth_header] = UPSTREAM_API_KEY
    return headers


def _check_bridge_auth(authorization: str | None) -> bool:
    if not BRIDGE_AUTH_TOKEN:
        return True
    return authorization == f"Bearer {BRIDGE_AUTH_TOKEN}"


def _rpc_result(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _parse_mcp_response(resp: httpx.Response) -> dict:
    content_type = resp.headers.get("content-type", "")
    text = resp.text
    if "text/event-stream" in content_type or text.lstrip().startswith("event:"):
        envelope: dict | None = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload and payload != "[DONE]":
                    try:
                        envelope = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
        if envelope is None:
            raise ValueError("No JSON-RPC data frame in upstream SSE response.")
        return envelope
    return resp.json()


async def _post_upstream(rpc: dict) -> httpx.Response:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **(await _upstream_auth_headers()),
    }

    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        return await client.post(UPSTREAM_MCP_ENDPOINT, headers=headers, json=rpc)


async def _forward_upstream(rpc: dict) -> dict:
    if not UPSTREAM_MCP_ENDPOINT:
        raise RuntimeError("UPSTREAM_MCP_ENDPOINT is not configured.")

    resp = await _post_upstream(rpc)
    if resp.status_code == 401 and UPSTREAM_AUTH_MODE == "oauth":
        # Access token may have been revoked before expiry: refresh once and retry.
        oauth.invalidate()
        resp = await _post_upstream(rpc)
    resp.raise_for_status()
    return _parse_mcp_response(resp)


async def _forward_tool_call(upstream_name: str, arguments: dict, req_id) -> dict:
    return await _forward_upstream({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": upstream_name, "arguments": arguments},
    })


def _strip_orchestrator_only(arguments: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    args = dict(arguments)
    injected = {k: args.pop(k) for k in list(args) if k in ORCHESTRATOR_INJECTED_KEYS}
    return args, injected


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/docs/{competency_id}")
async def docs(competency_id: str) -> Response:
    if competency_id != COMPETENCY_ID:
        return PlainTextResponse("Unknown competency id.", status_code=404)
    if not _DOC_PATH.exists():
        return PlainTextResponse("Documentation not found.", status_code=404)
    return PlainTextResponse(_DOC_PATH.read_text(encoding="utf-8"), media_type="text/markdown")


@app.get("/icon")
async def icon() -> Response:
    if not _ICON_PATH.exists():
        return PlainTextResponse("Icon not found.", status_code=404)
    return Response(content=_ICON_PATH.read_bytes(), media_type="image/png")


@app.get("/files/{token}")
async def files(token: str) -> Response:
    # The unguessable single-use token IS the authorization for this route:
    # it is minted by a tier-2 handler via handoff.publish_file() and expires
    # after a short TTL, so no bearer check applies here.
    entry = await handoff.store.take(token)
    if entry is None:
        return PlainTextResponse("Unknown or expired file token.", status_code=404)
    disposition = {"Content-Disposition": f'attachment; filename="{entry.file_name}"'}
    if entry.data is not None:
        return Response(
            content=entry.data,
            media_type=entry.media_type,
            headers=disposition,
        )
    if entry.path is not None and entry.path.is_file():
        # Stream from disk instead of reading the whole file into memory: a
        # bridge container is sized for its own work, not for holding a
        # multi-hundred-megabyte transfer in RAM while it is being served.
        return FileResponse(
            entry.path,
            media_type=entry.media_type,
            filename=entry.file_name,
            headers=disposition,
        )
    return PlainTextResponse("Published file is gone.", status_code=410)


@app.post("/mcp")
async def mcp(request: Request) -> Response:
    if not _check_bridge_auth(request.headers.get("authorization")):
        return JSONResponse({"error": "Unauthorized."}, status_code=401)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)

    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}

    if req_id is None and isinstance(method, str) and method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "initialize":
        return JSONResponse(_rpc_result(req_id, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }))

    if method == "ping":
        return JSONResponse(_rpc_result(req_id, {}))

    if method == "tools/list":
        try:
            upstream = await _forward_upstream(
                {"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": params}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("tools/list upstream failure: %s", exc)
            return JSONResponse(
                _rpc_error(req_id, -32603, f"Upstream MCP unavailable: {exc}")
            )

        if "result" not in upstream:
            return JSONResponse(upstream)

        upstream_tools = upstream["result"].get("tools") or []
        if MANIFEST.tools:
            tools = registry.transform_tools_list(upstream_tools, get_handler_schemas())
            return JSONResponse(_rpc_result(req_id, {"tools": tools}))

        return JSONResponse(_rpc_result(req_id, upstream["result"]))

    if method == "tools/call":
        name = params.get("name")
        arguments = dict(params.get("arguments") or {})

        if not name:
            return JSONResponse(_rpc_error(req_id, -32602, "Missing required field 'name'"))

        entry = MANIFEST.tool_by_name().get(name)

        try:
            if entry and entry.is_tier2:
                handler = get_handler(entry.handler or "")
                if handler is None:
                    return JSONResponse(_rpc_error(
                        req_id, -32603, f"Handler not registered: {entry.handler}"
                    ))
                ctx = HandlerContext(
                    tool_name=name,
                    entry=entry,
                    competency_id=COMPETENCY_ID,
                    forward_upstream=_forward_tool_call,
                    req_id=req_id,
                )
                result = await handler(ctx, arguments)
                return JSONResponse(_rpc_result(req_id, result))

            if entry:
                upstream_name, upstream_args, injected = registry.resolve_call(name, arguments)
            else:
                upstream_name = name
                upstream_args, injected = _strip_orchestrator_only(arguments)

            actor = injected.get("user_email") or "unknown"
            logger.info("tools/call tool=%s upstream=%s actor=%s", name, upstream_name, actor)

            upstream = await _forward_tool_call(upstream_name, upstream_args, req_id)

        except KeyError as exc:
            return JSONResponse(_rpc_error(req_id, -32602, str(exc)))
        except ValueError as exc:
            return JSONResponse(_rpc_error(req_id, -32603, str(exc)))
        except httpx.HTTPStatusError as exc:
            logger.error("tools/call upstream HTTP %s", exc.response.status_code)
            return JSONResponse(_rpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps({
                    "error": f"Upstream returned HTTP {exc.response.status_code}.",
                })}],
                "isError": True,
            }))
        except Exception as exc:  # noqa: BLE001
            logger.error("tools/call failure: %s", exc)
            return JSONResponse(_rpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps({
                    "error": f"Bridge error: {exc}",
                })}],
                "isError": True,
            }))

        if "result" in upstream:
            return JSONResponse(_rpc_result(req_id, upstream["result"]))
        if "error" in upstream:
            return JSONResponse(_rpc_error(
                req_id,
                upstream["error"].get("code", -32603),
                upstream["error"].get("message", "Upstream error"),
            ))
        return JSONResponse(upstream)

    return JSONResponse(_rpc_error(req_id, -32601, f"Method not found: {method}"))
