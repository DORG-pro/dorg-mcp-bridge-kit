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
- `upstream_injections` — map esplicito `injected_key` → `upstream_key` (disaccoppiato da `injected_params`)
- `argument_aliases` — rename parametri nello schema

### Tier 2 — codice custom

Tool con `"handler": "nome"` nel manifest → funzione in `competency_handlers.py`.

Vedi `bridges/example/competency_handlers.py` (`assign_ticket_to_me`).

## Quick start

```powershell
cd e:\Dev\DORG\dorg-mcp-bridge-kit

# 1. Crea workspace bridge
python -m cli init-bridge my-service
# oppure passthrough 1:1:
# python -m cli init-bridge my-proxy --template passthrough

# 2. Configura bridges/my-service/project.config.json (endpoint, competency, mappings)

# 3. Genera manifest (richiede MCP upstream raggiungibile)
python -m cli generate-manifest --bridge my-service

# 4. (Opzionale) Tier-2: edita bridges/my-service/competency_handlers.py

# 5. Package + Docker
python -m cli package --bridge my-service
# SOURCE_DATE_EPOCH forza il timestamp `created` in UTC: Azure Container Apps
# rifiuta immagini con offset locale (es. +02:00, Docker 29+/podman).
SOURCE_DATE_EPOCH=$(date +%s) docker build -t yourregistry.azurecr.io/my-service:1.0.0 bridges/my-service/.build
```

Oppure: `.\scripts\init-bridge.ps1 -Name my-service` → `.\scripts\build-bridge.ps1 -Bridge my-service -Tag yourregistry.azurecr.io/my-service:1.0.0`

## Struttura monorepo

```
dorg-mcp-bridge-kit/
├── bridge/                 # Runtime condiviso (upgrade centralizzato)
├── bridges/
│   ├── _template/          # Scaffold init-bridge
│   ├── example/            # Esempio di riferimento
│   └── <nome>/             # Un bridge = una competenza
│       ├── project.config.json
│       ├── competency.manifest.json
│       ├── DOCUMENTATION.md
│       ├── competency_handlers.py   # opzionale
│       └── .build/                  # generato da package (gitignored)
├── cli/
└── scripts/
```

Ogni competenza vive in `bridges/<nome>/`. Il comando `package` copia il runtime aggiornato da `bridge/` in `bridges/<nome>/.build/` — nessun fork di `server.py` per bridge.

## `project.config.json`

File in **`bridges/<nome>/project.config.json`**, usato dal CLI `generate-manifest`. Non va nel container; produce `competency.manifest.json` nella stessa cartella.

Template: `bridges/_template/project.config.json` (virtual tools) o `project.config.passthrough.json` (1:1).

### Sezioni principali

| Sezione | Obbligatoria | Scopo |
|---|---|---|
| `competency` | sì | Metadata competenza → header del manifest |
| `upstream` | sì* | Endpoint MCP per `tools/list` in fase di generazione |
| `tool_mappings` | no | Regole di mapping tool → voci `tools[]` nel manifest |
| `tool_defaults` | no | Valori di default per tutti i tool generati |
| `bridge` | no | Metadata bridge copiati in `manifest.bridge` |
| `manifest_output` | no | Path output (default `competency.manifest.json`) |

\*Richiesto in config o via `--endpoint` sul CLI.

### `competency`

Identità e presentazione della competenza. Campi comuni:

| Campo | Obbligatorio | Descrizione |
|---|---|---|
| `id` | sì | `competency_id` (es. `acme.example.bridge`) |
| `title` | sì | Nome in University |
| `description` | sì | Descrizione business |
| `version` | no | Versione semver (default `1.0.0`) |
| `vcpu` / `ram` | no | Risorse container (default `0.5` / `1.0`) |
| `health_path` / `mcp_path` | no | Path bridge (default `/health`, `/mcp`) |
| `docker_image` | no | Riferimento immagine in manifest |
| `supported_dorg_versions` | no | Compatibilità runtime (default `["3.*"]`) |
| `intended_usage` | no | Casi d'uso per University |
| `documentation` | no | Path docs (default `/docs/<id>`) |
| `env` | no | Variabili che l'operatore configura in Console → `competency_env` nel manifest |

Ogni entry in `competency.env`:

| Campo | Descrizione |
|---|---|
| `key` | Nome variabile ENV nel container |
| `label` | Etichetta UI Console |
| `description` | Testo di aiuto |
| `required` | Obbligatoria all'install (default `true`) |
| `secret` | Salvata in Key Vault se `true` |

**Non inserire mai valori segreti** in `project.config.json` — solo dichiarazioni.

### `upstream`

Usato dal CLI per chiamare `tools/list` sull'MCP remoto durante la generazione.

| Campo | Descrizione |
|---|---|
| `endpoint` | URL MCP Streamable-HTTP |
| `auth_header_name` | Header credenziale (default `X-Api-Key`) |
| `api_key` | Solo per generazione locale; preferire `--api-key` o variabile d'ambiente |

### `tool_defaults`

Default applicati a ogni tool nel manifest generato, se non overridden nel mapping:

```json
"tool_defaults": {
  "allowed_groups": ["all_users"],
  "injected_params": [{ "key": "user_email" }],
  "retention": {
    "message_retention_hours": 24,
    "log_retention_hours": 168
  }
}
```

### `tool_mappings`

Controlla quali tool upstream finiscono nel manifest e come vengono esposti.

| Campo | Default | Descrizione |
|---|---|---|
| `passthrough_unmapped` | `true` | Se `true`, include tutti i tool upstream non mappati/esclusi |
| `exclude_upstream` | `[]` | Nomi tool upstream da non esporre |
| `mappings` | `[]` | Lista mapping espliciti (virtual tools e/o handler) |

Ogni elemento di `mappings`:

| Campo | Tier | Descrizione |
|---|---|---|
| `tool_name` | — | Nome esposto al dorg (`tool_name` nel manifest) |
| `upstream_tool_name` | 1 | Tool reale upstream; omesso = uguale a `tool_name` |
| `tool_description` | — | Descrizione nel manifest e in `tools/list` |
| `tool_name_user` / `tool_description_user` | — | Etichette UI University |
| `forced_arguments` | 1 | Argomenti fissi verso upstream |
| `forced_arguments_from_env` | 1 | Map `arg → env_var` (env dichiarata in `competency.env`) |
| `upstream_injections` | 1 | Map orchestrator → upstream con chiavi distinte (vedi sotto) |
| `argument_aliases` | 1 | Rename parametri LLM nello schema (es. `customer_email` → `customerId`) |
| `handler` | 2 | Nome handler in `competency_handlers.py` |
| `input_schema` | 2 | Schema MCP per tool con handler (se non definito nel codice) |
| `allowed_groups` | — | Override di `tool_defaults` |
| `injected_params` | — | Override di `tool_defaults` |
| `retention` | — | Override policy logging |
| `concurrency` | — | Limite chiamate concorrenti |

#### `upstream_injections` — disaccoppiamento chiavi

`injected_params` nel manifest dichiara cosa il **Dorg orchestrator** inietta (`user_email`, …).
L'**upstream MCP** può usare nomi diversi (`assignee`, `reporterName`, …).

```json
"upstream_injections": [
  { "injected_key": "user_email", "upstream_key": "assignee" },
  { "injected_key": "user_display_name", "upstream_key": "reporterName" }
],
"injected_params": [{ "key": "user_email" }, { "key": "user_display_name" }]
```

| Campo | Ruolo |
|---|---|
| `injected_key` | Chiave ricevuta dal bridge negli `arguments` (vocabolario orchestrator) |
| `upstream_key` | Chiave inviata al MCP remoto su `tools/call` |

Entrambe le chiavi sono **nascoste** dall'`inputSchema` esposto al dorg. Il formato legacy `inject_from_orchestrator: { "assignee": "user_email" }` resta supportato ma deprecato.

### `bridge`

Metadata opzionali copiati nel manifest sotto `bridge` (es. header auth upstream):

```json
"bridge": {
  "upstream_auth_header": "X-Api-Key"
}
```

### Flusso config → manifest

```
project.config.json  (in bridges/<name>/)
       │
       ├─ competency.*     → competency_id, title, env, vcpu, …
       ├─ tool_mappings    → tools[] (con upstream_tool_name, forced_*, handler)
       └─ tool_defaults    → allowed_groups, injected_params, retention
       │
       ▼  generate-manifest --bridge <name>
bridges/<name>/competency.manifest.json  → package → .build/ → Docker
```

Template: `bridges/_template/`. Esempio completo: `bridges/example/`.

## CLI

```bash
python -m cli init-bridge <name> [--template passthrough]
python -m cli generate-manifest --bridge <name>
python -m cli validate-manifest --bridge <name>
python -m cli package --bridge <name>
python -m cli list-bridges
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
├── bridge/                    # Runtime Docker (immagine generica, upgrade qui)
├── bridges/<name>/            # Una competenza per cartella
├── cli/                       # init-bridge, generate-manifest, package
├── scripts/                   # init-bridge.ps1, package-bridge.ps1, build-bridge.ps1
├── CLAUDE.md
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
