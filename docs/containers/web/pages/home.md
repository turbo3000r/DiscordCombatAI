# Page: Home

## 1. Purpose & Scope

Minimal landing page: bot name/description and quick links into Dashboard and Guilds. The SPA shell at `/` is public; data APIs (`GET /api/bot/info`, etc.) require Entra admin auth (`contracts/web_auth.md`). Unauthenticated visitors see the shell and are redirected to login when an API returns **401**; signed-in non-admins see the **403** “not authorized” page.

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
| `GET` | `/api/bot/info` | Bearer required | `{name, description, id, invite_link, version}` | Reads `identity` from `contracts/status_document.md`. **Web** may edit identity on Home after seed; Bot status pushes do **not** maintain identity. **`version` remains open.** Bootstrap: if blob missing, Web creates defaults on first load (or explicit seed). Actor `oid` logged on identity writes (`contracts/web_auth.md` §7). |

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
| Azure Blob Storage (status document) | `name`/`description`/`id`/`invite_link` | `contracts/status_document.md` — Web owns `identity` after seed |
| Bot/`Launcher` version source | `version` on `/api/bot/info` | Still undecided |

## 10. Open Items / Future Work

- ~~Identity storage / ownership~~ — **resolved (P0.5.3):** Web seeds + edits `identity`; Bot writes only `status`.
- **`version` remains unresolved**.
- ~~Authentication~~ — **resolved (P0.7):** `contracts/web_auth.md`.
