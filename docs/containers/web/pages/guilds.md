# Page: Guilds

## 1. Purpose & Scope

Browse every guild the bot is configured for, and view a selected guild's basic info + bot configuration. Legacy scoped this as read-only with a "full guild statistics and management coming soon" placeholder in the detail panel — that scope is carried forward as-is; this page is not where guild configuration gets *edited* (no such flow exists anywhere yet, in legacy or here).

## 2. Route & Entry Point

| | |
|---|---|
| **Frontend route** | `/guilds` |
| **Frontend source** | `frontend/src/pages/GuildsPage.tsx` |
| **Nav label** | "Guilds" |

## 3. Layout & Components

`SplitPanelList` (`components.md` §4): left panel is a scrollable list of guild cards (icon, name, member count, guild ID, webhook-configured `StatusBadge` — `components.md` §5); right panel shows the selected guild's details, or an empty state if none is selected. A manual "Refresh" button and a guild count sit in the header.

## 4. Data Sources

| Source | Channel | Format | Trigger |
|---|---|---|---|
| This page's own backend (§5) | `GET /api/guilds` | `{guilds: [...], count}` | On page load and on manual refresh (§7) |
| This page's own backend (§5) | `GET /api/guilds/{id}` | Single guild detail object | On selecting a guild card |

## 5. Backend Endpoints (owned by this page)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/guilds` | — | `{guilds: [{id, name, member_count, icon_url, created_at, owner_id, webhook_configured}], count}` | **Source now resolved for every field — see §9.** Every field maps 1:1 onto `contracts/guild_config.md` §3's document; legacy's live `bot.guilds` read is replaced by a plain Cosmos DB read of the same collection `Bot` now keeps in sync. |
| `GET` | `/api/guilds/{guild_id}` | Path: guild ID | Guild detail (basic info + bot config subset: language, model, enabled) | Same sourcing caveat as above |

## 6. User Interactions & Actions

| Action | Effect |
|---|---|
| Click a guild card | Selects it, fetches and displays its detail via `GET /api/guilds/{id}` |
| Refresh button | Re-fetches `GET /api/guilds`, disables itself and shows "Refreshing…" while in flight |

No mutating actions exist on this page (read-only, per §1).

## 7. State & Refresh Behavior

- No polling and no live updates — guild list refreshes only on page load or manual "Refresh" click. This matches legacy's own behavior; guild membership/config doesn't change often enough to warrant push updates.
- Selected guild ID is client-side-only state, cleared on refresh (mirrors legacy: refreshing re-fetches the list but doesn't re-select).

## 8. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `/api/guilds` fails | Fetch rejects or non-2xx | List panel shows an inline error message; `ConnectionStatus` (`components.md` §2) flips to "Disconnected" |
| `/api/guilds/{id}` fails | Fetch rejects or non-2xx | Detail panel shows an inline error message; selection in the list is preserved |

Underlying Cosmos DB failure modes are defined once in `web.md` §9 — not re-derived here.

## 9. Dependencies

| Dependency | Used for | Notes |
|---|---|---|
| Azure Cosmos DB | `GuildConfigs` collection (`contracts/guild_config.md`) — now the **only** dependency for every field on this page, admin-config and Discord-metadata alike | Direct `cosmos.py` access, per `web.md` §5 |
| `Bot` (indirect, resolved) | Keeps `name`, `member_count`, `icon_url`, `owner_id` fresh on the same document via `on_guild_join`/`on_guild_update`/a periodic reconciliation sweep | **Resolved, confirmed decision** — `bot/discord_bot.md` §6.2, `contracts/guild_config.md` §4. `Web` never talks to `Bot` or Discord directly; it just reads whatever `Bot` last wrote. Freshness is bounded by `Bot`'s `BOT_GUILD_SYNC_INTERVAL_SEC` (default 1h) between reconciliation sweeps for `member_count` specifically — name/icon/owner changes land immediately via `on_guild_update`. |

**`webhook_configured`** (whether a guild has a webhook URL set) is derivable purely from the Cosmos DB guild-config document already — no `Bot`-write dependency beyond what's already there, since the webhook URL itself is admin-entered config data, not Discord-sourced metadata.

## 10. Open Items / Future Work

- ~~Every Discord-sourced display field is blocked on `Bot` committing to write guild metadata into Cosmos DB~~ — **resolved**: `bot/discord_bot.md` §6.2 and `contracts/guild_config.md` now define exactly which triggers keep those fields fresh. This page's `/api/guilds` implementation is unblocked — no further design decision needed before building it.
- `member_count` can be up to `BOT_GUILD_SYNC_INTERVAL_SEC` (default 1 hour) stale between `Bot`'s reconciliation sweeps, since Discord has no push event for membership-count drift specifically — acceptable per the confirmed decision in `discord_bot.md` §6.2, not treated as a bug.
- The "full guild statistics and management coming soon" placeholder from legacy is carried forward as an explicit non-goal for now, not a near-term addition.
