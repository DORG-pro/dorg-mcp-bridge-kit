"""Offline tests for manifest CLI builders."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from cli.generate_manifest import build_manifest, build_manifest_tools  # noqa: E402


UPSTREAM = [
    {"name": "get_tickets", "description": "List", "inputSchema": {}},
    {"name": "admin_wipe", "description": "Wipe", "inputSchema": {}},
]


def test_build_manifest_tools_with_mappings():
    project = {
        "tool_defaults": {"allowed_groups": ["all_users"]},
        "tool_mappings": {
            "passthrough_unmapped": False,
            "exclude_upstream": ["admin_wipe"],
            "mappings": [{
                "tool_name": "list_open",
                "upstream_tool_name": "get_tickets",
                "forced_arguments": {"status": "open"},
            }],
        },
    }
    tools = build_manifest_tools(UPSTREAM, project)
    assert len(tools) == 1
    assert tools[0]["tool_name"] == "list_open"
    assert tools[0]["upstream_tool_name"] == "get_tickets"
    assert tools[0]["forced_arguments"] == {"status": "open"}


def test_build_manifest_writes_upstream_injections():
    project = {
        "tool_defaults": {},
        "tool_mappings": {
            "passthrough_unmapped": False,
            "mappings": [{
                "tool_name": "assign_to_me",
                "upstream_tool_name": "get_tickets",
                "upstream_injections": [
                    {"injected_key": "user_email", "upstream_key": "assignee"},
                ],
            }],
        },
    }
    tools = build_manifest_tools(UPSTREAM, project)
    assert tools[0]["upstream_injections"] == [
        {"injected_key": "user_email", "upstream_key": "assignee"},
    ]


def test_build_manifest_converts_legacy_inject_from_orchestrator():
    project = {
        "tool_defaults": {},
        "tool_mappings": {
            "passthrough_unmapped": False,
            "mappings": [{
                "tool_name": "assign_to_me",
                "inject_from_orchestrator": {"assignee": "user_email"},
            }],
        },
    }
    tools = build_manifest_tools(UPSTREAM, project)
    assert tools[0]["upstream_injections"] == [
        {"injected_key": "user_email", "upstream_key": "assignee"},
    ]


def test_build_manifest_includes_handler_field():
    project = {
        "competency": {
            "id": "acme.test",
            "title": "T",
            "description": "D",
            "version": "1.0.0",
        },
        "tool_mappings": {
            "passthrough_unmapped": False,
            "mappings": [{
                "tool_name": "assign_to_me",
                "handler": "assign_to_me",
                "upstream_tool_name": "update_ticket",
            }],
        },
    }
    manifest = build_manifest(project, UPSTREAM)
    assert manifest["competency_id"] == "acme.test"
    assert manifest["tools"][0]["handler"] == "assign_to_me"
