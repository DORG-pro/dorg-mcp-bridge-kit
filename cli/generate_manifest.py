"""Generate competency.manifest.json from upstream MCP tools and project mappings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

_BRIDGE_DIR = Path(__file__).resolve().parents[1] / "bridge"
if str(_BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_DIR))

from manifest import ORCHESTRATOR_INJECTED_KEYS, ManifestConfig  # noqa: E402


DEFAULT_RETENTION_READ = {"message_retention_hours": 24, "log_retention_hours": 168}
DEFAULT_INJECTED = [{"key": "user_email"}]


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


def fetch_upstream_tools(
    endpoint: str,
    api_key: str = "",
    auth_header: str = "X-Api-Key",
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if api_key:
        headers[auth_header] = api_key

    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(endpoint, headers=headers, json=rpc)
    resp.raise_for_status()
    envelope = _parse_mcp_response(resp)
    if "error" in envelope:
        raise RuntimeError(envelope["error"].get("message", "tools/list failed"))
    return envelope.get("result", {}).get("tools") or []


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _upstream_injections_for_manifest(mapping: dict[str, Any]) -> list[dict[str, str]] | None:
    if mapping.get("upstream_injections"):
        return [
            {
                "injected_key": item["injected_key"],
                "upstream_key": item["upstream_key"],
            }
            for item in mapping["upstream_injections"]
        ]
    legacy = mapping.get("inject_from_orchestrator")
    if legacy:
        return [
            {"injected_key": injected, "upstream_key": upstream}
            for upstream, injected in legacy.items()
        ]
    return None


def _mapping_dict_to_manifest_tool(
    mapping: dict[str, Any],
    upstream_tool: dict[str, Any] | None,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    tool_name = mapping.get("tool_name") or mapping.get("expose_as")
    upstream = mapping.get("upstream_tool_name") or mapping.get("upstream")
    if not tool_name:
        raise ValueError("Each mapping requires tool_name (or expose_as).")

    description = (
        mapping.get("tool_description")
        or mapping.get("description")
        or (upstream_tool or {}).get("description")
        or f"Tool {tool_name}"
    )

    entry: dict[str, Any] = {
        "tool_name": tool_name,
        "tool_description": description,
        "retention": mapping.get("retention") or defaults.get("retention", DEFAULT_RETENTION_READ),
        "allowed_groups": mapping.get("allowed_groups") or defaults.get("allowed_groups", ["all_users"]),
        "injected_params": mapping.get("injected_params") or defaults.get("injected_params", DEFAULT_INJECTED),
    }

    if upstream and upstream != tool_name:
        entry["upstream_tool_name"] = upstream
    if mapping.get("handler"):
        entry["handler"] = mapping["handler"]
    if mapping.get("forced_arguments"):
        entry["forced_arguments"] = mapping["forced_arguments"]
    if mapping.get("forced_arguments_from_env"):
        entry["forced_arguments_from_env"] = mapping["forced_arguments_from_env"]
    injections = _upstream_injections_for_manifest(mapping)
    if injections:
        entry["upstream_injections"] = injections
    if mapping.get("argument_aliases"):
        entry["argument_aliases"] = mapping["argument_aliases"]
    if mapping.get("input_schema"):
        entry["input_schema"] = mapping["input_schema"]
    if mapping.get("tool_name_user"):
        entry["tool_name_user"] = mapping["tool_name_user"]
    if mapping.get("tool_description_user") or mapping.get("description_user"):
        entry["tool_description_user"] = mapping.get("tool_description_user") or mapping.get("description_user")
    if mapping.get("concurrency"):
        entry["concurrency"] = mapping["concurrency"]

    return entry


def _passthrough_tool(
    upstream_tool: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    name = upstream_tool["name"]
    return {
        "tool_name": name,
        "tool_description": upstream_tool.get("description") or f"Tool {name}",
        "retention": defaults.get("retention", DEFAULT_RETENTION_READ),
        "allowed_groups": defaults.get("allowed_groups", ["all_users"]),
        "injected_params": defaults.get("injected_params", DEFAULT_INJECTED),
    }


def build_manifest_tools(
    upstream_tools: list[dict[str, Any]],
    project: dict[str, Any],
) -> list[dict[str, Any]]:
    defaults = project.get("tool_defaults") or {}
    mappings_cfg = project.get("tool_mappings") or {}
    explicit_mappings: list[dict[str, Any]] = list(mappings_cfg.get("mappings") or [])
    exclude = set(mappings_cfg.get("exclude_upstream") or [])
    passthrough = mappings_cfg.get("passthrough_unmapped", True)

    by_name = {t["name"]: t for t in upstream_tools if t.get("name")}
    mapped_upstream: set[str] = set()
    entries: list[dict[str, Any]] = []

    for mapping in explicit_mappings:
        upstream_name = mapping.get("upstream_tool_name") or mapping.get("upstream")
        upstream_tool = by_name.get(upstream_name) if upstream_name else None
        if upstream_name and upstream_tool is None and not mapping.get("handler"):
            print(
                f"warning: upstream tool '{upstream_name}' not found — skipping '{mapping.get('tool_name')}'",
                file=sys.stderr,
            )
            continue
        if upstream_name:
            mapped_upstream.add(upstream_name)
        entries.append(_mapping_dict_to_manifest_tool(mapping, upstream_tool, defaults))

    if passthrough:
        for name, tool in by_name.items():
            if name in exclude or name in mapped_upstream:
                continue
            entries.append(_passthrough_tool(tool, defaults))

    return entries


def build_manifest(
    project: dict[str, Any],
    upstream_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    competency = project["competency"]
    tools = build_manifest_tools(upstream_tools, project)

    manifest: dict[str, Any] = {
        "competency_id": competency["id"],
        "competency_title": competency["title"],
        "competency_description": competency["description"],
        "competency_version": competency.get("version", "1.0.0"),
        "competency_vcpu": competency.get("vcpu", 0.5),
        "competency_ram": competency.get("ram", 1.0),
        "competency_health_path": competency.get("health_path", "/health"),
        "competency_mcp_path": competency.get("mcp_path", "/mcp"),
        "supported_dorg_versions": competency.get("supported_dorg_versions", ["3.*"]),
        "intended_usage": competency.get("intended_usage", []),
        "tools": tools,
    }

    if competency.get("docker_image"):
        manifest["competency_docker_image"] = competency["docker_image"]

    env_entries = competency.get("env") or []
    if env_entries:
        manifest["competency_env"] = env_entries

    bridge_meta = project.get("bridge") or {}
    if bridge_meta:
        manifest["bridge"] = bridge_meta

    doc_path = competency.get("documentation")
    if doc_path:
        manifest["documentation"] = doc_path
    elif competency.get("id"):
        manifest["documentation"] = f"/docs/{competency['id']}"

    return manifest


def _load_project_config(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    if "competency" not in data:
        raise ValueError("Project config must contain a 'competency' object.")
    return data


def cmd_generate(args: argparse.Namespace) -> int:
    project_path = Path(args.project)
    project = _load_project_config(project_path)

    upstream = project.get("upstream") or {}
    endpoint = args.endpoint or upstream.get("endpoint")
    if not endpoint:
        print("error: upstream endpoint required (--endpoint or project.upstream.endpoint)", file=sys.stderr)
        return 1

    api_key = args.api_key or upstream.get("api_key") or ""
    auth_header = upstream.get("auth_header_name", "X-Api-Key")

    print(f"Fetching tools from {endpoint} ...", file=sys.stderr)
    upstream_tools = fetch_upstream_tools(endpoint, api_key, auth_header, args.timeout)
    print(f"Found {len(upstream_tools)} upstream tool(s).", file=sys.stderr)

    manifest = build_manifest(project, upstream_tools)
    output = Path(args.output or project.get("manifest_output", "competency.manifest.json"))
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output} ({len(manifest['tools'])} tool entries).", file=sys.stderr)
    return 0


def cmd_validate_manifest(args: argparse.Namespace) -> int:
    path = Path(args.manifest)
    cfg = ManifestConfig.from_dict(_load_json(path))
    print(f"OK: competency_id={cfg.competency_id}, tools={len(cfg.tools)}")
    for t in cfg.tools:
        tier = "tier-2" if t.is_tier2 else "tier-1"
        upstream = t.resolved_upstream_name()
        extra = f" handler={t.handler}" if t.handler else ""
        remap = f" -> {upstream}" if upstream != t.tool_name else ""
        print(f"  [{tier}] {t.tool_name}{remap}{extra}")
    return 0


def cmd_list_injected(_: argparse.Namespace) -> int:
    for key in sorted(ORCHESTRATOR_INJECTED_KEYS):
        print(key)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dorg-bridge",
        description="Generate and validate Dorg competency manifests for MCP bridges.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-manifest", help="Fetch upstream tools and write competency.manifest.json")
    gen.add_argument("--project", "-p", required=True, help="Path to project.config.json")
    gen.add_argument("--endpoint", "-e", help="Override upstream MCP endpoint URL")
    gen.add_argument("--api-key", help="Upstream API key for tools/list (optional)")
    gen.add_argument("--output", "-o", help="Output manifest path")
    gen.add_argument("--timeout", type=float, default=60.0)
    gen.set_defaults(func=cmd_generate)

    val = sub.add_parser("validate-manifest", help="Validate competency.manifest.json for bridge routing")
    val.add_argument("--manifest", "-m", required=True)
    val.set_defaults(func=cmd_validate_manifest)

    inj = sub.add_parser("list-injected-params", help="Print orchestrator injected param keys")
    inj.set_defaults(func=cmd_list_injected)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
