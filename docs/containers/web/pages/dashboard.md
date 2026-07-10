# Page: Dashboard

## 1. Purpose & Scope

The at-a-glance operational snapshot: current headline stats, a short live window of CPU/memory/latency, and a live-tailing log console. This is the landing point for "is everything okay right now" — deep historical analysis is `pages/performance.md`'s job instead, not duplicated here. Public/admin distinction: same as every other page, no auth exists yet (`web.md` §3, §13).

## 2. Route & Entry Point

| | |
|---|---|
| **Frontend route** | `/dashboard` |
| **Frontend source** | `frontend/src/pages/DashboardPage.tsx` |
| **Nav label** | "Dashboard" |

## 3. Layout & Components

- Header row: page title + a shortcut link to the Webhook page's announcement form + `ConnectionStatus` (`components.md` §2), reporting the Web PubSub live-connection state (§7).
- Metric cards row (uptime, latency, guild count, error count) — page-specific, not promoted to `components.md` since nothing else currently reuses a bare "icon + label + value" card.
- Three `TimeSeriesChart` instances (`components.md` §3) in `compact` variant: CPU, memory, latency, each also showing its current value inline.
- Live console section: scrolling log viewer with Pause/Clear controls and an "Auto-refresh: ON/OFF" indicator — page-specific (not in `components.md`, see `components.md` §6 note on why nothing here is promoted yet).

## 4. Data Sources

| Source | Channel | Format | Trigger |
|---|---|---|---|
| This page's own backend (§5) | `GET /api/metrics` | Current snapshot JSON (see §5) | On page load, then every 2s (§7) |
| This page's own backend (§5) | `GET /api/metrics/history?minutes=2` | Array of `{time, value}` points per metric | Same cadence as above, feeds the three charts |
| This page's own backend (§5) | `GET /api/pubsub/negotiate` | Client access token (`web.md` §6.1) | Once, on page mount |
| Azure Web PubSub (direct) | WebSocket | Live telemetry/log batch, per `head.md` §5/§8 | Continuous, once connected |

## 5. Backend Endpoints (owned by this page)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/metrics` | — | `{cpu, memory: {mb, percent}, latency, uptime, errors, guilds, timestamp}` | Reads current values from the latest Table Storage row(s); see §9 for which fields are actually sourceable today |
| `GET` | `/api/metrics/history` | Query: `minutes` (default 2) | `{cpu: [...], memory: [...], latency: [...]}`, each a `{time, value}[]` array | Backed by Table Storage (`web.md` §5); the same shape is reused by `pages/performance.md` §5 with a larger `minutes` value — don't redefine this shape there |
| `GET` | `/api/pubsub/negotiate` | — | Web PubSub client access token/URL | Shared mechanism, defined once in `web.md` §6.1 — this is the first page to use it; `pages/performance.md` reuses it too if it ever adds live data (currently it doesn't, §7) |

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
- `ConnectionStatus` reflects the Web PubSub connection specifically, not the `/api/metrics` polling — those are two independent data paths and can be in different states (e.g. charts still updating via polling while the live console shows "Disconnected").
- On Web PubSub disconnect, the frontend should reconnect with a short backoff (legacy used a flat 3s retry) — exact backoff strategy not re-specified here, carried forward as a reasonable default rather than a firm decision.

## 8. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `/api/metrics` or `/api/metrics/history` request fails | Fetch rejects or non-2xx | Cards/charts keep showing their last successful values; no distinct "stale" indicator defined — open item |
| Web PubSub connection drops | WebSocket `close`/`error` event | `ConnectionStatus` flips to "Disconnected"; console stops receiving new lines until reconnect (§7) succeeds |
| `/api/pubsub/negotiate` fails | Non-2xx response | Console never connects at all; per §7 this is indistinguishable from "connected then dropped" unless the UI is given a distinct initial-failure state — not decided |

See `web.md` §9 for the underlying Cosmos/Table/PubSub-unreachable failure modes this page's endpoints depend on — not re-derived here.

## 9. Dependencies

| Dependency | Used for | Notes |
|---|---|---|
| Azure Table Storage | `/api/metrics`, `/api/metrics/history` | Via `table.py`, per `azure.md`'s Addition note in §4/§5 |
| Azure Web PubSub | Live console + (optionally) live chart updates | Group-naming open item blocks this entirely — `web.md` §3, `head.md` §5 |
| `Bot` (indirect, resolved) | `latency`, `guilds` count fields on `/api/metrics` | **Both now solvable.** `guilds`: a count of documents in the `GuildConfigs` collection (`contracts/guild_config.md`), computed by this page's own backend — no dependency on `Bot` at all, direct Cosmos DB read. `latency`: **resolved, confirmed decision** — `bot/discord_bot.md` §6.3, `Bot` periodically upserts `{latency_ms, guild_count, updated_at}` into the shared `status.py` cloud document (`azure.md` §2) every `BOT_STATUS_PUSH_INTERVAL_SEC` specifically so `Web` can read it without any Mosquitto access. This page's backend should read `latency` from that document, not fabricate a separate path. |
| `Head` (indirect) | `uptime`, `errors` fields | **Both remain open items** (`web.md` §6.2) — unaffected by this revision's `Bot`-side resolution. "uptime" is ambiguous across multiple nodes, and no error-count metric exists in `head.md`'s Table Storage schema at all today. |

## 10. Open Items / Future Work

- ~~`latency` and `guilds` fields on `/api/metrics` have no confirmed data source~~ — **resolved** (§9): `guilds` is a direct Cosmos DB count; `latency` reads from the `status.py` document `Bot` now writes to (`bot/discord_bot.md` §6.3). `uptime` and `errors` remain unresolved — the endpoint should still ship *without* those two fields (or with explicit `null`s) rather than fabricate placeholder values, until `web.md` §13's underlying `Head`-side gaps are resolved.
- Whether the compact charts (§3) should also receive live updates via Web PubSub (instead of 2s polling, once a connection exists anyway) or deliberately stay on the simpler polling path is undecided — not a functional gap, just an unmade simplification-vs-consistency call.
- Stale-data indication when `/api/metrics` polling fails (§8) is undecided.
