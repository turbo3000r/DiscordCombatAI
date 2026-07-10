# Page: <Name>

> **Deviation from `containers/template.md` (intentional):** this template documents one **dashboard page** within the `Web` container — a paired frontend view + the specific backend endpoints it owns — not a standalone service. A page has no environment variables or independent health check of its own; instead it has a UI, a set of data sources, and user-triggered actions, so the sections below are adapted for that instead of copied 1:1 from `containers/template.md`. This mirrors how `bot/commands/*.md` pairs a command's UI with its service logic in one file, and how `ai_worker/graphs/template.md` adapts the same base schema for a different kind of unit. See `pages/dashboard.md` as the filled-out example.

## 1. Purpose & Scope

What this page shows and what an admin can do on it (1–3 sentences). Who it's for — is it public, or does it assume an eventual authenticated admin (per `web.md` §3's open item on auth)? If the page has more than one mode/view (e.g. filtered list + detail panel), name them here briefly.

## 2. Route & Entry Point

| | |
|---|---|
| **Frontend route** | e.g. `/dashboard` |
| **Frontend source** | e.g. `frontend/src/pages/DashboardPage.tsx` |
| **Nav label** | As it appears in the shared nav (`components.md`) |

## 3. Layout & Components

Which shared components from `components.md` this page composes (charts, tables, modals, layout pieces), and which UI is page-specific enough that it doesn't belong in `components.md`. If the page has distinct sub-views (e.g. list + detail), describe the layout briefly — a short prose description or simple diagram, not a full mockup.

## 4. Data Sources

| Source | Channel | Format | Trigger |
|---|---|---|---|
| | | | |

Everything this page reads, whether it's this page's own backend endpoint (§5), a direct Web PubSub subscription (`web.md` §6.1), or something else. If a data source is shared with another page (e.g. the same guild list used by both Guilds and Webhook), say so and point to that page's §5 instead of re-defining the endpoint here.

## 5. Backend Endpoints (owned by this page)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| | | | | |

Only endpoints this page introduces and owns. If this page calls an endpoint defined by another page's doc, reference it — don't redefine it here (same "define once, link elsewhere" convention as `azure.md` §3).

## 6. User Interactions & Actions

What buttons/forms/filters exist and what each one does — especially anything that mutates data (POST/PATCH) rather than just displaying it. For each mutating action, note what happens on success and on failure from the user's point of view (this feeds §8, but describe the UI-level behavior here, not just the wire format already covered in §5).

## 7. State & Refresh Behavior

How this page stays up to date: polling interval, live push via Web PubSub, or manual-refresh-only. Loading and empty states. If the page has meaningful client-side state (filters, selection, pagination) that isn't persisted anywhere, say so explicitly rather than leaving it implied.

## 8. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| | | |

What the user actually sees when a data source (§4) is unreachable or an action (§6) fails. If a failure mode is a known gap inherited from `web.md` (e.g. "no auth" or "Queue Storage failure isn't surfaced distinctly") rather than something newly discovered here, link back to `web.md` §9 instead of re-describing it.

## 9. Dependencies

| Dependency | Used for | Notes |
|---|---|---|

Azure resources (link to `azure.md` §3, don't redefine variables), other pages this one links to or shares data with, and any cross-container dependency this page's data ultimately relies on (e.g. a field that only exists if `Bot` writes it somewhere — link to `web.md` §6.2 if it's one of the already-tracked gaps there, rather than re-flagging it independently).

## 10. Open Items / Future Work

Explicitly undecided things specific to this page. Don't restate `web.md`'s container-wide open items (§13 there) unless this page adds a page-specific angle on one of them.
