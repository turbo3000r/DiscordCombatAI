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

`SplitPanelList` (`components.md` §4): left panel is a filterable list of suggestion cards (type indicator dot + label, title/message preview, `StatusBadge` — `components.md` §5 — for Pending/Done, user + guild + created-at meta, category tags); right panel is the selected suggestion's full detail: metadata, conversation thread, and a response form (text area + three action buttons, §6). Filter controls above the list: type, category (multi-select), sort order (new/old), status (all/pending/done), and a "Clear filters" button.

**Where the type/category options themselves come from (added this revision):** this page no longer has its own hardcoded type/category list. Per `bot/commands/suggest.md` §6/§9, both the type and category values are now sourced from the same shared document `azure.md`'s `services/status.py` already owns (the `bot.json`-style doc, extended with these two new fields) — the single source of truth `Bot`'s `/suggest` command reads to populate its Selects. This page's filter controls and category tags should read from that same document (via this page's own backend, §5) rather than duplicating the 8 category / 5 type values as a second hardcoded list on the `Web` side. Exact endpoint/contract for that read is not designed yet — see §9's note and §10.

## 4. Data Sources

| Source | Channel | Format | Trigger |
|---|---|---|---|
| This page's own backend (§5) | `GET /api/suggestions` | `{items: [...], total}` | On page load and whenever a filter changes (§7) |
| This page's own backend (§5) | `GET /api/suggestions/{id}` | Single suggestion detail | Not currently used separately — detail is served from the already-fetched list (§7); kept here only if a future direct-link/deep-link use case needs it |

## 5. Backend Endpoints (owned by this page)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/suggestions` | Query: `type`, `categories` (comma-separated), `order` (`new`\|`old`) | `{items: [...], total}` | Reads directly from the Cosmos DB Suggestions collection (`web.md` §5); the `status` (pending/done) filter is applied client-side over the already-fetched set, not a query param, matching the legacy implementation |
| `GET` | `/api/suggestions/{suggestion_id}` | Path: suggestion ID | Single suggestion object | See note in §4 |
| `POST` | `/api/suggestions/{suggestion_id}/respond` | Body: `{mode: "send" \| "done_no_feedback" \| "done_auto_feedback", response_text?: string}` | Updated suggestion object | The one mutating endpoint on this page — see §6 |

## 6. User Interactions & Actions

| Action | Effect |
|---|---|
| Select a suggestion card | Shows its full detail + conversation thread + response form in the right panel |
| Type / category / order / status filter change | Re-fetches or re-filters the list (§5); status filter is client-side only |
| "Clear filters" | Resets all filters to defaults and re-fetches |
| "Send" (with response text) | `POST /api/suggestions/{id}/respond` with `mode: "send"` — pushes a Cosmos DB update **and** a Queue Storage notification so `Bot` DMs the user (`architecture.md` Scenario 3) |
| "Mark done, no feedback" | Same endpoint, `mode: "done_no_feedback"` — marks resolved without notifying `Bot` to send anything |
| "Auto feedback" | Same endpoint, `mode: "done_auto_feedback"` — sends a localized default message; locale resolution logic (user → stored → guild, falling back to `en`) is carried forward unchanged from legacy |
| Refresh button | Re-fetches the current filtered view |

Once a suggestion is marked `responded`, the response form and all three action buttons disable — matches legacy (no "re-open" flow exists).

## 7. State & Refresh Behavior

- No polling and no live updates — list refreshes only on load, filter change, manual refresh, or immediately after a successful response action (to reflect the new status). New incoming suggestions from `/suggest` are not pushed to an already-open page.
- Filters and current selection are client-side-only state, not persisted across reloads.
- The conversation thread (§3) is rendered from whatever's embedded in the suggestion document itself — no separate endpoint, no pagination (assumes a bounded number of entries per ticket).

## 8. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `/api/suggestions` fails | Fetch rejects or non-2xx | List shows an inline error message |
| `/respond` fails | Non-2xx response | Response form re-enables, an inline error status message is shown with the server's error detail if available |
| `/respond` succeeds in Cosmos DB but the Queue Storage notification fails | **Not distinguished today** — per `web.md` §9, this is an inherited gap: the suggestion is saved as responded, but `Bot` may never learn to send the DM, and the UI currently has no way to tell the admin this happened separately from a full failure |

## 9. Dependencies

| Dependency | Used for | Notes |
|---|---|---|
| Azure Cosmos DB | Suggestion reads/updates | Direct `cosmos.py` access, per `web.md` §5 |
| Azure Queue Storage | Notifying `Bot` to send the response DM | Per `architecture.md` Scenario 3; failure handling gap noted in §8 |
| `Bot` (indirect) | Actually delivering the DM to the user, and localization lookup for `done_auto_feedback` | `Web` never talks to `Bot` directly (`web.md` §1) — this is entirely mediated through the Queue Storage event; if `Bot` is offline, the notification sits in the queue until it comes back, per `architecture.md`'s Queue Storage polling model |
| `azure.md`'s `status.py` entry | Reading the shared type/category list (§3) | Second reader of the same document `bot/commands/suggest.md` §9 reads — mediated through Azure only, no direct `Bot` link. Read/write ownership of this document is explicitly undecided (`suggest.md` §14) — not resolved here either |

## 10. Open Items / Future Work

- The "responded but notification failed" ambiguity (§8) is a real gap, not a hypothetical — worth a follow-up decision on whether the response record should track queue-delivery status separately from "admin submitted a response."
- No confirmation step exists before marking a suggestion done (`components.md` §6 flags this as a candidate use for the shared confirmation modal, not yet wired up).
- **This page's own read path for the shared type/category document is undecided** (§3, §9) — whether it's a new small backend endpoint on this page (`GET /api/suggestions/categories`?) or folded into an existing "bot info" endpoint (`components.md` §1's footer already implies one exists for name/version) isn't designed yet. Cross-referenced from `bot/commands/suggest.md` §14, which leaves the same question open on the `Bot` side.
