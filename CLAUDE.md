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

Aggiungi `"handler": "nome_handler"` nel manifest. Implementa in `bridges/<nome>/competency_handlers.py`:

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

## Domande da fare all'utente (nuovo bridge)

Quando l'utente chiede di **creare un nuovo bridge**, non iniziare a scrivere file finché non hai raccolto le informazioni sotto. Fai le domande in ordine; raggruppale in un unico messaggio se possibile. Se l'utente non sa rispondere, proponi un default e chiedi conferma.

### 1. Identità competenza (obbligatorio)

| Domanda | Perché | Default se assente |
|---|---|---|
| **`competency_id`** — quale id University? (es. `acme.crm.bridge`, solo alfanumerico e `-`) | Immutabile, usato in manifest e `/docs/<id>` | Derivare da org + servizio: `<org>.<servizio>.bridge` |
| **Titolo** — nome leggibile in University? | `competency_title` | Titolo del servizio upstream |
| **Descrizione** — cosa fa la competenza in 1–2 frasi? | `competency_description` | "Bridge verso \<servizio\> via MCP" |
| **Versione iniziale**? | `competency_version` | `1.0.0` |
| **Nome cartella bridge** in `bridges/<nome>/`? (lowercase, trattini) | Workspace repo | Slug dal `competency_id` senza punti |

### 2. MCP upstream (obbligatorio)

| Domanda | Perché |
|---|---|
| **URL endpoint** MCP Streamable-HTTP? (es. `https://api.example.com/mcp`) | `upstream.endpoint` + `UPSTREAM_MCP_ENDPOINT` in manifest env |
| **Autenticazione upstream** — API key (header?), Bearer, o nessuna? | `competency_env`, `bridge.upstream_auth_header`, `UPSTREAM_AUTH_MODE` |
| **Hai accesso ora** per eseguire `tools/list`? (endpoint raggiungibile + credenziali di test) | Necessario per `generate-manifest`; senza, chiedi export manuale dei tool o endpoint di staging |

Non chiedere mai di committare segreti: le credenziali restano in env locale / `--api-key` solo per la generazione.

### 3. Strategia tool (obbligatorio — scegli una modalità)

Chiedi esplicitamente:

> Vuoi esporre **tutti** i tool upstream (passthrough 1:1), **solo un sottoinsieme**, o **virtual tool** con rename/filtri?

| Risposta utente | Config |
|---|---|
| Tutti i tool, stessi nomi | `init-bridge --template passthrough`, `passthrough_unmapped: true`, `mappings: []` |
| Tutti tranne alcuni pericolosi | `passthrough_unmapped: true` + `exclude_upstream: [...]` |
| Solo tool selezionati / rename / filtri fissi | `passthrough_unmapped: false` + `mappings: [...]` |
| Mix (alcuni virtual + resto passthrough) | `passthrough_unmapped: true` + `mappings` per i virtual |

Se non è passthrough puro, per **ogni tool esposto** chiedi (o deduci da `tools/list`):

| Domanda | Campo manifest |
|---|---|
| Nome esposto al dorg (`tool_name`)? | Può differire da upstream |
| Tool upstream reale (`upstream_tool_name`)? | Se diverso da `tool_name` |
| Descrizione business per il dorg? | `tool_description` |
| Argomenti **fissi** da nascondere all'LLM? (es. `status: open`) | `forced_arguments` |
| Valori da **env operatore**? (es. `workspaceId` da `WORKSPACE_ID`) | `forced_arguments_from_env` + dichiarazione in `competency.env` |
| Il upstream usa **nomi parametro diversi** dal contesto Dorg? | `upstream_injections` e/o `argument_aliases` |
| Chi può usare il tool? | `allowed_groups` (default `all_users`) |
| Serve **consenso** su contesto utente? (`user_email`, `user_groups`, …) | `injected_params` — solo chiavi necessarie |

### 4. Injected params e mapping chiavi (se applicabile)

Se un tool deve ricevere dati dal collega (email, gruppi, …) **e** passarli all'upstream con **nome diverso**:

1. Chiedi quali chiavi orchestrator servono → `injected_params` (vocabolario chiuso: `python -m cli list-injected-params`).
2. Chiedi come si chiamano sul MCP remoto → `upstream_injections`:

```json
{ "injected_key": "user_email", "upstream_key": "assignee" }
```

Esempio da chiarire con l'utente: *"Il dorg inietta `user_email`; l'API remota si aspetta `assignee`?"*

### 5. Tier-2 — codice custom (solo se necessario)

Chiedi:

> Serve logica che il manifest non può esprimere? (chiamate multiple, validazione complessa, risposta trasformata, tool che non esiste upstream)

Per ogni tool tier-2 chiedi:

| Domanda | Output |
|---|---|
| Nome handler (`handler` nel manifest)? | es. `assign_ticket_to_me` |
| Quale tool upstream chiama (se uno)? | `upstream_tool_name` + implementazione in `competency_handlers.py` |
| Schema input per il dorg? | `input_schema` nel manifest o `@register(..., input_schema=...)` |

Se tutto è rename + forced args + `upstream_injections`, **resta tier-1**.

### 6. Env da dichiarare in University (obbligatorio elenco, non valori)

Per ogni variabile che l'operatore configurerà:

| Domanda | Campi `competency.env` |
|---|---|
| Nome variabile (`key`)? | es. `UPSTREAM_MCP_ENDPOINT` |
| Etichetta e descrizione per Console? | `label`, `description` |
| Obbligatoria? | `required` |
| Segreto (Key Vault)? | `secret` |

Minimo quasi sempre: `UPSTREAM_MCP_ENDPOINT`. Aggiungi `UPSTREAM_API_KEY` / bearer solo se l'upstream lo richiede. Aggiungi env custom solo se usate in `forced_arguments_from_env`.

### 7. Pubblicazione e runtime (consigliato)

| Domanda | Uso |
|---|---|
| **Registry Docker** e tag immagine? | `competency_docker_image`, `SOURCE_DATE_EPOCH=$(date +%s) docker build -t ...` |
| **`intended_usage`** — 2–5 frasi "Use when…" / "Do not use…"? | Manifest University |
| **vCPU / RAM** container? | Default `0.5` / `1.0` se non specificato |
| **Documentazione utente** — cosa deve sapere il collega che usa la skill? | `bridges/<nome>/DOCUMENTATION.md` |

### 8. Checklist prima di generare

Conferma con l'utente:

- [ ] `competency_id` e nome cartella `bridges/<nome>/` definiti
- [ ] Endpoint upstream noto (o piano B senza `generate-manifest` live)
- [ ] Strategia tool (passthrough / exclude / virtual) chiara
- [ ] Per ogni tool non banale: mapping, groups, injected, forced args documentati
- [ ] Nessun segreto nel manifest o in `project.config.json`
- [ ] Tier-2 giustificato solo dove serve

### Ordine operativo dopo le risposte

```
1. python -m cli init-bridge <nome> [--template passthrough|default]
2. Compilare bridges/<nome>/project.config.json dalle risposte
3. python -m cli generate-manifest --bridge <nome>   # se upstream raggiungibile
4. bridges/<nome>/competency_handlers.py             # solo tier-2
5. bridges/<nome>/DOCUMENTATION.md
6. python -m cli validate-manifest --bridge <nome>
7. python -m cli package --bridge <nome>
```

### Cosa non chiedere

- Valori di API key, bearer o password (solo *se* servono e *come* dichiararli in env).
- Dettagli implementativi del runtime condiviso (`bridge/server.py`) — non si forkano per competenza.
- Conferma su ogni singolo tool se l'utente ha chiesto passthrough completo e non ci sono tool da escludere.

## Workflow agente

```
1. Raccogli: endpoint MCP, credenziali, competency metadata, tool mappings
2. python -m cli init-bridge <nome>
3. Edita bridges/<nome>/project.config.json
4. python -m cli generate-manifest --bridge <nome>
5. Se handler richiesti: edita bridges/<nome>/competency_handlers.py
6. Edita bridges/<nome>/DOCUMENTATION.md
7. python -m cli package --bridge <nome>
8. SOURCE_DATE_EPOCH=$(date +%s) docker build -t <registry>/<image>:<tag> bridges/<nome>/.build
   # SOURCE_DATE_EPOCH obbligatorio: Azure Container Apps rifiuta immagini con
   # timestamp `created` non-UTC (Docker 29+/containerd store, podman)
```

## project.config.json

Vive in `bridges/<nome>/project.config.json`. Mappings in `tool_mappings`:

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
- tool con `handler` → `bridges/<nome>/competency_handlers.py` (via package)
- altrimenti → `manifest.py` tier-1
- tool non in manifest → non esposto (se manifest.tools non vuoto)

## ENV bridge

| Variabile | Scopo |
|---|---|
| `UPSTREAM_MCP_ENDPOINT` | URL MCP remoto |
| `UPSTREAM_API_KEY` | Credenziale (header configurabile) |
| `UPSTREAM_BEARER_TOKEN` | Alternativa bearer |
| `UPSTREAM_AUTH_MODE` | `api_key`, `bearer` o `oauth` |
| `UPSTREAM_OAUTH_TOKEN_URL` | Token endpoint OAuth (default Google) |
| `UPSTREAM_OAUTH_CLIENT_ID` | Client id OAuth (mode `oauth`) |
| `UPSTREAM_OAUTH_CLIENT_SECRET` | Client secret OAuth (mode `oauth`) |
| `UPSTREAM_OAUTH_REFRESH_TOKEN` | Refresh token (da `python -m cli oauth-bootstrap`) |
| `UPSTREAM_OAUTH_SCOPES` | Scope opzionali (spazio-separati) |
| `BRIDGE_AUTH_TOKEN` | Bearer orchestrator → bridge |
| `MANIFEST_PATH` | Default `competency.manifest.json` |
| `HANDLERS_MODULE` | Default `competency_handlers` |

## Validazione

```bash
python -m cli validate-manifest --bridge <nome>
python -m cli list-bridges
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
| `bridges/<nome>/project.config.json` | Setup generazione |
| `bridges/<nome>/competency.manifest.json` | 1 (+ dichiarazione handler) |
| `bridges/<nome>/competency_handlers.py` | 2 solo se necessario |
| `bridges/<nome>/DOCUMENTATION.md` | Sempre |

Non modificare `bridge/server.py` / `bridge/manifest.py` — upgrade centralizzato via `package`.
