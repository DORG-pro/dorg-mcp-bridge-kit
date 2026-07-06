# Dorg MCP Bridge Kit

Skeleton e strumenti per pubblicare **Competenze Dorg** che wrappano un MCP remoto tramite un **bridge HTTP statico**.

📖 [Documentazione Dorg — Create a Competency](https://docs.dorg.pro/create-competency)

## Architettura

Un'**unica immagine Docker generica** (`dorg-mcp-bridge`) legge a runtime:

| Input | Ruolo |
|---|---|
| `UPSTREAM_MCP_ENDPOINT` (+ API key / bearer via ENV) | MCP remoto |
| `competency.manifest.json` | Tool esposti, governance, **tier-1 mapping** |
| `competency_handlers.py` (opzionale) | **Tier-2** handler Python custom |

### Tier 1 — dichiarativo (manifest, zero codice)

- `upstream_tool_name` — rename tool
- `forced_arguments` / `forced_arguments_from_env` — injection verso upstream
- `inject_from_orchestrator` — map `user_email` → campo upstream
- `argument_aliases` — rename parametri nello schema

### Tier 2 — codice custom

Tool con `"handler": "nome"` nel manifest → funzione in `competency_handlers.py`.

Vedi `examples/competency_handlers.example.py` (`assign_ticket_to_me`).

## Quick start

```powershell
cd e:\Dev\DORG\dorg-mcp-bridge-kit

# 1. Configura progetto
cp examples\project.config.example.json project.config.json
# oppure passthrough 1:1:
# cp examples\project.config.passthrough.json project.config.json

# 2. Genera manifest (richiede MCP upstream raggiungibile)
.venv\Scripts\python -m cli generate-manifest --project project.config.json

# 3. (Opzionale) Tier-2 handlers
cp examples\competency_handlers.example.py bridge\competency_handlers.py

# 4. Documentazione
cp bridge\DOCUMENTATION.md.template DOCUMENTATION.md

# 5. Package + Docker
.\scripts\package-bridge.ps1 -Project project.config.json
docker build -t yourregistry.azurecr.io/dorg-mcp-bridge:1.0.0 bridge/
```

## CLI

```bash
python -m cli generate-manifest --project project.config.json
python -m cli validate-manifest --manifest competency.manifest.json
python -m cli list-injected-params
```

## Esempio manifest (tier 1 + tier 2)

```json
{
  "tools": [
    {
      "tool_name": "list_open_tickets",
      "upstream_tool_name": "get_tickets",
      "forced_arguments": { "status": "open" },
      "tool_description": "List open tickets only.",
      "allowed_groups": ["all_users"],
      "injected_params": [{ "key": "user_email" }]
    },
    {
      "tool_name": "assign_ticket_to_me",
      "handler": "assign_ticket_to_me",
      "tool_description": "Assign ticket to current colleague.",
      "injected_params": [{ "key": "user_email" }]
    }
  ]
}
```

## Struttura repo

```
dorg-mcp-bridge-kit/
├── bridge/                    # Runtime Docker (immagine generica)
│   ├── server.py              # Router tier-1 / tier-2
│   ├── manifest.py            # Lettura manifest + transform tier-1
│   ├── handlers/              # Registry handler
│   └── competency_handlers.py # Handler custom per competenza
├── cli/                       # generate-manifest
├── examples/
├── scripts/package-bridge.ps1
├── CLAUDE.md                  # Guida agente AI
└── tests/
```

## Test

```bash
pytest tests/ -v
```

## Pubblicazione University

1. `competency.manifest.json` (generato)
2. Immagine Docker (`docker save` → `.tar.gz`)
3. `DOCUMENTATION.md`

Per competenze **solo tier-1**: stessa immagine base `dorg-mcp-bridge`, manifest diverso.
Per **tier-2**: aggiungi `competency_handlers.py` nel layer Docker (`package-bridge.ps1`).
