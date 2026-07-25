# Page: Suggestions

## 1. Purpose & Scope

Review and respond to user-submitted suggestions/tickets (Bot `/suggest`, `bot/commands/suggest.md`). Write workflow: admin filters/reads tickets and sends a response; production delivery is a Discord DM via Bot Queue poller/sweep (`contracts/suggestion.md`, `discord_bot.md` §6.6).

**Phase 3 Web slice (only):** authenticated list/detail sufficient for response handling, `POST .../respond`, shared suggestion response service, Queue enqueue for notifying modes, catalog seed/update only if needed by `/suggest`. Home, Guilds, Dashboard, Performance, webhook broadcast, S13, and full P1.6 polish are **deferred** (`web.md` Phase 3 note).

## 2. Route & Entry Point

| | |
|---|---|
| **Frontend route** | `/suggestions` |
| **Frontend source** | `src/web/frontend/.../SuggestionsPage.tsx` (target tree) |
| **Nav label** | "Suggestions" |

Legacy root `web/` (vanilla HTML/JS) is **reference-only** — implement under `src/web/` (`web.md` §2).

## 3. Layout & Components

`SplitPanelList` (`components.md` §4): left = filterable list (`ticket_uid`, type label, title/details preview, Pending/Done badge, submitter/guild/created meta, category tags); right = detail (metadata, conversation thread, response form). Filters: type, category (catalog values), sort, status, clear.

Catalog options: `suggestion_catalog` on the status document (`contracts/status_document.md`). Exact list endpoint shape beyond what this page needs remains P1.6.

## 4. Data Sources

| Source | Channel | Trigger |
|---|---|---|
| `GET /api/suggestions` | Bearer (prod Entra / dev local admin) | Load / filter / refresh |
| `GET /api/suggestions/{id}` | Bearer | Deep-link / refresh detail after conflict |
| `POST /api/suggestions/{id}/respond` | Bearer + `Idempotency-Key` | Send / done modes / failed-notification retry |

## 5. Backend Endpoints (owned by this page)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/suggestions` | Query + auth | `{items, total}` | Redact secrets; expose `ticket_uid`, status, `notification_status`, catalog values |
| `GET` | `/api/suggestions/{suggestion_id}` | Path + auth | Single ticket | Path = Cosmos `id` |
| `POST` | `/api/suggestions/{suggestion_id}/respond` | Body `{mode, response_text?}` + `Idempotency-Key` | Updated ticket | Modes and state machine: `contracts/suggestion.md` §3 / §3a |

Shared response service (target `src/web/backend/...`): one code path for Cosmos mutation + conditional Queue enqueue; Bot and Web must not fork claim rules.

## 6. User Interactions & Actions

| Action | Effect |
|---|---|
| Select card | Show detail + conversation + response form |
| Filters / clear / refresh | Re-fetch or re-filter |
| "Send" | `mode=send` — first response when `status=pending`, **or** failed-notification retry when `notification_status=failed` (`suggestion.md` §3a) |
| "Mark done, no feedback" | `mode=done_no_feedback` — only when `status=pending` |
| "Auto feedback" | `mode=done_auto_feedback` — only when `status=pending` |

**Form enablement:**

- `status=pending` → all three actions available (subject to validation).
- `status=done` and `notification_status=sent` or `null` (no-feedback) → form disabled (no re-open).
- `status=done` and `notification_status=failed` → allow **Send** retry only (confirmed/new body required); other done modes stay disabled.
- `notification_status=pending|claiming` → show delivery-in-progress; disable duplicate Send unless idempotent replay.

## 7. State & Refresh Behavior

- No live push — refresh on load, filter change, manual refresh, or successful respond.
- Conversation from `conversation[]`; no pagination in Phase 3 (bounded entries assumed).

## 8. Failure Modes

| Failure | Recovery |
|---|---|
| List/detail auth failure | **401** login / **403** not-authorized (`web_auth.md`) |
| `/respond` validation / conflict | Re-enable form; show error; on ETag 412 reload detail |
| `/respond` Cosmos OK, Queue enqueue fails | Ticket stays `pending`; UI shows saved + notification pending; Bot sweep recovers (`suggestion.md`) |
| Idempotent replay | Return prior result; no second conversation entry / enqueue |
| Development mode | Persist via `dev-support`; **no** Queue/DM (`local_development.md` §7) |

## 9. Dependencies

| Dependency | Notes |
|---|---|
| `contracts/suggestion.md` | State machine + failed retry |
| `contracts/web_auth.md` / `local_development.md` | Auth boundary |
| `contracts/status_document.md` | Catalog |
| `discord_bot.md` §6.6 | DM delivery |
| S12 | Acceptance |

## 10. Open Items / Future Work

- Full OpenAPI / pagination / operational polish — P1.6 (deferred past Phase 3 minimal slice).
- Unrestricted multi-admin editing beyond ETag + Idempotency-Key — deferred.
- Optional confirm dialog before done — UX only.
