# Contract: Guild Configuration (Cosmos DB Document)

> **Why this lives in `contracts/`, not a single service doc:** this document is written almost exclusively by `Bot` but read directly by `Web` (`web/pages/guilds.md`, `web/pages/dashboard.md`) via its own repository access — in production that is Cosmos DB (`cosmos.py`); in product development it is `dev-support` (`contracts/local_development.md`). Per the same rationale already established for `task_progress.md` and `localization.md`, a schema two independent codebases both depend on belongs here, not duplicated inside `bot/discord_bot.md` and `web/web.md` separately. **Confirmed decision (project owner):** this gets its own contract file rather than staying inline in `discord_bot.md`.

---

## 1. Purpose

Defines the single Cosmos DB document shape that represents "everything known about one guild" — both the admin-configured settings `/config` writes (`bot/commands/config.md`) and the Discord-sourced metadata `Bot` observes passively (name, icon, member count, owner). One document, one collection, one source of truth — replacing legacy's per-guild local JSON file (`docs/legacy/Old_arch.md`) entirely.

---

## 2. Storage Location

| | |
|---|---|
| **Resource (production)** | Azure Cosmos DB (`azure.md` §3, `AZURE_COSMOS_ENDPOINT`) |
| **Collection** | `GuildConfigs` |
| **Document `id`** | The guild's Discord snowflake ID, as a string — one document per guild, no secondary lookup needed. |
| **Partition key** | `/id` (same value) — trivial partitioning, no guild-spanning queries exist anywhere in any written doc. |
| **Development** | Same `GuildConfigDocument` schema via `GuildRepository` → Compose-only `dev-support` SQLite (`contracts/local_development.md` §5–§6). Not Cosmos. Production Patch/ETag concurrency remains Azure-only semantics for S12-class acceptance. |

---

## 3. Schema

```python
class GuildConfigDocument(TypedDict):
    schema_version: int          # current = 1. Same reject-unknown-version / Web ±1 additive
                                  # tolerance rule as other shared documents (`contracts/drain_status.md` §5).
    id: str                      # Discord guild ID (string) — also the partition key, see §2
    guild_id: str                # duplicate of id, kept as a real field so it survives being read out of context (e.g. a query projection)

    # --- Admin-configured (written by /config, bot/commands/config.md §9) ---
    language: str                 # Closed v1 enum: "en" | "es" | "ua" — NOT free-form BCP-47.
                                  # AI graphs receive a mapped language_locale (ua → uk-UA). Canonical: contracts/localization.md §3–§4.
    api_key: str                  # Google Gemini API key. Stored PLAINTEXT (confirmed decision, §7) — v1 scope, security follow-up flagged
    model: str                    # Gemini model name, e.g. "gemini-2.5-flash" — see config.md §6 for how the option list is sourced
    webhook_url: str              # Discord webhook URL for release/announcement broadcasts (web/pages/webhook.md §5) — "" if unset
    enabled: bool                 # AI features on/off — mirrors legacy Guild.enableAI(), requires a valid api_key to ever be true (config.md §9)

    # --- Discord-sourced metadata (written by Bot's guild lifecycle, bot/discord_bot.md §6.2 — NOT admin-editable) ---
    name: str                     # guild.name
    icon_url: str | None          # guild.icon.url if set, else null
    member_count: int             # guild.member_count
    owner_id: str                 # guild.owner_id, as a string

    # --- Bookkeeping ---
    created_at: str               # ISO 8601 UTC — set once on first create, never overwritten (including rejoin, §7)
    updated_at: str               # ISO 8601 UTC — set on every write to this document, by anything
    left_at: str | None           # ISO 8601 UTC — set on_guild_remove (§4), null while the bot is still a member.
                                  # Soft-delete CONFIRMED (§7): document is never hard-deleted in v1.
```

`webhook_configured` (used by `web/pages/guilds.md` §3) is **not** a stored field — it's derived by whoever reads this document as `bool(webhook_url)`, since storing it separately would just be a second, driftable copy of the same fact.

**Webhook URL allowlist (P0.7):** when Bot `/config` stores `webhook_url`, the value must match the Discord-host allowlist in `contracts/web_auth.md` §7 (`https://discord.com/api/webhooks/...` or `https://discordapp.com/api/webhooks/...` only). Reject non-HTTPS, other hosts, and IP literals on save. Web re-validates the same allowlist before any Discord POST, even if a legacy Cosmos value is bad.

**Web API redaction (P0.7):** Web list/detail endpoints must **never** return raw `webhook_url` or `api_key` — only `webhook_configured: bool` and non-secret config fields. See `contracts/web_auth.md` §6.

---

## 4. Field Ownership & Write Triggers

| Field(s) | Written by | Trigger |
|---|---|---|
| `language`, `api_key`, `model`, `webhook_url`, `enabled` | `Bot` | `/config` → "Apply" (`bot/commands/config.md` §6, §9) |
| `name`, `icon_url`, `member_count`, `owner_id` | `Bot` | `on_guild_join` / rejoin (§7), `on_guild_update`, and periodic reconciliation (`bot/discord_bot.md` §6.2) |
| `schema_version`, `id`, `guild_id`, `created_at` | `Bot` | First create only — defaults (§5). Never overwritten later (including rejoin). Strict writers reject unknown `schema_version`. |
| `left_at` | `Bot` | `on_guild_remove` sets timestamp; rejoin clears to `null` (§7). Never hard-deletes. |
| `updated_at` | `Bot` | Every write to this document, regardless of which fields changed. |

**No field in this document is ever written by `Web`** — `Web`'s Cosmos access to this collection is read-only everywhere it's used (`web/pages/guilds.md`). Dashboard current guild count does **not** come from this collection (`contracts/telemetry.md` — uses `status.py`). If Web ever needs to mutate guild config, it needs an explicit decision here first.

### 4a. Field-scoped writes + ETag (resolved, P1.3 subset for shared Azure clients)

`src/shared/azure/services/guilds.py` (and any direct Cosmos caller) **must not** replace the whole document when only one field group changes:

| Writer | Patch set (only these paths) |
|---|---|
| `/config` Apply | `language`, `api_key`, `model`, `webhook_url`, `enabled`, `updated_at` |
| Guild lifecycle / sync | `name`, `icon_url`, `member_count`, `owner_id`, `updated_at` (and `left_at` on remove/rejoin) |
| First create | Full document with defaults + metadata |

Use Cosmos **partial Patch** (or equivalent conditional field update) with **If-Match ETag**. On HTTP 412 Precondition Failed: re-read, re-apply the same field set, retry **≤5** times (same budget as `contracts/status_document.md` ETag RMW). Concurrent `/config` and metadata sync therefore cannot clobber each other's fields.

---

## 5. Defaults on `on_guild_join`

Carried forward from legacy's `setup_guild` (`docs/legacy/Old_arch.md`), now targeting this Cosmos document instead of a local file:

```json
{
  "schema_version": 1,
  "language": "en",
  "api_key": "",
  "model": "",
  "webhook_url": "",
  "enabled": false
}
```

`enabled` can never legitimately become `true` before a valid `api_key` is staged and applied — enforced by `/config`'s own flow (`bot/commands/config.md` §9), not by this document itself.

### 5a. Schema evolution

Current `schema_version` is **`1`**. Strict Bot writers/readers that own mutations reject unknown versions. `Web` tolerates **one prior and one following** additive schema version when reading guild documents (`contracts/drain_status.md` §5).

---

## 6. Consumers

| Consumer | Reads | Writes |
|---|---|---|
| `Bot` | Full document, on every command needing guild context (`ProcessCommand`'s `Guild` wrapper, `modules/guild.py`) | See §4 |
| `Web` — `pages/guilds.md` | Active guilds only by default (§7); detail may include a left guild if requested by id | — (read-only) |
| `Web` — `pages/dashboard.md` | — (guild count for Dashboard “now” comes from `status.py`, not this collection — `contracts/telemetry.md`) | — |

---

## 7. Soft-delete, rejoin, and list filtering (resolved, P1.3 subset)

**Soft-delete (confirmed):** `on_guild_remove` sets `left_at` and never hard-deletes. **Retention:** unbounded in v1 (same posture as battle archives). No TTL purge job in v1.

**Rejoin:** if a document already exists for the guild id and `left_at != null`:

1. Clear `left_at` to `null`.
2. Refresh Discord metadata (`name`, `icon_url`, `member_count`, `owner_id`).
3. **Preserve** `created_at` and all admin fields (`language`, `api_key`, `model`, `webhook_url`, `enabled`).
4. Update `updated_at`.

If no document exists, create with defaults (§5). `guilds.py` should expose a single `ensure_active_guild(...)` (name is implementation) that implements create-or-reactivate.

**List/count filtering:** default list helpers and Web `GET /api/guilds` **exclude** documents where `left_at != null`. Pass an explicit `include_left=true` (or fetch-by-id) to see soft-deleted rows. Dashboard “now” guild count continues to come from Bot Gateway membership via `status.py`, not from counting Cosmos rows.

---

## 8. Open Items (remaining P1.3 — not required for shared Azure client scaffolding)

- **`api_key` plaintext (confirmed, v1).** Web redacts (`contracts/web_auth.md` §6); Cosmos at-rest encryption of this field is still deferred.
- **Webhook allowlist** — **resolved (P0.7).**
- **Offline removal detection** while Bot was down (mark `left_at` for Cosmos docs absent from `bot.guilds`) — still P1.3; soft-delete + list filters above are enough for `guilds.py` CRUD shape.
- **`/config` Apply atomicity when a staged key/model is invalid** — still P1.3 / command-owned.
- **Model-catalog timeout/pagination vs Discord 25-option Select** — still P1.3 / command-owned.
- Reconciliation **scheduling algorithm** — implementation detail after semantics above.
