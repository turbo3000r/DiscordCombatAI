# Page: Suggestions

## 1. Purpose & Scope

Review and respond to user-submitted suggestions/tickets (submitted via the Bot's `/suggest` command, per `architecture.md` Scenario 3 and `bot/commands/suggest.md`, now drafted). This is the one page whose primary purpose is a write workflow, not just observability — an admin filters/reads suggestions and sends a response, which reaches the user as a Discord DM via `Bot`.

## 2. Route & Entry Point

| | |
|---|---|
| **Frontend route** | `/suggestions` |
| **Frontend source** | `frontend/src/pages/SuggestionsPage.tsx` |
| **Nav label** | "Suggestions" |

## 3. Layout & Components

`SplitPanelList` (`components.md` §4): left panel is a filterable list of suggestion cards (`ticket_uid` as the primary human id, type indicator dot + catalog **label**, title/details preview, `StatusBadge` — `components.md` §5 — for Pending/Done, submitter display name + guild snapshot name + created-at meta, category tags using catalog labels); right panel is the selected suggestion's full detail: metadata (including Cosmos `id` for ops if shown), submitter snapshot, conversation thread (`conversation[]` from `contracts/suggestion.md`), and a response form (text area + three action buttons, §6). Filter controls above the list: type, category (multi-select — options from catalog `{value,label}`, filter by value), sort order (new/old), status (all/pending/done), and a "Clear filters" button.

**Where the type/category options themselves come from:** `suggestion_catalog` on the shared status document (`contracts/status_document.md`) as `list[{value, label}]` — **Web** seeds and may edit; Bot/Web both read. Exact list endpoint may be `GET /api/suggestions/categories` or folded into bot-info — still an implementation detail (P1.6), not an ownership gap.

## 4. Data Sources

| Source | Channel | Format | Trigger |
|---|---|---|---|
| This page's own backend (§5) | `GET /api/suggestions` | `{items: [...], total}` | On page load and whenever a filter changes (§7) |
| This page's own backend (§5) | `GET /api/suggestions/{id}` | Single suggestion detail | Not currently used separately — detail is served from the already-fetched list (§7); kept here only if a future direct-link/deep-link use case needs it |

## 5. Backend Endpoints (owned by this page)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/suggestions` | Query + Bearer | `{items: [...], total}` | Reads directly from the Cosmos DB Suggestions collection (`web.md` §5); the `status` (pending/done) filter is applied client-side over the already-fetched set, not a query param, matching the legacy implementation. List items expose `ticket_uid`, submitter, catalog values (UI maps labels), and latest `response_text` / status. |
| `GET` | `/api/suggestions/{suggestion_id}` | Path + Bearer | Single suggestion object | Path key is Cosmos `id` (UUID). See note in §4 |
| `POST` | `/api/suggestions/{suggestion_id}/respond` | Body: `{mode: "send" \| "done_no_feedback" \| "done_auto_feedback", response_text?: string}`; Bearer required | Updated suggestion object | Mutating endpoint — appends staff `conversation` entry, updates `response_text` / notification fields, persists Entra actor audit (`contracts/suggestion.md` §2, `contracts/web_auth.md`) |

## 6. User Interactions & Actions

| Action | Effect |
|---|---|
| Select a suggestion card | Shows its full detail (`ticket_uid`, submitter, guild snapshot) + conversation thread + response form in the right panel |
| Type / category / order / status filter change | Re-fetches or re-filters the list (§5); status filter is client-side only; type/category filters use catalog values with labels in the UI |
| "Clear filters" | Resets all filters to defaults and re-fetches |
| "Send" (with response text) | `POST .../respond` `mode: "send"` — Cosmos write sets ticket `status=done`, `notification_status=pending`, appends staff conversation entry (`contracts/suggestion.md`), then enqueues Queue message (`id` + `ticket_uid`). Bot claims via ETag before DM. |
| "Mark done, no feedback" | `mode: "done_no_feedback"` — `notification_status` stays `null`; no Queue enqueue |
| "Auto feedback" | `mode: "done_auto_feedback"` — same pending + enqueue path as Send |
| Refresh button | Re-fetches the current filtered view |

Once a suggestion is `status=done`, the response form and all three action buttons disable — no "re-open" flow exists. Do not rely on obsolete legacy booleans (`responded` / `response_given`).

## 7. State & Refresh Behavior

- No polling and no live updates — list refreshes only on load, filter change, manual refresh, or immediately after a successful response action (to reflect the new status). New incoming suggestions from `/suggest` are not pushed to an already-open page.
- Filters and current selection are client-side-only state, not persisted across reloads.
- The conversation thread (§3) is rendered from `conversation[]` on the suggestion document — no separate endpoint, no pagination (assumes a bounded number of entries per ticket). User modal entry first; staff responses appended on `/respond`.

## 8. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `/api/suggestions` fails | Fetch rejects or non-2xx | List shows an inline error message; **401** → MSAL login; **403** → not-authorized page (`contracts/web_auth.md`) |
| `/respond` fails | Non-2xx response | Response form re-enables, an inline error status message is shown with the server's error detail if available |
| `/respond` succeeds in Cosmos DB but the Queue notification fails, is lost, or `Bot` is offline | Self-healing via Bot claim + sweep (`contracts/suggestion.md` §3, `discord_bot.md` §6.6). UI may show "response saved, notification pending/claiming/sent/failed". |

## 9. Dependencies

| Dependency | Used for | Notes |
|---|---|---|
| Azure Cosmos DB | Suggestion reads/updates | Direct `cosmos.py` access, per `web.md` §5 |
| Azure Queue Storage | Notifying `Bot` to send the response DM | Per `architecture.md` Scenario 3; failure handling gap noted in §8 |
| `Bot` (indirect) | Actually delivering the DM to the user, and localization lookup for `done_auto_feedback` | `Web` never talks to `Bot` directly (`web.md` §1) — this is entirely mediated through the Queue Storage event; if `Bot` is offline, the notification sits in the queue until it comes back, per `architecture.md`'s Queue Storage polling model |
| `contracts/suggestion.md` | Ticket + queue + claim state machine | Canonical |
| `contracts/status_document.md` | Catalog read/write ownership | Web seeds/edits; Bot reads |

## 10. Open Items / Future Work

- ~~Notification delivery / claim~~ — **resolved (P0.5.1):** `contracts/suggestion.md`.
- ~~Catalog ownership~~ — **resolved (P0.5.3):** Web seeds/edits.
- No confirmation step before marking done — candidate for shared modal (optional UX; not a security gap — mutations already require Entra admin).
- Exact catalog HTTP endpoint shape — P1.6.
- Remaining delivery edge cases — P1.4.
- ~~Admin auth for mutations~~ — **resolved (P0.7):** Bearer + admin group; audit fields `acted_by_oid` / `acted_by_upn` / `acted_at` on ticket (`contracts/suggestion.md`, `contracts/web_auth.md`).
