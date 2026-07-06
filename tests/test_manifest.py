"""Tests for manifest-driven tier-1 tool routing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BRIDGE = Path(__file__).resolve().parents[1] / "bridge"
if str(_BRIDGE) not in sys.path:
    sys.path.insert(0, str(_BRIDGE))

from manifest import ManifestConfig, ManifestToolRegistry  # noqa: E402


UPSTREAM_TOOLS = [
    {
        "name": "get_tickets",
        "description": "List tickets",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "user_email": {"type": "string"},
            },
            "required": ["status"],
        },
    },
    {
        "name": "create_ticket",
        "description": "Create ticket",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "customerId": {"type": "string"},
            },
            "required": ["title"],
        },
    },
]


def _manifest(data: dict) -> ManifestConfig:
    return ManifestConfig.from_dict(data)


def test_manifest_only_lists_declared_tools():
    manifest = _manifest({
        "tools": [
            {"tool_name": "list_open", "upstream_tool_name": "get_tickets",
             "forced_arguments": {"status": "open"}, "tool_description": "Open only"},
        ],
    })
    registry = ManifestToolRegistry(manifest)
    tools = registry.transform_tools_list(UPSTREAM_TOOLS)
    assert len(tools) == 1
    assert tools[0]["name"] == "list_open"


def test_virtual_tool_forces_and_strips():
    manifest = _manifest({
        "tools": [{
            "tool_name": "list_open",
            "upstream_tool_name": "get_tickets",
            "forced_arguments": {"status": "open"},
            "tool_description": "x",
        }],
    })
    registry = ManifestToolRegistry(manifest)
    registry.transform_tools_list(UPSTREAM_TOOLS)
    upstream, args, injected = registry.resolve_call(
        "list_open", {"page": 1, "user_email": "a@example.com"},
    )
    assert upstream == "get_tickets"
    assert args == {"page": 1, "status": "open"}
    assert injected["user_email"] == "a@example.com"


def test_inject_from_orchestrator():
    manifest = _manifest({
        "tools": [{
            "tool_name": "create_ticket",
            "inject_from_orchestrator": {"assignee": "user_email"},
            "tool_description": "x",
        }],
    })
    registry = ManifestToolRegistry(manifest)
    registry.transform_tools_list(UPSTREAM_TOOLS)
    _, args, _ = registry.resolve_call(
        "create_ticket", {"title": "Hi", "user_email": "me@example.com"},
    )
    assert args["assignee"] == "me@example.com"
    assert "user_email" not in args


def test_argument_aliases():
    manifest = _manifest({
        "tools": [{
            "tool_name": "create_ticket",
            "argument_aliases": {"customer_email": "customerId"},
            "tool_description": "x",
        }],
    })
    registry = ManifestToolRegistry(manifest)
    tools = registry.transform_tools_list(UPSTREAM_TOOLS)
    props = tools[0]["inputSchema"]["properties"]
    assert "customer_email" in props
    assert "customerId" not in props

    _, args, _ = registry.resolve_call(
        "create_ticket", {"title": "Hi", "customer_email": "c@x.com"},
    )
    assert args["customerId"] == "c@x.com"


def test_forced_arguments_from_env(monkeypatch):
    monkeypatch.setenv("DEFAULT_CUSTOMER_ID", "cust-42")
    manifest = _manifest({
        "tools": [{
            "tool_name": "create_ticket",
            "forced_arguments_from_env": {"customerId": "DEFAULT_CUSTOMER_ID"},
            "tool_description": "x",
        }],
    })
    registry = ManifestToolRegistry(manifest)
    registry.transform_tools_list(UPSTREAM_TOOLS)
    _, args, _ = registry.resolve_call("create_ticket", {"title": "Hi"})
    assert args["customerId"] == "cust-42"


def test_tier2_entry_uses_handler_schema():
    manifest = _manifest({
        "tools": [{
            "tool_name": "assign_ticket_to_me",
            "handler": "assign_ticket_to_me",
            "tool_description": "Assign",
            "input_schema": {
                "type": "object",
                "properties": {"ticket_id": {"type": "string"}},
                "required": ["ticket_id"],
            },
        }],
    })
    registry = ManifestToolRegistry(manifest)
    tools = registry.transform_tools_list(UPSTREAM_TOOLS)
    assert tools[0]["name"] == "assign_ticket_to_me"
    assert "ticket_id" in tools[0]["inputSchema"]["properties"]


def test_resolve_call_rejects_tier2():
    manifest = _manifest({
        "tools": [{"tool_name": "x", "handler": "h", "tool_description": "x"}],
    })
    registry = ManifestToolRegistry(manifest)
    with pytest.raises(ValueError, match="custom handler"):
        registry.resolve_call("x", {})
