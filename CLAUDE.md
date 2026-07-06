# CLAUDE.md — Dorg MCP Bridge Kit

Guida per agenti AI che creano Competenze Dorg wrappando un MCP remoto.

Documentazione Dorg: https://docs.dorg.pro/create-competency

## Principio

**Un bridge statico, molte competenze.** L'immagine Docker `dorg-mcp-bridge` è generica. Ogni competenza differisce per:

1. `competency.manifest.json` — identity, governance, routing tier-1
2. ENV (`UPSTREAM_MCP_ENDPOINT`, credenziali)
3. `competency_handlers.py` — solo se servono tool tier-2

## Due tier

### Tier 1 — manifest only (preferire sempre)

Nessun codice Python. Campi nel manifest per tool entry:

| Campo | Uso |
|---|---|
| `tool_name` | Nome esposto al dorg (`tools/list`, autorizzazione orchestrator) |
| `upstream_tool_name` | Tool reale upstream; omesso = passthrough 1:1 |
| `forced_arguments` | `{ "status": "open" }` — mergiati su ogni call, nascosti dallo schema |
| `forced_arguments_from_env` | `{ "customerId": "DEFAULT_CUSTOMER_ID" }` — valore da env container |
| `upstream_injections` | `[{ "injected_key": "user_email", "upstream_key": "assignee" }]` — disaccoppia orchestrator/upstream |
| `argument_aliases` | `{ "customer_email": "customerId" }` — rename param nello schema |
| `injected_params` | Vocabolario chiuso Dorg — orchestrator → bridge (audit / map) |

**Quando usare tier 1:** rename tool, filtri fissi, valori da env, rename parametri, map injected→upstream semplice.

### Tier 2 — handler Python

Aggiungi `"handler": "nome_handler"` nel manifest. Implementa in `bridge/competency_handlers.py`:

```python
from handlers.registry import register

@register("assign_ticket_to_me", input_schema={...})
async def assign_ticket_to_me(ctx, arguments):
    email = arguments.get("user_email")
    result = await ctx.forward("update_ticket", {
        "ticket_id": arguments["ticket_id"],
        "assignee": email,
    })
    return result.get("result", result)
```

**Quando usare tier 2:** multi-call, validazione complessa, trasformazione risposta, tool sintetico senza upstream, logica condizionale.

**Regola:** prova tier 1 prima; passa a tier 2 solo se il manifest non basta.

## Workflow agente

```
1. Raccogli: endpoint MCP, credenziali, competency metadata, tool mappings
2. Crea project.config.json (examples/project.config.example.json)
3. python -m cli generate-manifest --project project.config.json
4. Se handler richiesti: copia/adatta examples/competency_handlers.example.py
5. Scrivi DOCUMENTATION.md
6. .\scripts\package-bridge.ps1 -Project project.config.json
7. docker build -t <registry>/dorg-mcp-bridge:<tag> bridge/
```

## project.config.json

Mappings in `tool_mappings` (solo per generazione CLI → finiscono nel manifest):

```json
{
  "tool_mappings": {
    "passthrough_unmapped": false,
    "exclude_upstream": ["admin_delete_all"],
    "mappings": [
      {
        "tool_name": "list_open",
        "upstream_tool_name": "get_tickets",
        "forced_arguments": { "status": "open" }
      },
      {
        "tool_name": "assign_to_me",
        "handler": "assign_ticket_to_me"
      }
    ]
  }
}
```

`passthrough_unmapped: true` + `mappings: []` = tutti i tool upstream nel manifest (1:1).

## Injected params — non confondere

| Meccanismo | Chi inietta | Dove si dichiara |
|---|---|---|
| `injected_params` | Dorg orchestrator | manifest (consenso utente) |
| `forced_arguments` | Bridge da manifest | manifest |
| `forced_arguments_from_env` | Bridge da ENV | manifest + `competency_env` |
| `upstream_injections` | Bridge: `injected_key` → `upstream_key` | manifest |
| `injected_params` | Dichiarazione consenso orchestrator | manifest |

### `upstream_injections`

Usa sempre la forma esplicita (non confondere le due chiavi):

```json
"upstream_injections": [
  { "injected_key": "user_email", "upstream_key": "assignee" }
],
"injected_params": [{ "key": "user_email" }]
```

- `injected_params` → cosa il **cliente approva** e l'orchestrator inietta
- `upstream_injections` → come il **bridge rinomina** verso il MCP remoto

Tier-2: `ctx.map_injected_to_upstream(arguments)` applica le stesse regole del manifest.

Legacy deprecato: `inject_from_orchestrator: { "assignee": "user_email" }`.

Elenco chiavi orchestrator: `python -m cli list-injected-params`

## Ibrido tier-1 + tier-2

Stesso manifest, stesso container:

```json
"tools": [
  { "tool_name": "list_open", "upstream_tool_name": "get_tickets", "forced_arguments": {"status": "open"} },
  { "tool_name": "get_tickets", "tool_description": "..." },
  { "tool_name": "assign_to_me", "handler": "assign_ticket_to_me" }
]
```

Il router in `server.py`:
- tool con `handler` → `competency_handlers.py`
- altrimenti → `manifest.py` tier-1
- tool non in manifest → non esposto (se manifest.tools non vuoto)

## ENV bridge

| Variabile | Scopo |
|---|---|
| `UPSTREAM_MCP_ENDPOINT` | URL MCP remoto |
| `UPSTREAM_API_KEY` | Credenziale (header configurabile) |
| `UPSTREAM_BEARER_TOKEN` | Alternativa bearer |
| `UPSTREAM_AUTH_MODE` | `api_key` o `bearer` |
| `BRIDGE_AUTH_TOKEN` | Bearer orchestrator → bridge |
| `MANIFEST_PATH` | Default `competency.manifest.json` |
| `HANDLERS_MODULE` | Default `competency_handlers` |

## Validazione

```bash
python -m cli validate-manifest --manifest competency.manifest.json
pytest tests/ -v
```

## Output University

- `competency.manifest.json`
- Docker image (generica o con layer handler)
- `DOCUMENTATION.md`

Non committare segreti. Mai valori env nel manifest.

## File da toccare per competenza

| File | Tier |
|---|---|
| `project.config.json` | Setup generazione |
| `competency.manifest.json` | 1 (+ dichiarazione handler) |
| `competency_handlers.py` | 2 solo se necessario |
| `DOCUMENTATION.md` | Sempre |

Non modificare `server.py` / `manifest.py` per singole competenze.
