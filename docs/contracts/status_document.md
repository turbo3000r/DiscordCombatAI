# Contract: Status Document (Shared Blob JSON)

> **New this revision — closes P0.5.3.** Moves the nested `StatusDocument` shape out of prose in `azure.md` §2 into a real contract. `azure.md` keeps the `status.py` module listing and links here. Owner decision: **option C** (deploy-time seed + Web edits identity/catalog; Bot owns only `status`).

---

## 1. Storage Location

| | |
|---|---|
| **Resource (production)** | Azure Blob Storage (block blob, JSON) |
| **Container** | `AZURE_STATUS_BLOB_CONTAINER` (default `coordination`) — same container family as the leader lease blob is fine; distinct blob name |
| **Blob name** | `AZURE_STATUS_BLOB_NAME` (default `bot_status.json`) |
| **Access module (production)** | `src/shared/azure/services/status.py` only — no other file reads/writes this blob raw |
| **Development** | Same nested `StatusDocument` schema via `StatusRepository` → `dev-support`. Section owners unchanged (Web: identity/catalog; Bot: status). Not a Blob Lease or PubSub substitute. |

---

## 2. Section Owners

| Section | Writer | Reader |
|---|---|---|
| `identity` | **Web** (admin Home after seed); never Bot | Web (`/api/bot/info`, Home, footer) |
| `suggestion_catalog` | **Web** (Suggestions admin / catalog edit after seed) | Bot (`/suggest` Selects), Web (Suggestions filters) |
| `status` | **Bot** only, periodic push every `BOT_STATUS_PUSH_INTERVAL_SEC` | Web Dashboard current latency/guild_count |

**Seed:** deploy-time seed (or first writer that needs the blob — prefer Web on first Home/Suggestions load, or an explicit one-time seed step) creates **both** `identity` and `suggestion_catalog` with defaults below. Bot continues to own only the `status` section via periodic push; Bot status writes do **not** “keep identity alive.”

---

## 3. Schema

```python
class BotIdentitySection(TypedDict):
    name: str
    description: str
    id: str                 # Discord application/bot user ID
    invite_url: str
    # version: deliberately NOT here — still open (pages/home.md, pages/webhook.md)

class BotStatusSection(TypedDict):
    latency_ms: int
    guild_count: int
    updated_at: str         # ISO 8601 UTC

class CatalogEntry(TypedDict):
    # Same shape as contracts/suggestion.md — catalog stores {value, label}; tickets store values only
    value: str
    label: str

class SuggestionCatalogSection(TypedDict):
    types: list[CatalogEntry]
    categories: list[CatalogEntry]

class StatusDocument(TypedDict):
    schema_version: int     # current = 1
    identity: BotIdentitySection
    status: BotStatusSection
    suggestion_catalog: SuggestionCatalogSection
```

### Seed defaults

**`identity`:** empty strings / placeholders — `name=""`, `description=""`, `id=""`, `invite_url=""`. Web admin fills these on Home after seed.

**`suggestion_catalog`:** carry forward legacy `/suggest` values with display labels (legacy `suggestions.json` / seed lists). Tickets store `value` only; UI Selects and Web filters show `label`:

```python
types = [
    {"value": "minor_issue", "label": "minor issue"},
    {"value": "major_issue", "label": "major issue"},
    {"value": "request", "label": "Request"},
    {"value": "improvement", "label": "Improvement"},
    {"value": "feedback", "label": "Feedback"},
]
categories = [
    {"value": "prompts", "label": "Prompts"},
    {"value": "gamemodes", "label": "Gamemodes"},
    {"value": "settings", "label": "Battle settings"},
    {"value": "generic_environments", "label": "Generic environments"},
    {"value": "commands", "label": "Commands"},
    {"value": "functionalities", "label": "Functionalities"},
    {"value": "localization", "label": "Localization"},
    {"value": "other", "label": "Other"},
]
```

**`status`:** `latency_ms=0`, `guild_count=0`, `updated_at` = seed time (ISO 8601 UTC), until Bot’s first successful push.

---

## 4. Bootstrap, Malformed, and Concurrency

| Case | Behavior |
|---|---|
| Blob missing | First writer that needs it creates the full document with seed defaults (§3). Prefer Web on first Home/Suggestions load, or document an explicit seed step once in ops notes. |
| Malformed JSON / wrong shape | Log **ERROR**, **refuse overwrite** without an operator-driven backup/repair. Do not silently replace a corrupt blob with defaults. |
| Concurrent section updates | Every writer does read-modify-write with **ETag `If-Match`**. On conflict, re-read and retry with bounded attempts (**5**, with jitter). Update only the writer’s own section. |

`status.py` / `StatusService` exposes typed accessors (`get_identity`, `update_identity`, `get_status`, `update_status`, `get_suggestion_catalog`, `update_suggestion_catalog`) so callers never patch raw JSON ad hoc. **`/suggest` must call `get_suggestion_catalog()`** on every new invocation (`bot/commands/suggest.md` §6) — fail closed if missing/malformed; no hardcoded fallback.

**Web identity/catalog writes (P0.7):** when Web updates `identity` or `suggestion_catalog`, log the acting Entra `oid` at INFO (`contracts/web_auth.md` §7). Optional `updated_by_oid` on the document is allowed but not required for v1.

---

## 5. Schema Evolution

`schema_version` required. Strict writers reject unknown versions. Web ±1 additive tolerance when reading (`drain_status.md` §5 / P0.5.5).

---

## 6. Related

| Concern | Canonical doc |
|---|---|
| Env vars for container/blob name | `azure.md` §3 |
| Bot status push | `bot/discord_bot.md` §6.3 |
| Web identity edit / Home | `web/pages/home.md` |
| Catalog consumers | `bot/commands/suggest.md`, `web/pages/suggestions.md` |
| Dashboard “now” latency/guilds | `contracts/telemetry.md` §1/§4 |
| Web admin auth (who may edit identity/catalog) | `contracts/web_auth.md` |
