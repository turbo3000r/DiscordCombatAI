# Web: Shared Frontend Components

> **Why this file exists:** `frontend/src/components/` (per `architecture.md`'s file structure) holds React components reused across more than one page. Per the same convention already established for `ai_worker/nodes.md` (shared LangGraph nodes) and `azure.md` (shared env vars): a piece used by ≥2 pages gets one definition here; each page's own doc (`pages/*.md` §3) just references it instead of re-describing it. A component used by exactly one page stays documented in that page's own doc instead — don't pre-emptively list it here just because it's structurally a "component."

---

## 1. App Shell & Navigation

| | |
|---|---|
| **Location** | `components/layout/` |
| **Responsibility** | Top nav bar with a hamburger-triggered dropdown (Home, Dashboard, Guilds, Performance, Suggestions, Webhook) and a shared footer showing bot name/version (from a `bot/info`-style endpoint — see `pages/home.md` §5) and an "Add bot to your server" invite link. |
| **Used by** | Every page. |
| **Notes** | Directly carried over from the legacy design (`navigation.js` + `common.js`) — dropdown-on-click, closes on outside-click or link-click. No behavior change expected, just a React re-implementation. |

---

## 2. Connection Status Indicator

| | |
|---|---|
| **Location** | `components/layout/ConnectionStatus.tsx` |
| **Responsibility** | Small dot + label ("Connected" / "Disconnected") reflecting whether the page's live data source is currently reachable. |
| **Used by** | `pages/dashboard.md` (Web PubSub connection state), `pages/performance.md` and `pages/guilds.md` (last poll/fetch success or failure) |
| **Notes** | What "connected" means differs per page (a live socket vs. "did the last fetch succeed") — each page's own §7 (State & Refresh Behavior) defines which one it's reporting. This component only renders the boolean, it doesn't decide it. |

---

## 3. Time-Series Chart

| | |
|---|---|
| **Location** | `components/charts/TimeSeriesChart.tsx` |
| **Responsibility** | Wraps a charting library (Chart.js in the legacy implementation; carrying the same choice forward unless reopened) to render one metric (CPU %, memory MB, latency ms) as a line chart over time. |
| **Props (conceptual)** | `data: {time, value}[]`, `unit`, `variant: "compact" | "detailed"` — `compact` (no time axis, short window, used by Dashboard) vs. `detailed` (time axis, decimation for large ranges, used by Performance). |
| **Used by** | `pages/dashboard.md` (three compact instances: CPU/memory/latency, ~2 min window) and `pages/performance.md` (three detailed instances, selectable window up to 24h, plus current/average/peak stat readout) |
| **Notes** | Both pages feed this component from the same backend metrics-history shape (`web.md` §5, `pages/performance.md` §5) — only the query range and rendering `variant` differ. Decimation for large ranges (legacy used Chart.js's `lttb` algorithm at 500 samples) is a `detailed`-variant concern, not something `compact` needs. |

---

## 4. Split Panel List (list + detail)

| | |
|---|---|
| **Location** | `components/layout/SplitPanelList.tsx` |
| **Responsibility** | Two-column layout: a scrollable, selectable list/card panel on the left, a detail panel on the right showing whatever is selected, with a draggable resizer between them (min/max width clamped, e.g. 15–50%). |
| **Used by** | `pages/guilds.md` (guild list → guild detail) and `pages/suggestions.md` (suggestion list → suggestion detail + response form) |
| **Notes** | The list-item rendering (a guild card vs. a suggestion card) and the detail panel's contents are page-specific and passed in as children/render-props — this component only owns the split layout, selection highlighting, and the resizer behavior, carried over from the legacy `guilds.js` resizer implementation. |

---

## 5. Status Badge / Pill

| | |
|---|---|
| **Location** | `components/tables/StatusBadge.tsx` |
| **Responsibility** | Small colored pill for a short status label (e.g. "Pending" / "✓ Done", "✅ Webhook configured" / "⚠️ Not configured"). |
| **Used by** | `pages/suggestions.md` (response status), `pages/guilds.md` (webhook-configured indicator) |
| **Notes** | Purely presentational — color/label is passed in by the caller, no internal logic. |

---

## 6. Confirmation Modal

| | |
|---|---|
| **Location** | `components/modals/` |
| **Responsibility** | Generic "are you sure?" confirmation dialog. |
| **Used by** | **None yet** — no page's current design (carried over from legacy) actually gates a mutating action behind a confirmation step (e.g. marking a suggestion done, sending an announcement to *all* guilds are both currently one-click). |
| **Notes** | Listed here as an available shared building block, not because it's in active use — flagged as a candidate if any page's Open Items (`pages/webhook.md` §10 is the most likely candidate, given "send to ALL guilds" is a wide-blast-radius action) later decide a confirmation step is worth adding. |

---

## 7. Open Items

- Exact charting library choice (Chart.js vs. a React-native charting library, e.g. `recharts`/`visx`) isn't re-confirmed here — legacy used Chart.js directly against the DOM, which doesn't map 1:1 onto a React component lifecycle as cleanly as a React-first charting library would. Flagged for whoever implements `TimeSeriesChart` (§3), not decided in this doc.
- No design system / shared style tokens (color palette, spacing scale) are defined yet beyond what legacy's `static/css/styles.css` established informally (dark theme, Discord-blurple accents). Whether that gets formalized (CSS variables, a small theme file) or just ported as-is is undecided.
