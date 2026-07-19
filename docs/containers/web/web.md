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
| `WEB_ENTRA_TENANT_ID` | Yes | — | Entra tenant ID for admin login (`contracts/web_auth.md` §3). |
| `WEB_ENTRA_CLIENT_ID` | Yes | — | SPA / app registration client ID (public; not a secret). |
| `WEB_ENTRA_API_AUDIENCE` | Yes | — | Expected JWT `aud` (typically `api://{WEB_ENTRA_CLIENT_ID}`). |
| `WEB_ENTRA_ADMIN_GROUP_ID` | Yes | — | Object ID of the Entra security group authorized as Web admins. |
| `WEB_ENTRA_AUTHORITY` | No | `https://login.microsoftonline.com/{WEB_ENTRA_TENANT_ID}` | Authority URL override. |
| `WEB_WEBHOOK_ALL_COOLDOWN_SEC` | No | `60` | Per-admin cooldown between successful ALL-guild webhook broadcasts. |
| `WEB_WEBHOOK_SELECTED_RATE_PER_MIN` | No | `10` | Per-admin rate limit for SELECTED webhook sends. |
| `WEB_ADMIN_AUDIT_BLOB_CONTAINER` | No | `admin-audit` | Blob container for durable webhook broadcast audit objects. |

> **Azure configuration lives in `azure.md`, not here.** Per Section 10, `Web` depends on **Cosmos DB**, **Queue Storage**, **Web PubSub**, **Table Storage**, and **Blob Storage** (status document + admin audit). All Azure **Service Principal** authentication and endpoint variables are defined exactly once in `azure.md` §3 — this table only lists variables owned by `Web` itself. **Human admin auth** (`WEB_ENTRA_*`) is distinct from `WEB_AZURE_CLIENT_*` — see `contracts/web_auth.md` §10.
>
> **Authentication — resolved (P0.7):** Microsoft Entra ID (MSAL.js PKCE + Bearer JWT + admin group). Canonical contract: `contracts/web_auth.md`. All `/api/*` require a valid Bearer and admin-group membership; static SPA shell remains public. Unauthenticated → **401**; authenticated non-admin → **403**.
>
> **Web PubSub dashboard group — resolved (P0.6):** `HEAD_PUBSUB_DASHBOARD_GROUP` default `dashboard-live` (shared constant with Head; no separate `WEB_*` override required). Negotiate returns join/leave-only tokens for that group only, under the same Entra boundary (`contracts/pubsub_live.md` §4).


---

## 4. Inbound Communication

| Source | Channel | Format | Trigger |
|---|---|---|---|
| Browser (own frontend) | HTTPS | REST API calls (JSON) with `Authorization: Bearer <access_token>`, see each `pages/*.md` §5 | User loads or interacts with a page (after MSAL login) |
| Browser (own frontend) | HTTPS | `GET /api/pubsub/negotiate` (same Bearer) | Dashboard/Performance page mount, before opening the direct Web PubSub connection (§6) |

`Web` has no inbound channel from `Bot`, `Head`, `RabbitMQ`, or `Mosquitto` — by design (§1). Suggestions arrive indirectly: `Bot` writes them to Cosmos DB (`architecture.md` Scenario 3), and `Web` simply reads that same collection — there is no message `Web` "receives" from `Bot` directly.

---

## 5. Outbound Communication

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| Azure Cosmos DB | HTTPS (`cosmos.py`) | Guild config reads; suggestion reads/updates | Guilds/Suggestions page loads and actions (`pages/guilds.md`, `pages/suggestions.md`) |
| Azure Queue Storage | HTTPS (`queue.py`) | Suggestion-response notification event | Admin responds to or closes a suggestion (`architecture.md` Scenario 3; `pages/suggestions.md` §5) |
| Azure Table Storage | HTTPS (`table.py`) | Historical metrics query | Dashboard/Performance page load + periodic refresh (`pages/dashboard.md`, `pages/performance.md`) |
| Azure Web PubSub | HTTPS (`pubsub.py`) | `get_client_access_token` (negotiate) | Dashboard/Performance page mount (§6) |
| Azure Blob Storage | HTTPS (`blob.py`) | Status document identity/catalog RMW; webhook admin audit writes | Home/Suggestions catalog edits; webhook send/update (`contracts/web_auth.md` §7) |
| Discord Webhook URLs (per-guild, external — **not** an Azure resource) | HTTPS, direct POST (allowlisted hosts only, no redirects) | Announcement / release-note embed payloads | Admin submits the Webhook page's form (`pages/webhook.md` §5) |
| Browser (own frontend) | HTTPS response | Compiled static assets + REST JSON | Every request |

Sending directly to a guild's Discord webhook URL requires no bot token and no gateway connection — it's a plain HTTPS POST to a public Discord endpoint — which is why this one outbound path bypasses Azure and `Bot` entirely, exactly as it did in the legacy design, without breaking the "no direct connection to `Bot`" rule.

---

## 6. Internal Logic

### 6.1 Frontend/backend integration model

The backend is not a passive static file host — it serves two genuinely different kinds of traffic on the same origin:

1. **The compiled app shell and assets** (`GET /`, `GET /assets/*`, other non-API SPA routes) — served via FastAPI's `StaticFiles`, **public** so the SPA can load and redirect to Entra login (`contracts/web_auth.md` §5).
2. **The REST API** (`/api/*`) — one router per page, each documented in its own `pages/*.md`. **All `/api/*` require** a valid Entra Bearer token **and** admin-group membership (§6.2).

For **live data** (Dashboard's metric graphs and log console), the browser does **not** open a WebSocket to this backend. Instead:

1. The frontend calls `GET /api/pubsub/negotiate` on page mount (with Bearer).
2. The backend calls `pubsub.py`'s `get_client_access_token` (`azure.md` §5) scoped to `dashboard-live` with **join/leave only** (no send), TTL default 60 minutes, PubSub `user id` = Entra `oid` (`contracts/pubsub_live.md` §4), and returns `{ url, expires_at, group }`.
3. The **browser** opens a WebSocket **directly to Azure Web PubSub** and joins `dashboard-live`. Leader `Head` always streams while leader — no listener detection. `Web` itself never listens to PubSub.

Historical charts use REST → Table Storage (`contracts/telemetry.md` §3). Reconnect: re-negotiate; use `seq` to drop duplicates; no live backfill.

### 6.2 Authentication middleware (Entra ID)

Canonical: `contracts/web_auth.md`. Summary for implementers:

1. MSAL.js (Authorization Code + PKCE) in the browser; attach `Authorization: Bearer <access_token>` to every `/api/*` call.
2. FastAPI validates JWT (issuer, audience, JWKS signature, `tid`, `exp`/`nbf`) then requires `WEB_ENTRA_ADMIN_GROUP_ID ∈ token.groups`.
3. **401** → frontend triggers MSAL login; **403** → “not authorized” page (signed in, not admin).
4. No cookie session / no BFF / **no CSRF token** for v1 (Bearer-only).
5. Redact secrets per `web_auth.md` §6; webhook SSRF allowlist + broadcast controls per §7.

### 6.3 What legacy data this design can and can't reproduce

The legacy dashboard read several fields directly off an in-process `discord.py` bot object (`bot.latency`, `len(bot.guilds)`, `bot.guilds_data`). A standalone `Web` has no such object. Rather than inventing a new channel to smuggle that access back in, each field was evaluated on its own:

| Legacy field | New-architecture source | Status |
|---|---|---|
| Guild count (current) | `status.py` `status.guild_count` from Bot push — **not** Cosmos document count (soft-delete pollution) | **Resolved** — `contracts/telemetry.md` §1, `contracts/status_document.md` |
| Guild metadata (name, icon, member count) | Cosmos `GuildConfigs` via `contracts/guild_config.md` | **Resolved** |
| Bot Discord gateway latency (current) | `status.py` `status.latency_ms` | **Resolved** |
| CPU/RAM/uptime | Leader Head → Table (`contracts/telemetry.md`); `uptime` = leader Head process uptime | **Resolved** |
| Error count | Leader Head `errors_in_window` (ERROR log lines per batch window, reset each flush) | **Resolved** |
| Live log console | Browser ← PubSub `dashboard-live` (`contracts/pubsub_live.md`); live-only, no Blob replay in v1 | **Resolved** |

**Multi-node:** only the leader uploads telemetry; Dashboard shows that leader’s node (`contracts/telemetry.md` §1), falling back to the most recently written partition. There is no multi-node picker in v1.

---

## 7. Logging

- Same shared structured format as every other service (`architecture.md`'s Mosquitto section):
  ```
  [%time%][%level%][web][%file/module%]<tags>: [%message%]
  ```
- **Open item:** `Web` is not on the local Docker Compose network and has no Mosquitto broker to publish to (§1, §10) — so unlike every other container, it has no path into the centralized log-aggregation pipeline `Head` owns (`head.md` §7). Where `Web`'s own logs go (stdout only, for `docker logs`? A direct Blob Storage append, bypassing `Head` entirely?) is not decided anywhere. Flagged, not resolved.
- **Sensitive data exclusion:** suggestion message bodies and any admin-entered response text may contain arbitrary user-submitted content — treat the same as `ai_worker.md` §7 treats prompt content: safe to log at `DEBUG`, not at `INFO` or above. Never log Bearer tokens, guild `api_key`, raw `webhook_url`, Azure secrets, or PubSub connection strings (`contracts/web_auth.md` §6). Admin mutation audit fields (`acted_by_oid`, etc.) are safe at INFO.

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
| Missing/invalid Bearer on `/api/*` | Auth middleware | **401** — frontend triggers MSAL login (`contracts/web_auth.md` §4). |
| Valid Bearer but not in admin group | Auth middleware | **403** — frontend shows “not authorized” page. |
| Webhook URL fails allowlist / SSRF checks | Validation before Discord POST | Reject that guild’s send; do not follow redirects (`contracts/web_auth.md` §7). |
| ALL-broadcast cooldown / SELECTED rate exceeded | Rate limiter keyed by Entra `oid` | **429** (or equivalent) with clear error; no Discord POST. |
| Duplicate `Idempotency-Key` on webhook send/update | Idempotency store | Return prior result; do not re-POST Discord. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| Azure Cosmos DB | Guild config reads, suggestion CRUD | See §9. |
| Azure Queue Storage | Suggestion-response notification to `Bot` | See §9. |
| Azure Web PubSub | Negotiate-only; browser connects to `dashboard-live` | See §9; `contracts/pubsub_live.md`. |
| Azure Table Storage | Historical metrics (Dashboard/Performance) | See §9; `contracts/telemetry.md`. |
| Azure Blob Storage | Status document identity/catalog RMW; webhook admin audit blobs | `contracts/status_document.md`; `contracts/web_auth.md` §7. |
| `azure.md` | Client construction/auth for Azure resources above | Internal documentation dependency, not a runtime one. Distinct from Entra human auth (`contracts/web_auth.md` §10). |
| Microsoft Entra ID | Human admin login + JWT validation | `contracts/web_auth.md` — required for all `/api/*`. |
| Discord Webhook URLs (per-guild) | Announcements/updates | External to Azure entirely; allowlisted hosts only — see §9 / `web_auth.md` §7. |
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

- ~~**Web PubSub telemetry/log group naming**~~ — **resolved (P0.6):** `dashboard-live` / `contracts/pubsub_live.md`.
- ~~**No authentication/authorization**~~ — **resolved (P0.7):** Entra ID + admin group; `contracts/web_auth.md`. Group-claim overage Graph fallback remains P2.
- ~~Bot-sourced dashboard fields (gateway latency, per-guild live metadata) have no defined write path from `Bot` yet~~ — **resolved** (§6.3): `bot/discord_bot.md` §6.2/§6.3 and `contracts/guild_config.md` now define both write paths.
- ~~**Error-count metric** / **multi-node metrics ambiguity**~~ — **resolved** at contract level (`contracts/telemetry.md`: leader-only; `errors_in_window`; process `uptime_sec`; current-leader/most-recent partition selection). No node picker in v1.
- **No logging path off this container** — `Web` can't reach `Head`'s aggregation pipeline (§7).
- **No metrics path for `Web`'s own operational health** (§8).
- **No health check contract defined** (§11).
- **`Web`'s own deployment/rollout mechanism** is undefined — it's the one service `Launcher` doesn't manage (§12).
- **Historical log replay** for the Dashboard console was deliberately deferred rather than designed in, to avoid taking on a Blob Storage log-read dependency before it's justified.
