# Page: Home

## 1. Purpose & Scope

Minimal landing page: bot name/description and quick links into Dashboard and Guilds. Legacy's home page also contained a visible "Auth Placeholder" ("Authentication will be implemented here") — carried forward as an explicit marker of the same still-open gap tracked in `web.md` §3/§13, not as functionality to design around yet.

## 2. Route & Entry Point

| | |
|---|---|
| **Frontend route** | `/` |
| **Frontend source** | `frontend/src/pages/HomePage.tsx` |
| **Nav label** | "Home" |

## 3. Layout & Components

Just the shared app shell/nav (`components.md` §1) plus a centered title/subtitle and two quick-link buttons (Dashboard, Guilds). No page-specific components beyond that.

## 4. Data Sources

| Source | Channel | Format | Trigger |
|---|---|---|---|
| Shared bot-info endpoint (used by the footer on every page, `components.md` §1) | `GET /api/bot/info` | `{name, description, id, invite_link, version}` | On page load |

## 5. Backend Endpoints (owned by this page)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/bot/info` | — | `{name, description, id, invite_link, version}` | Not really "owned" by Home specifically — it's the shared footer/nav's data source (`components.md` §1) and happens to also populate Home's title/subtitle; documented here since Home is the simplest page and this is its only data need. **`name`/`description`/`id`/`invite_link` are now resolved:** read from the shared `status.py` document (`azure.md` §2's `bot.json`-style document), which `bot/discord_bot.md` §6.3 now confirms `Bot` actively keeps alive with periodic writes (previously just a named-but-dormant file in the file tree). **`version` remains open** — same unresolved gap `pages/webhook.md` §9 already flags (the coordinated release tag lives with `Launcher`, which never reaches Azure); not solved by this revision. |

## 6. User Interactions & Actions

Quick-link buttons navigate to `/dashboard` and `/guilds` respectively — no backend calls.

## 7. State & Refresh Behavior

Fetched once on load, no polling, no live data.

## 8. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `/api/bot/info` fails | Fetch rejects or non-2xx | Title/subtitle fall back to generic static text (matches legacy's HTML defaults) |

## 9. Dependencies

| Dependency | Used for | Notes |
|---|---|---|
| Azure Blob Storage (`status.py` document) | `name`/`description`/`id`/`invite_link` on `/api/bot/info` | **Resolved** — see §5; `bot/discord_bot.md` §6.3 |
| Bot/`Launcher` version source | `version` on `/api/bot/info` | Still undecided — see §5; same underlying gap as `pages/webhook.md` §9 |

## 10. Open Items / Future Work

- ~~Where bot identity data actually lives in this architecture is unresolved~~ — **resolved for `name`/`description`/`id`/`invite_link`**: the shared `status.py` document (§5, §9). **`version` remains unresolved** — same gap noted from `pages/webhook.md`, just the second place it surfaces.
- Authentication remains an explicit, visible gap (`web.md` §3/§13) — carried forward, not addressed by this page beyond keeping the marker in mind for whoever eventually designs the login flow.
