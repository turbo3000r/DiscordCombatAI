# Service: Web

> **Structural note, mirroring `bot/`'s split (per `docs/Readme.md`):** `Web` is the second-largest, most user-facing container after `Bot`, and gets the same treatment — this file covers only what's shared across the whole container (integration model, build/deploy, Azure deps, env vars). Frontend UI pieces reused across more than one page live in `components.md` (analogous to `bot/visuals.md`). Each dashboard page gets its own file under `pages/` pairing its UI with the specific backend endpoints it owns (analogous to `bot/commands/*.md` pairing a command's UI with its service logic) — see `pages/template.md` for that schema. Don't duplicate a page's endpoint contract here; don't duplicate this file's build/integration decisions inside a page doc.

---

## 1. Responsibility

`Web` is the independent admin/monitoring dashboard for the whole system (per `architecture.md`'s Container Breakdown): a FastAPI backend serving a compiled React frontend, packaged as a **single container**, deployed independently of the local Docker Compose node (`architecture.md`: "excluded from `docker-compose.yml` by design").

It has **no direct network access** to `Bot`, `Head`, `RabbitMQ`, or `Mosquitto` — every capability it has is mediated entirely through Azure (`Cosmos DB`, `Queue Storage`, `Web PubSub`, `Table Storage`; see §10). This is the literal meaning of "standalone" for this container: it is one deployable unit (frontend + backend together), decoupled from the local cluster's lifecycle, not two independently deployable halves.

`Web` gives administrators five things: a live operational snapshot (Dashboard), deep historical performance analysis (Performance), guild visibility (Guilds), a suggestion/ticket review-and-respond workflow (Suggestions), and a way to broadcast announcements/release notes to guild-configured Discord webhooks (Webhook). A sixth, minimal Home page exists as a landing/entry point.

---

## 2. File Structure

See `architecture.md`'s Project File Structure for the authoritative on-disk tree (`src/web/`). Summary of the decisions that shape it:

- **Single container, single Dockerfile, multi-stage build.** Stage 1 (Node) runs `npm run build` under `frontend/`, producing static assets in `frontend/dist/`. Stage 2 (Python) copies `backend/` plus that `dist/` output, and runs FastAPI/Uvicorn as the only process. There is no separate frontend server, no reverse proxy, and no CORS configuration needed in production — the frontend and API share one origin.
- **Frontend stack: React + TypeScript + Vite.** This corrects an earlier draft of `architecture.md`'s tree, which sketched plain per-page `.ts` files with no framework (closer to the legacy vanilla-JS dashboard's style). Given the number of interactive, stateful pages (live charts, filterable tables, a multi-action ticket workflow), a component framework was chosen deliberately over continuing that pattern — see `components.md` for how shared UI pieces are organized as a result.
- **Dev vs. prod frontend serving.** In production, the backend serves the pre-built `dist/`. In `docker-compose.dev.yml`, the frontend can instead run Vite's own dev server (hot module reload) proxying API calls to the backend — this is a local convenience only, never how the shipped image runs.
- **`backend/routes/` maps roughly one file per page** (`dashboard.py`, `guilds.py`, `suggestions.py`, `webhook.py`), matching the legacy router split — see each page's own doc under `pages/` for its exact endpoints.

---

## 3. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `WEB_HOST` | No | `0.0.0.0` | Bind host for the FastAPI/Uvicorn process. |
| `WEB_PORT` | No | `8000` | Bind port. |
| `WEB_METRICS_DEFAULT_RANGE_MIN` | No | `1440` | Default look-back window (minutes) for the historical metrics endpoint when a page doesn't specify one (`pages/performance.md` §4). |

> **Azure configuration lives in `azure.md`, not here.** Per Section 10, `Web` depends on **Cosmos DB**, **Queue Storage**, **Web PubSub**, and **Table Storage**. All Azure authentication and endpoint variables are defined exactly once in `azure.md` §3 — this table only lists variables owned by `Web` itself.
>
> **No authentication/authorization variables exist yet — explicit open item, deliberately deferred, not an oversight.** The legacy home page literally ships an "Auth Placeholder" that was never implemented. Every admin-facing action documented in `pages/suggestions.md` and `pages/webhook.md` is, as of this revision, reachable by anyone who can reach the container's port. **Confirmed this revision (project owner):** this stays deferred for now, but network exposure is explicitly a deployment-time decision, never assumed or hardcoded either way in this doc or in any deployment manifest — `Web` is not documented as "public" or "private," it is documented as "unauthenticated at the application layer, whoever operates the deployment decides what network reaches it." This is being carried forward *as a known gap*, not re-decided here — see §9 and §12.
>
> **Web PubSub group name(s) for live telemetry — undecided, blocks §6.** `azure.md` §5 and `head.md` §5 both flag that `Head`'s telemetry/log broadcast group(s) have no documented stable name. `Web` needs that name to request a correctly-scoped client access token (§6). No `WEB_*` env var is defined for this yet because it isn't clear whether the value even belongs to `Web` (vs. being a fixed constant shared with `Head`) — resolve the naming question first (`head.md`'s note), then decide where the constant lives.

---

## 4. Inbound Communication

| Source | Channel | Format | Trigger |
|---|---|---|---|
| Browser (own frontend) | HTTPS | REST API calls (JSON), see each `pages/*.md` §5 | User loads or interacts with a page |
| Browser (own frontend) | HTTPS | `GET /api/pubsub/negotiate` | Dashboard/Performance page mount, before opening the direct Web PubSub connection (§6) |

`Web` has no inbound channel from `Bot`, `Head`, `RabbitMQ`, or `Mosquitto` — by design (§1). Suggestions arrive indirectly: `Bot` writes them to Cosmos DB (`architecture.md` Scenario 3), and `Web` simply reads that same collection — there is no message `Web` "receives" from `Bot` directly.

---

## 5. Outbound Communication

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| Azure Cosmos DB | HTTPS (`cosmos.py`) | Guild config reads; suggestion reads/updates | Guilds/Suggestions page loads and actions (`pages/guilds.md`, `pages/suggestions.md`) |
| Azure Queue Storage | HTTPS (`queue.py`) | Suggestion-response notification event | Admin responds to or closes a suggestion (`architecture.md` Scenario 3; `pages/suggestions.md` §5) |
| Azure Table Storage | HTTPS (`table.py`) | Historical metrics query | Dashboard/Performance page load + periodic refresh (`pages/dashboard.md`, `pages/performance.md`) |
| Azure Web PubSub | HTTPS (`pubsub.py`) | `get_client_access_token` (negotiate) | Dashboard/Performance page mount (§6) |
| Discord Webhook URLs (per-guild, external — **not** an Azure resource) | HTTPS, direct POST | Announcement / release-note embed payloads | Admin submits the Webhook page's form (`pages/webhook.md` §5) |
| Browser (own frontend) | HTTPS response | Compiled static assets + REST JSON | Every request |

Sending directly to a guild's Discord webhook URL requires no bot token and no gateway connection — it's a plain HTTPS POST to a public Discord endpoint — which is why this one outbound path bypasses Azure and `Bot` entirely, exactly as it did in the legacy design, without breaking the "no direct connection to `Bot`" rule.

---

## 6. Internal Logic

### 6.1 Frontend/backend integration model

The backend is not a passive static file host — it serves two genuinely different kinds of traffic on the same origin:

1. **The compiled app shell and assets** (`GET /`, `GET /assets/*`) — served via FastAPI's `StaticFiles`, no logic involved.
2. **The REST API** (`/api/*`) — one router per page, each documented in its own `pages/*.md`.

For **live data** (Dashboard's metric graphs and log console), the browser does **not** open a WebSocket to this backend. Instead:

1. The frontend calls `GET /api/pubsub/negotiate` on page mount.
2. The backend calls `pubsub.py`'s `get_client_access_token` (`azure.md` §5) and returns a short-lived, group-scoped token/URL to the browser.
3. The browser opens a WebSocket **directly to Azure Web PubSub** using that token and joins the same broadcast group `Head` is already streaming into (`head.md` §5, §8).

This was chosen over proxying the connection through this backend (the legacy `/ws/logs` pattern) specifically so the backend stays stateless with respect to live connections — it never holds open WebSocket state per browser tab, it only issues short-lived tokens. The tradeoff: this backend now depends on a Web PubSub group-naming answer it doesn't own (§3, `head.md`'s open item) before this can actually be wired up.

For **historical data** (initial chart load, the Performance page's full range), the frontend calls this backend's own REST endpoints, which query Table Storage directly (no PubSub involved) — matching `architecture.md`'s Scenario 4 ("the browser fetches the last 24h of history via a static API call").

### 6.2 What legacy data this design can and can't reproduce

The legacy dashboard read several fields directly off an in-process `discord.py` bot object (`bot.latency`, `len(bot.guilds)`, `bot.guilds_data`). A standalone `Web` has no such object. Rather than inventing a new channel to smuggle that access back in, each field was evaluated on its own:

| Legacy field | New-architecture source | Status |
|---|---|---|
| Guild count | Count of documents in Cosmos DB's Guild Configs collection | **Solvable today** — `Web` already has direct Cosmos DB access; no `Bot` involvement needed |
| Guild metadata (name, icon, member count) | `Bot` writes these into the same Cosmos DB guild-config document on `on_guild_join`/`on_guild_update`/a periodic reconciliation sweep | **Resolved** — confirmed decision in `bot/discord_bot.md` §6.2, schema in `contracts/guild_config.md`; see `pages/guilds.md` §9 |
| Bot Discord gateway latency | `Bot` periodically writes a status snapshot (extending the `bot.json`-style document `azure.md`'s `services/status.py` already describes) that `Web` reads directly | **Resolved** — confirmed decision in `bot/discord_bot.md` §6.3 (`Bot`'s Mosquitto heartbeat payload is mirrored into `status.py` every `BOT_STATUS_PUSH_INTERVAL_SEC`); see `pages/dashboard.md` §9 |
| CPU/RAM/uptime | Already flows to Azure Table Storage via `Head` (`head.md` §8) | **Solvable today**, with one caveat below |
| Error count | Legacy parsed a local `Errors.log` file directly | **Open item** — no metric like this exists in `head.md`'s Table Storage schema today; would need `Head` (or whichever service detects the error) to start emitting it as a counted metric |
| Live log console | Legacy tailed a local file + in-process WebSocket | **Solvable today** as *live-only* (§6.1) — a historical backlog on page load would additionally require Blob Storage access, deliberately deferred (`azure.md`'s Addition note, §5) |

**The CPU/RAM/uptime caveat:** `Head` (and therefore its metrics) exists per-node, and multiple nodes can run simultaneously (`architecture.md`'s "Multiple LOCAL NODE setups" note). Legacy's single-process model made "the bot's CPU usage" unambiguous; in this architecture it isn't — is the Dashboard showing the current leader's node, a specific node, or an aggregate across nodes? **Not decided anywhere** — flagged as an open item (§12) rather than silently picking one.

---

## 7. Logging

- Same shared structured format as every other service (`architecture.md`'s Mosquitto section):
  ```
  [%time%][%level%][web][%file/module%]<tags>: [%message%]
  ```
- **Open item:** `Web` is not on the local Docker Compose network and has no Mosquitto broker to publish to (§1, §10) — so unlike every other container, it has no path into the centralized log-aggregation pipeline `Head` owns (`head.md` §7). Where `Web`'s own logs go (stdout only, for `docker logs`? A direct Blob Storage append, bypassing `Head` entirely?) is not decided anywhere. Flagged, not resolved.
- **Sensitive data exclusion:** suggestion message bodies and any admin-entered response text may contain arbitrary user-submitted content — treat the same as `ai_worker.md` §7 treats prompt content: safe to log at `DEBUG`, not at `INFO` or above.

---

## 8. Metrics

`Web` does not currently have its own self-reported metrics (request counts, error rates), and — per §7 — has no established path to publish any if it did (no Mosquitto access, no confirmed direct-to-Azure metrics convention for a non-cluster service). Flagged as an open item rather than assigned a destination; not blocking for the core dashboard functionality itself, which displays *other services'* metrics rather than its own.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Cosmos DB unreachable | Exception from `cosmos.py` call | Surfaced to the calling page; per-page doc specifies the resulting UI state (e.g. `pages/guilds.md` §8, `pages/suggestions.md` §8). No retry/fallback defined at this layer beyond what `azure.md` §9 already states generically. |
| Queue Storage unreachable | Exception from `queue.py` call | Per `azure.md` §9, no documented fallback exists project-wide for this — inherited gap, not `Web`-specific. Practical effect: a suggestion response is saved to Cosmos DB but `Bot` is never notified to send the DM; whether the UI should surface this distinction to the admin is undecided (`pages/suggestions.md` §8). |
| Table Storage unreachable | Exception from `table.py` call | Dashboard/Performance charts fail to load historical data; live data via Web PubSub (§6.1) is unaffected since it's a separate path. |
| Web PubSub unreachable, or negotiate fails | Exception from `pubsub.py` call, or browser WebSocket connect failure | Dashboard/Performance falls back to historical-only view (no live updates); exact UI treatment (banner? silent?) left to each page doc. |
| Discord webhook POST fails for a given guild | Non-2xx response from Discord | Per-guild failure, doesn't block sending to other selected guilds — mirrors legacy's own per-guild result tracking (`pages/webhook.md` §5/§8). |
| No auth layer (§3) | N/A — not a failure, a standing gap | Anyone reaching the container's port can perform admin actions today. Not mitigated at this layer. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| Azure Cosmos DB | Guild config reads, suggestion CRUD | See §9. |
| Azure Queue Storage | Suggestion-response notification to `Bot` | See §9. |
| Azure Web PubSub | Live dashboard data, via a direct browser connection negotiated by this backend | See §9; also blocked entirely until the group-naming open item (§3, `head.md`) is resolved. |
| Azure Table Storage | Historical metrics (Dashboard/Performance) | See §9. |
| `azure.md` | Client construction/auth for all four resources above | Internal documentation dependency, not a runtime one. |
| Discord Webhook URLs (per-guild) | Announcements/updates | External to Azure entirely; see §9. |
| **Not a dependency (explicit):** `Bot`, `Head`, `RabbitMQ`, `Mosquitto` | — | Confirmed by design (§1) — this is what "standalone" means for this container. |

---

## 11. Health Check

**Not yet specified.** Unlike `Head` (§11 there) or `Launcher`, no health endpoint contract exists for `Web` in any doc — and since `Web` isn't polled by `Launcher` (it isn't part of the local Compose stack `Launcher` manages, §1), there's no existing consumer forcing this to be defined yet. A minimal `GET /api/health` (present in the legacy implementation) is a reasonable placeholder, but what "healthy" should mean here (just "process responds," vs. also checking Cosmos/Table/Queue reachability) is undecided — flagged as an open item (§12).

---

## 12. Versioning & Update Behavior

- `Web` shares the coordinated version tag with `Bot`, `Head`, and `AI Worker` (per `Launcher.md` §12) — it does not version independently.
- **Deployment is not driven by `Launcher`.** Per `architecture.md`, `Web` is excluded from `docker-compose.yml` and deployed independently (its own hosting target, not defined in any doc yet). How a new version of `Web` actually gets rolled out — and whether it needs the same drain/verify/rollback discipline `Launcher` gives the local cluster — is an open item, not addressed by any existing doc.
- No in-place state to preserve across restarts — `Web` holds no local persistent state of its own (all state lives in Cosmos DB / Table Storage), so a fresh container instance starts cold with no migration concerns.

---

## 13. Open Items / Future Work

*(Additive section, same convention `ai_worker.md` uses — this doc surfaced enough open questions to warrant collecting them here rather than only inline.)*

- **Web PubSub telemetry/log group naming** — blocks the entire live-data integration (§3, §6.1; also flagged in `azure.md` §5 and `head.md` §5).
- **No authentication/authorization exists** for any admin action (§3, §9) — carried forward from legacy's unfinished "Auth Placeholder," not re-decided here.
- ~~Bot-sourced dashboard fields (gateway latency, per-guild live metadata) have no defined write path from `Bot` yet~~ — **resolved** (§6.2): `bot/discord_bot.md` §6.2/§6.3 and `contracts/guild_config.md` now define both write paths.
- **Error-count metric** has no defined source anywhere in the new architecture (§6.2) — unaffected by the `Bot`-side resolution above, this one is still `Head`'s gap to close.
- **Multi-node metrics ambiguity** — which node's CPU/RAM/uptime the Dashboard shows is undecided (§6.2).
- **No logging path off this container** — `Web` can't reach `Head`'s aggregation pipeline (§7).
- **No metrics path for `Web`'s own operational health** (§8).
- **No health check contract defined** (§11).
- **`Web`'s own deployment/rollout mechanism** is undefined — it's the one service `Launcher` doesn't manage (§12).
- **Historical log replay** for the Dashboard console was deliberately deferred rather than designed in, to avoid taking on a Blob Storage dependency before it's justified (§6.2, `azure.md` §5 Addition note).
