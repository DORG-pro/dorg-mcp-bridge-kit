"""Protocol tests for manifest-driven MCP bridge (offline)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_BRIDGE = Path(__file__).resolve().parents[1] / "bridge"
if str(_BRIDGE) not in sys.path:
    sys.path.insert(0, str(_BRIDGE))


@pytest.fixture()
def bridge_client(monkeypatch, tmp_path):
    manifest = {
        "competency_id": "test.bridge",
        "tools": [
            {
                "tool_name": "list_open",
                "upstream_tool_name": "get_items",
                "forced_arguments": {"status": "open"},
                "tool_description": "Open items",
            },
            {
                "tool_name": "assign_to_me",
                "handler": "assign_to_me",
                "tool_description": "Assign",
                "input_schema": {
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                },
            },
        ],
    }
    manifest_path = tmp_path / "competency.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    import manifest as manifest_mod
    import server
    from handlers.registry import HANDLERS, register
    from manifest import ManifestConfig, ManifestToolRegistry

    cfg = ManifestConfig.from_dict(manifest)
    monkeypatch.setattr(manifest_mod, "ManifestConfig", ManifestConfig)
    monkeypatch.setenv("MANIFEST_PATH", str(manifest_path))
    monkeypatch.setattr(server, "MANIFEST", cfg)
    monkeypatch.setattr(server, "COMPETENCY_ID", "test.bridge")
    monkeypatch.setattr(server, "registry", ManifestToolRegistry(cfg))
    monkeypatch.setattr(server, "UPSTREAM_MCP_ENDPOINT", "http://upstream.test/mcp")

    @register("assign_to_me")
    async def assign_to_me(ctx, arguments):
        email = arguments.get("user_email")
        item_id = arguments.get("item_id")
        result = await ctx.forward("update_item", {"item_id": item_id, "owner": email})
        return result.get("result", result)

    HANDLERS["assign_to_me"] = assign_to_me

    yield TestClient(server.app), server


def test_health(bridge_client):
    client, _ = bridge_client
    assert client.get("/health").json() == {"status": "ok"}


def test_tier1_tools_call(bridge_client, monkeypatch):
    client, server = bridge_client
    captured = {}

    async def fake_forward(name, args, req_id):
        captured["name"] = name
        captured["args"] = args
        return {"result": {"content": [{"type": "text", "text": "{}"}], "isError": False}}

    monkeypatch.setattr(server, "_forward_tool_call", fake_forward)

    r = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "list_open", "arguments": {"user_email": "a@x.com"}},
    })
    assert r.json()["result"]["isError"] is False
    assert captured["name"] == "get_items"
    assert captured["args"] == {"status": "open"}


def test_tier2_handler(bridge_client, monkeypatch):
    client, server = bridge_client
    captured = {}

    async def fake_forward(name, args, req_id):
        captured["name"] = name
        captured["args"] = args
        return {"result": {"content": [{"type": "text", "text": "{}"}], "isError": False}}

    monkeypatch.setattr(server, "_forward_tool_call", fake_forward)

    r = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "assign_to_me", "arguments": {
            "item_id": "42", "user_email": "me@example.com",
        }},
    })
    assert r.json()["result"]["isError"] is False
    assert captured["name"] == "update_item"
    assert captured["args"]["owner"] == "me@example.com"


def test_tools_list_from_manifest(bridge_client, monkeypatch):
    client, server = bridge_client

    async def fake_forward(rpc):
        return {"result": {"tools": [{
            "name": "get_items",
            "description": "List",
            "inputSchema": {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
            },
        }]}}

    monkeypatch.setattr(server, "_forward_upstream", fake_forward)

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"list_open", "assign_to_me"}
    open_tool = next(t for t in tools if t["name"] == "list_open")
    assert "status" not in open_tool["inputSchema"].get("properties", {})
