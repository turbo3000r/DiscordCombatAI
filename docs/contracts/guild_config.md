# Contract: Guild Configuration (Cosmos DB Document)

> **Why this lives in `contracts/`, not a single service doc:** this document is written almost exclusively by `Bot` but read directly by `Web` (`web/pages/guilds.md`, `web/pages/dashboard.md`) via its own `cosmos.py` access — no service-to-service call sits between them, Cosmos DB *is* the interface. Per the same rationale already established for `task_progress.md` and `localization.md`, a schema two independent codebases both depend on belongs here, not duplicated inside `bot/discord_bot.md` and `web/web.md` separately. **Confirmed decision (project owner):** this gets its own contract file rather than staying inline in `discord_bot.md`.

---

## 1. Purpose

Defines the single Cosmos DB document shape that represents "everything known about one guild" — both the admin-configured settings `/config` writes (`bot/commands/config.md`) and the Discord-sourced metadata `Bot` observes passively (name, icon, member count, owner). One document, one collection, one source of truth — replacing legacy's per-guild local JSON file (`docs/legacy/Old_arch.md`) entirely.

---

## 2. Storage Location

| | |
|---|---|
| **Resource** | Azure Cosmos DB (`azure.md` §3, `AZURE_COSMOS_ENDPOINT`) |
| **Collection** | `GuildConfigs` |
| **Document `id`** | The guild's Discord snowflake ID, as a string — one document per guild, no secondary lookup needed. |
| **Partition key** | `/id` (same value) — trivial partitioning, no guild-spanning queries exist anywhere in any written doc. |

---

## 3. Schema

```python
class GuildConfigDocument(TypedDict):
    id: str                      # Discord guild ID (string) — also the partition key, see §2
    guild_id: str                # duplicate of id, kept as a real field so it survives being read out of context (e.g. a query projection)

    # --- Admin-configured (written by /config, bot/commands/config.md §9) ---
    language: str                 # BCP-47 locale, e.g. "uk-UA" — per contracts/localization.md
    api_key: str                  # Google Gemini API key. Stored PLAINTEXT (confirmed decision, §6) — v1 scope, security follow-up flagged
    model: str                    # Gemini model name, e.g. "gemini-2.5-flash" — see config.md §6 for how the option list is sourced
    webhook_url: str              # Discord webhook URL for release/announcement broadcasts (web/pages/webhook.md §5) — "" if unset
    enabled: bool                 # AI features on/off — mirrors legacy Guild.enableAI(), requires a valid api_key to ever be true (config.md §9)

    # --- Discord-sourced metadata (written by Bot's guild lifecycle, bot/discord_bot.md §6.2 — NOT admin-editable) ---
    name: str                     # guild.name
    icon_url: str | None          # guild.icon.url if set, else null
    member_count: int             # guild.member_count
    owner_id: str                 # guild.owner_id, as a string

    # --- Bookkeeping ---
    created_at: str               # ISO 8601 UTC — set once, on_guild_join, never overwritten afterward
    updated_at: str               # ISO 8601 UTC — set on every write to this document, by anything
    left_at: str | None           # ISO 8601 UTC — set on_guild_remove (§4), null while the bot is still a member. Soft-delete (proposed, §6): the document is never hard-deleted, so history/analytics survive a kick+re-invite.
```

`webhook_configured` (used by `web/pages/guilds.md` §3) is **not** a stored field — it's derived by whoever reads this document as `bool(webhook_url)`, since storing it separately would just be a second, driftable copy of the same fact.

---

## 4. Field Ownership & Write Triggers

| Field(s) | Written by | Trigger |
|---|---|---|
| `language`, `api_key`, `model`, `webhook_url`, `enabled` | `Bot` | `/config` → "Apply" (`bot/commands/config.md` §6, §9) |
| `name`, `icon_url`, `member_count`, `owner_id` | `Bot` | `on_guild_join` (initial write), `on_guild_update` (name/icon/owner changes), and a periodic reconciliation sweep (`bot/discord_bot.md` §6.2) — **confirmed decision (project owner):** all three triggers, not just the two Discord-pushed events, since Discord has no event for member-count drift specifically. |
| `id`, `guild_id`, `created_at` | `Bot` | `on_guild_join` only — a fresh document with all admin-configured fields at their defaults (§5). Never overwritten by any later write. |
| `left_at` | `Bot` | `on_guild_remove` — sets the timestamp; does **not** delete the document (§6). |
| `updated_at` | `Bot` | Every write to this document, regardless of which fields changed. |

**No field in this document is ever written by `Web`** — `Web`'s Cosmos access to this collection is read-only everywhere it's used (`web/pages/guilds.md`, `web/pages/dashboard.md`'s guild count). If that ever changes (e.g. a future admin-edit-from-Web feature), it needs an explicit decision here first, not an assumed extension.

---

## 5. Defaults on `on_guild_join`

Carried forward from legacy's `setup_guild` (`docs/legacy/Old_arch.md`), now targeting this Cosmos document instead of a local file:

```json
{
  "language": "en",
  "api_key": "",
  "model": "",
  "webhook_url": "",
  "enabled": false
}
```

`enabled` can never legitimately become `true` before a valid `api_key` is staged and applied — enforced by `/config`'s own flow (`bot/commands/config.md` §9), not by this document itself.

---

## 6. Consumers

| Consumer | Reads | Writes |
|---|---|---|
| `Bot` | Full document, on every command needing guild context (`ProcessCommand`'s `Guild` wrapper, `modules/guild.py`) | See §4 |
| `Web` — `pages/guilds.md` | Full document (guild list + detail panels) | — (read-only) |
| `Web` — `pages/dashboard.md` | Document count only, for the `guilds` metric card | — (read-only) |

---

## 7. Open Items

- **`api_key` is stored in plaintext (confirmed, v1 scope).** Flagged as a security follow-up, not a decision reversed here — matches legacy's own plaintext local-JSON storage, so this is not a regression, just not yet hardened. Revisit with application-level encryption or an Azure Key Vault secret-reference indirection if this ever needs to harden before a wider release.
- **`left_at` soft-delete is a proposed convention, not an explicit "project owner confirmed" decision** — flagged as the one field in this schema introduced by inference (avoiding data loss on a kick+re-invite cycle) rather than dictated. Revisit if guild churn ever makes stale left-guild documents a real cleanup problem.
- Whether the periodic reconciliation sweep (§4) should run per-guild on a fixed interval, or be triggered some other way (e.g. only for guilds not touched by an `on_guild_update` recently) is left as an implementation detail — `bot/discord_bot.md` §3 defines the interval env var, not the exact scheduling algorithm.
