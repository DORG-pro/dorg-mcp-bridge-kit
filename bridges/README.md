# Bridge workspaces

Each Dorg MCP competency bridge lives in its own folder:

```
bridges/<name>/
  project.config.json       # source config (edit)
  competency.manifest.json  # generated (commit)
  DOCUMENTATION.md          # user docs for University
  competency_handlers.py    # optional tier-2 handlers
  .build/                   # generated Docker context (gitignored)
```

## Commands

```bash
# New bridge
python -m cli init-bridge my-service
python -m cli init-bridge my-proxy --template passthrough

# Generate manifest (requires reachable upstream MCP)
python -m cli generate-manifest --bridge my-service

# Package + Docker
python -m cli package --bridge my-service
docker build -t yourregistry.azurecr.io/my-service:1.0.0 bridges/my-service/.build
```

PowerShell helpers: `scripts/init-bridge.ps1`, `scripts/package-bridge.ps1`, `scripts/build-bridge.ps1`.

## Folders

| Path | Purpose |
|---|---|
| `_template/` | Scaffold copied by `init-bridge` — do not deploy |
| `example/` | Reference workspace with virtual tools + tier-2 handler |

Upgrades to the shared runtime (`bridge/`) are picked up on the next `package` — no per-bridge fork of `server.py` required.
