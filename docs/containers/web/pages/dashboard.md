# Page: Dashboard

## 1. Purpose & Scope

The at-a-glance operational snapshot: current headline stats, a short live window of CPU/memory/latency, and a live-tailing log console. This is the landing point for "is everything okay right now" — deep historical analysis is `pages/performance.md`'s job instead, not duplicated here. All `/api/*` calls (including negotiate) require Entra admin auth (`contracts/web_auth.md`); the static SPA shell is public so login can run.

## 2. Route & Entry Point

| | |
|---|---|
| **Frontend route** | `/dashboard` |
| **Frontend source** | `frontend/src/pages/DashboardPage.tsx` |
| **Nav label** | "Dashboard" |

## 3. Layout & Components

- Header row: page title + a shortcut link to the Webhook page's announcement form + `ConnectionStatus` (`components.md` §2), reporting the Web PubSub live-connection state (§7).
- Metric cards row (uptime, latency, guild count, error count) plus Bot/AI Worker `fresh|stale` badges from the live payload — page-specific, not promoted to `components.md` since nothing else currently reuses a bare "icon + label + value" card.
- Three `TimeSeriesChart` instances (`components.md` §3) in `compact` variant: CPU, memory, latency, each also showing its current value inline.
- Live console section: scrolling log viewer with Pause/Clear controls and an "Auto-refresh: ON/OFF" indicator — page-specific (not in `components.md`, see `components.md` §6 note on why nothing here is promoted yet).

## 4. Data Sources

| Source | Channel | Format | Trigger |
|---|---|---|---|
| This page's own backend (§5) | `GET /api/metrics` | Current snapshot — Table latest + `status.py` for latency/guilds (`contracts/telemetry.md` §4) | On page load, then every 2s (§7) |
| This page's own backend (§5) | `GET /api/metrics/history?minutes=2` | Table-only series for cpu/memory/latency | Same cadence as above |
| This page's own backend (§5) | `GET /api/pubsub/negotiate` | `{ url, expires_at, group }` (`contracts/pubsub_live.md`) | Once, on page mount |
| Azure Web PubSub (direct, browser) | WebSocket | `telemetry_live` batches (`contracts/telemetry.md` §5) | Continuous after connect; reconnect re-negotiates; `seq` dedup |

## 5. Backend Endpoints (owned by this page)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/metrics` | Bearer required | `{cpu, memory: {mb, percent}, latency, uptime, errors, guilds, timestamp}` | Mix Table latest + `status.py` status section — **not** Cosmos guild count (`contracts/telemetry.md` §4) |
| `GET` | `/api/metrics/history` | Query: `minutes` (default 2); Bearer required | `{cpu: [...], memory: [...], latency: [...]}` | Table only |
| `GET` | `/api/pubsub/negotiate` | Bearer required | `{ url, expires_at, group: "dashboard-live" }` | Join/leave only; same Entra admin boundary as all `/api/*` (`contracts/web_auth.md`, `contracts/pubsub_live.md`) |

The legacy `GET /api/logs` (reading a local log file) and `WS /ws/logs` (proxied socket) are **not** carried forward — replaced entirely by the direct Web PubSub subscription (§4, `web.md` §6.1).

## 6. User Interactions & Actions

| Action | Effect |
|---|---|
| Pause button | Stops appending new lines to the console (client-side only; the underlying Web PubSub subscription keeps running so nothing is missed once resumed) |
| Clear button | Empties the console view (client-side only, no backend call) |
| "Send Announcement" link | Navigates to `pages/webhook.md`, no action performed on this page itself |

No mutating (POST/PATCH) actions exist on this page — it is read-only.

## 7. State & Refresh Behavior

- Metric cards + compact charts: polled via `GET /api/metrics` + `GET /api/metrics/history?minutes=2` every 2 seconds, matching the legacy interval — chosen originally to feel "live" without needing push for the numeric cards specifically.
- Console: purely push-driven once the Web PubSub connection is open (§4) — no polling. Auto-scrolls only while the user is already scrolled to the bottom (preserved from legacy's `autoScroll` tracking), pauses auto-scroll if the user scrolls up to read history.
- Each live tick applies canonical `service_liveness`, `logs_dropped`, 50-line, and 65,536-byte semantics from `contracts/telemetry.md` §5. A stale badge remains visible until a later tick marks that service fresh. `logs_dropped > 0` adds one client-side omission notice; omitted lines are not backfilled.
- `ConnectionStatus` reflects the Web PubSub connection specifically, not the `/api/metrics` polling — those are two independent data paths and can be in different states (e.g. charts still updating via polling while the live console shows "Disconnected").
- On Web PubSub disconnect, the frontend should reconnect with a short backoff (legacy used a flat 3s retry) — exact backoff strategy not re-specified here, carried forward as a reasonable default rather than a firm decision.

## 8. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `/api/metrics` or `/api/metrics/history` request fails | Fetch rejects or non-2xx | Cards/charts keep showing their last successful values; no distinct "stale" indicator defined — open item |
| Web PubSub connection drops | WebSocket `close`/`error` event | `ConnectionStatus` flips to "Disconnected"; console stops receiving new lines until reconnect (§7) succeeds |
| Bot or AI Worker heartbeat is stale | `telemetry_live.service_liveness` value is `stale` | Show the corresponding stale badge; do not zero/fabricate metric values. Head determines freshness using the 90-second monotonic threshold (`contracts/telemetry.md` §2.3). |
| `/api/pubsub/negotiate` fails | Non-2xx response | Console never connects at all; per §7 this is indistinguishable from "connected then dropped" unless the UI is given a distinct initial-failure state — not decided |

See `web.md` §9 for the underlying Cosmos/Table/PubSub-unreachable failure modes this page's endpoints depend on — not re-derived here.

## 9. Dependencies

| Dependency | Used for | Notes |
|---|---|---|
| Azure Table Storage | `/api/metrics`, `/api/metrics/history` | `contracts/telemetry.md` |
| Azure Web PubSub | Live console via browser connection | `contracts/pubsub_live.md` — group naming resolved |
| `status.py` / Bot (indirect) | Current `latency` + `guilds` on `/api/metrics` | `contracts/status_document.md` `status` section — **not** Cosmos count |
| Leader `Head` (indirect) | `uptime`, `errors`, cpu/memory history | `contracts/telemetry.md` |

## 10. Open Items / Future Work

- ~~Metric field sources~~ — **resolved (P0.5.2 / P0.6)** via `contracts/telemetry.md` + `contracts/pubsub_live.md`.
- Whether compact charts should also take live PubSub updates vs stay on 2s polling — undecided (not blocking).
- Stale-data indication when `/api/metrics` polling fails (§8) — undecided.
- ~~Negotiate auth~~ — **resolved (P0.7):** `contracts/web_auth.md`.
