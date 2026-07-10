# Page: Performance

## 1. Purpose & Scope

Deep historical analysis of the same three metrics the Dashboard shows live (CPU, memory, latency), over a selectable window up to 24 hours, with current/average/peak readouts per metric. This page intentionally does not duplicate Dashboard's live console or headline metric cards (`pages/dashboard.md` §1) — it exists purely for trend analysis over a longer horizon.

## 2. Route & Entry Point

| | |
|---|---|
| **Frontend route** | `/performance` |
| **Frontend source** | `frontend/src/pages/PerformancePage.tsx` |
| **Nav label** | "Performance" |

## 3. Layout & Components

- Header row: page title, a time-range `<select>` (Last Hour / 3h / 6h / 12h / 24h), and `ConnectionStatus` (`components.md` §2) reflecting the last history fetch's success/failure.
- Three `TimeSeriesChart` instances (`components.md` §3) in `detailed` variant — full time axis, decimated for large ranges, each with a current/average/peak stat row above the chart itself.

## 4. Data Sources

| Source | Channel | Format | Trigger |
|---|---|---|---|
| `pages/dashboard.md` §5's `/api/metrics/history` endpoint | HTTPS `GET` | `{cpu, memory, latency}` arrays of `{time, value}` | On page load, on time-range change, and every 10s thereafter (§7) |

This page introduces no backend endpoints of its own — see §5.

## 5. Backend Endpoints (owned by this page)

None. This page is a second consumer of `/api/metrics/history`, defined once in `pages/dashboard.md` §5, with a larger `minutes` value driven by the time-range selector. Do not add a second endpoint here.

## 6. User Interactions & Actions

| Action | Effect |
|---|---|
| Time-range selector | Re-fetches `/api/metrics/history?minutes=<N>` with the newly selected range and re-renders all three charts + stat rows |

No mutating actions exist on this page.

## 7. State & Refresh Behavior

- Polled every 10 seconds (slower than Dashboard's 2s, matching legacy's own reasoning: this page is for trend-watching, not moment-to-moment monitoring).
- Purely poll-driven — **no Web PubSub subscription**, unlike Dashboard. This was a deliberate scope decision (`web.md` §6.1's negotiate mechanism is available to this page too if that ever changes) rather than an oversight: nothing on this page needs sub-10-second freshness.
- Selected time range is client-side-only UI state, reset to the 24h default on page reload — not persisted anywhere.

## 8. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `/api/metrics/history` request fails | Fetch rejects or non-2xx | `ConnectionStatus` flips to "Disconnected"; charts keep showing their last successfully loaded data until the next successful poll |

Underlying Table Storage failure modes are defined once in `web.md` §9 — not re-derived here.

## 9. Dependencies

| Dependency | Used for | Notes |
|---|---|---|
| Azure Table Storage | `/api/metrics/history` (shared with `pages/dashboard.md`) | Same dependency, same endpoint — see `pages/dashboard.md` §9, not re-described here |

The `latency` series has the same unresolved-source caveat as Dashboard's (`pages/dashboard.md` §9) — not re-flagged separately here since it's the exact same underlying gap, just viewed over a longer window.

## 10. Open Items / Future Work

- None specific to this page beyond what `pages/dashboard.md` §10 and `web.md` §13 already track — this page is a thin second view over the same data source.
