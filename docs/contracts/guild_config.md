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

### 7a. First-use `/config` (resolved, Phase 3)

When `/config` is invoked and **no active guild document** exists (`left_at == null` row missing):

1. Call `ensure_active_guild(...)` with current Discord metadata (`name`, `icon_url`, `member_count`, `owner_id`).
2. Create with defaults (§5) **or** reactivate a soft-deleted row (§7) — never invent an in-memory-only fake document.
3. Only after a successful persist, load and render the `/config` panel (`bot/commands/config.md` §6).
4. Surface **transient** vs **permanent** Cosmos/repository errors as distinct ephemeral localized messages; **do not** render a panel backed by an unpersisted document.

Offline guild-removal detection (mark `left_at` for Cosmos docs absent from `bot.guilds` while Bot was down) remains **deferred** (not Phase 3).

---

## 8. `/config` Apply — partial-field transaction policy (normative, Phase 3)

Canonical for Apply semantics. UX wording lives in `bot/commands/config.md`; this section owns what may be written.

### 8.1 Storage of `api_key`

`api_key` is stored as **plaintext** in the guild document (v1, confirmed). There is no application-level encryption, Key Vault reference, or hashed representation in the current architecture. Web APIs must still **redact** it (`contracts/web_auth.md` §6). At-rest field encryption remains a deferred security follow-up — do not invent encryption the architecture cannot support.

### 8.2 Building the Patch

1. Validate each staged field independently.
2. Build **one** conditional administrator Patch containing **only valid** staged fields plus `updated_at`.
3. Invalid fields are **excluded** from the Patch and reported as inline field errors — they do not block valid siblings from committing (except the `enabled` gate in §8.4).

### 8.3 Field rules

| Staged field | Commit when valid | On invalid / failed validation |
|---|---|---|
| `language` | Include in Patch | Exclude; inline error |
| `webhook_url` | Include only if Discord-host allowlist passes (`web_auth.md` §7) | Exclude; inline error; previous URL unchanged |
| `api_key` | Include only after Apply-time probe succeeds (§8.5) | **Never** replace the previous key; exclude; inline invalid-key / transient error |
| `model` | Include only if present in the filtered catalog for the **effective** key (staged key if probe-valid, else previously stored key) **and** Apply-time probe accepts the pair | **Never** replace the previous model when unavailable for the staged key; exclude; inline error |
| `enabled=true` | Include only when the **resulting persisted** configuration would have a validated API key and non-empty validated model (§8.4) | Force/keep `enabled=false` or exclude `enabled=true`; inline warning |

**Preserve previous valid key/model values when replacements fail.** A failed staged key must not clear or overwrite the stored key; a failed staged model must not overwrite the stored model.

`language` and a valid webhook **may** commit in the same Patch even if a newly staged API key/model fails validation.

### 8.4 `enabled=true` gate

`enabled=true` may be committed only when, after applying the valid field set in this transaction, the document would have:

- a non-empty `api_key` that has passed validation (newly probed this Apply, or previously stored and not being replaced by a failed stage), and
- a non-empty `model` that is valid for that key.

Otherwise do not write `enabled=true` (leave previous `enabled` or write `false` if the admin explicitly disabled).

### 8.5 Google validation timing (see also `config.md` §6)

| When | Call | Purpose |
|---|---|---|
| API key staged (modal submit) | `client.models.list()` via `google-genai`, off the event loop | Populate `ModelSelect`; not sufficient alone to flip `enabled` |
| Apply (when key and/or model staged for replacement) | One minimal `generateContent` probe | Accept replacement key/model before Patch includes them |

### 8.6 Cosmos write outcome

- Apply successful fields with **one** ETag-conditional Patch (`§4a`, ≤5 retries on 412 only when re-reading and re-applying the **same** validated field set is still correct).
- On **ETag conflict** after budget: reload current state into the panel, clear or re-diff staged changes as needed, and require the administrator to review/retry — do not report success.
- On **Cosmos failure** (transient or permanent): do **not** report success; **preserve staged in-memory state**; show localized transient vs permanent error (`config.md` §12).
- After any Apply attempt, report clearly **which fields were applied** and **which were rejected**.

---

## 9. Open Items (deferred — not Phase 3)

- **`api_key` at-rest encryption** — deferred security follow-up; plaintext v1 confirmed (§8.1).
- **Offline removal detection** while Bot was down — deferred (not required for `/config` / `/suggest`).
- Reconciliation **scheduling algorithm** for metadata sync — implementation detail after §4/§7 semantics.
