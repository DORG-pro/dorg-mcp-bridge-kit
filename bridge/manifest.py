"""Load competency.manifest.json and drive tier-1 declarative tool routing."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ORCHESTRATOR_INJECTED_KEYS = frozenset({
    "user_email",
    "user_azure_id",
    "user_display_name",
    "user_groups",
    "tenant_id",
    "dorg_azure_id",
    "dorg_name",
    "dorg_version",
    "env",
    "channel",
    "message_id",
    "conversation_id",
    "msgraph_token",
    "anthropic_api_key",
    "ai_foundry_endpoint",
    "ai_foundry_key",
    "openai_api_key",
})


@dataclass
class ManifestToolEntry:
    """One tool entry from competency.manifest.json."""

    tool_name: str
    upstream_tool_name: str | None = None
    handler: str | None = None
    tool_description: str = ""
    tool_name_user: str | None = None
    tool_description_user: str | None = None
    forced_arguments: dict[str, Any] = field(default_factory=dict)
    forced_arguments_from_env: dict[str, str] = field(default_factory=dict)
    inject_from_orchestrator: dict[str, str] = field(default_factory=dict)
    argument_aliases: dict[str, str] = field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    strip_orchestrator_injected: bool = True

    @classmethod
    def from_manifest_dict(cls, data: dict[str, Any]) -> ManifestToolEntry:
        tool_name = data["tool_name"]
        upstream = data.get("upstream_tool_name")
        resolved_upstream = upstream if upstream and upstream != tool_name else None
        return cls(
            tool_name=tool_name,
            upstream_tool_name=resolved_upstream,
            handler=data.get("handler"),
            tool_description=data.get("tool_description", ""),
            tool_name_user=data.get("tool_name_user"),
            tool_description_user=data.get("tool_description_user"),
            forced_arguments=dict(data.get("forced_arguments") or {}),
            forced_arguments_from_env=dict(data.get("forced_arguments_from_env") or {}),
            inject_from_orchestrator=dict(data.get("inject_from_orchestrator") or {}),
            argument_aliases=dict(data.get("argument_aliases") or {}),
            input_schema=data.get("input_schema"),
            strip_orchestrator_injected=data.get("strip_orchestrator_injected", True),
        )

    @property
    def is_tier2(self) -> bool:
        return bool(self.handler)

    def resolved_upstream_name(self) -> str:
        return self.upstream_tool_name or self.tool_name


@dataclass
class ManifestConfig:
    competency_id: str = "acme.mcp.bridge"
    competency_version: str = "1.0.0"
    tools: list[ManifestToolEntry] = field(default_factory=list)
    upstream_auth_header: str = "X-Api-Key"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManifestConfig:
        bridge = data.get("bridge") or {}
        return cls(
            competency_id=data.get("competency_id", "acme.mcp.bridge"),
            competency_version=data.get("competency_version", "1.0.0"),
            tools=[ManifestToolEntry.from_manifest_dict(t) for t in data.get("tools") or []],
            upstream_auth_header=bridge.get("upstream_auth_header", "X-Api-Key"),
        )

    @classmethod
    def load(cls, path: Path | str | None = None) -> ManifestConfig:
        manifest_path = Path(path or os.getenv("MANIFEST_PATH", "competency.manifest.json"))
        if not manifest_path.exists():
            return cls()
        return cls.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))

    def tool_by_name(self) -> dict[str, ManifestToolEntry]:
        return {t.tool_name: t for t in self.tools}


def _strip_schema_properties(schema: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    if not schema or not keys:
        return schema
    out = deepcopy(schema)
    props = out.get("properties")
    if isinstance(props, dict):
        for key in keys:
            props.pop(key, None)
    required = out.get("required")
    if isinstance(required, list):
        out["required"] = [r for r in required if r not in keys]
    return out


def _rename_schema_properties(
    schema: dict[str, Any],
    aliases: dict[str, str],
) -> dict[str, Any]:
    """Expose LLM-facing names (keys) mapped from upstream property names (values)."""
    if not schema or not aliases:
        return schema
    reverse = {upstream: llm for llm, upstream in aliases.items()}
    out = deepcopy(schema)
    props = out.get("properties")
    if isinstance(props, dict):
        renamed: dict[str, Any] = {}
        for key, val in props.items():
            renamed[reverse.get(key, key)] = val
        out["properties"] = renamed
    required = out.get("required")
    if isinstance(required, list):
        out["required"] = [reverse.get(r, r) for r in required]
    return out


class ManifestToolRegistry:
    """Tier-1 declarative transforms: manifest tools + upstream schemas."""

    def __init__(self, manifest: ManifestConfig) -> None:
        self.manifest = manifest
        self._upstream_tools: dict[str, dict[str, Any]] = {}

    def set_upstream_tools(self, tools: list[dict[str, Any]]) -> None:
        self._upstream_tools = {t["name"]: t for t in tools if t.get("name")}

    def _hidden_schema_keys(self, entry: ManifestToolEntry) -> set[str]:
        keys = set(entry.forced_arguments) | set(entry.forced_arguments_from_env)
        keys |= set(entry.inject_from_orchestrator.values())
        keys |= set(entry.argument_aliases.values())
        if entry.strip_orchestrator_injected:
            keys |= ORCHESTRATOR_INJECTED_KEYS
        return keys

    def transform_tools_list(
        self,
        upstream_tools: list[dict[str, Any]],
        handler_schemas: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        self.set_upstream_tools(upstream_tools)
        handler_schemas = handler_schemas or {}
        exposed: list[dict[str, Any]] = []

        for entry in self.manifest.tools:
            if entry.is_tier2:
                schema = (
                    entry.input_schema
                    or handler_schemas.get(entry.handler or "")
                    or {"type": "object", "properties": {}}
                )
                exposed.append({
                    "name": entry.tool_name,
                    "description": entry.tool_description,
                    "inputSchema": schema,
                })
                continue

            upstream = self._upstream_tools.get(entry.resolved_upstream_name())
            if upstream is None:
                continue

            schema = _rename_schema_properties(
                upstream.get("inputSchema") or {},
                entry.argument_aliases,
            )
            schema = _strip_schema_properties(schema, self._hidden_schema_keys(entry))

            exposed.append({
                "name": entry.tool_name,
                "description": entry.tool_description or upstream.get("description", ""),
                "inputSchema": schema,
            })

        return exposed

    def resolve_forced_arguments(self, entry: ManifestToolEntry) -> dict[str, Any]:
        forced = dict(entry.forced_arguments)
        for arg_key, env_var in entry.forced_arguments_from_env.items():
            forced[arg_key] = os.getenv(env_var, "")
        return forced

    def resolve_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        entry = self.manifest.tool_by_name().get(tool_name)
        if entry is None:
            raise KeyError(f"Unknown tool: {tool_name}")
        if entry.is_tier2:
            raise ValueError(f"Tool {tool_name} is handled by a custom handler.")

        args = dict(arguments)
        strip_keys = (
            ORCHESTRATOR_INJECTED_KEYS if entry.strip_orchestrator_injected else frozenset()
        )

        for target, source in entry.inject_from_orchestrator.items():
            if source in args:
                args[target] = args[source]

        audit_injected = {k: args.pop(k) for k in list(args) if k in strip_keys}

        reverse_aliases = {llm: upstream for llm, upstream in entry.argument_aliases.items()}
        upstream_args: dict[str, Any] = {}
        for key, value in args.items():
            upstream_args[reverse_aliases.get(key, key)] = value

        upstream_args.update(self.resolve_forced_arguments(entry))
        return entry.resolved_upstream_name(), upstream_args, audit_injected
