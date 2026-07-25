# Contract: Local Product Development

> **Canonical source** for local product-development mode: Discord isolation, domain provider selection, the Compose-only `dev-support` service, Web local-admin auth, and allowed vs suppressed side effects. Product UI/command testing uses this contract. Architecture acceptance for Azure coordination, leadership, and failover (S01–S10, S12–S13 production paths) does **not** — those require real Azure and production topology.
>
> Confirmed decisions (project owner): separate Discord application required; product-workflow scope (not full control-plane simulation); Web uses a fixed local admin identity (no Entra); suggestions allow local CRUD/UI but suppress Discord DM/queue delivery and all outbound webhooks.

---

## 1. Goals and Non-Goals

### Goals

- Let a developer exercise Bot slash-command UI (`/config`, `/suggest`, `/quick-battle`) and Web dashboard pages against one designated Discord guild without Azure credentials or production Discord identity.
- Keep domain document schemas identical to production contracts (`guild_config.md`, `suggestion.md`, `status_document.md`, telemetry metrics shapes) so the same shared Pydantic models validate both modes.
- Fail closed: a misconfigured development process must refuse to start rather than silently touch production Azure or the production Discord application.

### Non-Goals

- **Not** an Azure protocol emulator. Do not reimplement Cosmos Patch/ETag, Blob Lease, Queue visibility, or Web PubSub Free_F1 semantics locally.
- **Not** a substitute for S01–S10 / S12–S13 production acceptance. Leadership, lease theft, PubSub budget, and Azure outage fidelity are out of scope for this mode.
- **Not** Head / Launcher / planned-update validation. Development excludes those containers.
- **Not** permission to reuse the production Discord bot token or production Azure Service Principal secrets.

---

## 2. Topology

```text
Development (product workflow)          Production (unchanged)
──────────────────────────────          ─────────────────────
Discord App B (dev token only)          Discord App A (prod token)
 └─ ONLY DISCORD_DEVELOPMENT_GUILD_ID    └─ all installed guilds
                                         └─ rejects reserved development guild

Compose (dev overlay required):         Compose (production):
  mosquitto, rabbitmq                     mosquitto, rabbitmq, head
  bot, ai_worker                          bot, ai_worker (profile application)
  web (Compose-included in development)   web (standalone, Azure-mediated)
  dev-support (SQLite + grants)           — no dev-support
  — no head, no launcher                  launcher (host binary)
```

| Component | Development | Production |
|---|---|---|
| Brokers | Real Mosquitto + RabbitMQ | Same |
| Bot / AI Worker | Present; providers → `dev-support` | Present; providers → Azure |
| Head / Launcher | **Absent** | Present |
| Web | In Compose; local admin + local data/live | Standalone; Entra + Azure |
| Persistence | `dev-support` SQLite volume | Cosmos / Blob / Queue / Table |
| Bot activation | `dev-support` publishes short-lived MQTT grants | Head + Blob Lease grants (`leadership_control.md`) |
| Live dashboard | Local live feed from `dev-support` (REST/SSE or internal WS) | Browser ← Azure Web PubSub (`pubsub_live.md`) |

**Compose invocation (normative):** development is opted into only by combining the production compose file with the development overlay (and whatever profile flags bring Bot/AI Worker/Web/`dev-support` up). `docker-compose.dev.yml` means **hot-reload mounts, loopback broker ports, and development services** — it is never a silent data-plane switch by itself. Setting `DCA_RUNTIME_MODE=development` without the overlay (or vice versa) is a startup failure for Bot and Web.

---

## 3. Mode Selection

| Variable | Required | Values | Owner |
|---|---|---|---|
| `DCA_RUNTIME_MODE` | Yes | `production` \| `development` | Shared — every Azure-consuming or Discord-facing process |
| `DISCORD_DEVELOPMENT_GUILD_ID` | **Yes in both modes** | Discord snowflake string | Shared Bot + Web + `dev-support`. Development: only accepted guild. Production: reserved guild always rejected. |
| `DISCORD_DEVELOPMENT_APPLICATION_ID` | Yes in `development` | Discord application snowflake | Bot (development). Verified against the authenticated application before command sync / Gateway use. Forbidden / unused in production. |
| `DISCORD_BOT_TOKEN` | Yes | In development Compose, map from `DISCORD_DEVELOPMENT_BOT_TOKEN` into the Bot's runtime token slot. Production uses the production application token. | Bot |
| `DEV_SUPPORT_URL` | Yes in `development` for Bot/Web | Internal Compose URL (e.g. `http://dev-support:8080`) | Bot + Web. Forbidden in production. |
| `DEV_COMPOSE_OVERLAY_ACTIVE` | Yes in `development` | Must be `true` when started via the development overlay | Bot + Web + `dev-support`. Forbidden in production. |
| `STORAGE_PROVIDER` | No (derived) | Must resolve to `local` when mode is `development`, `azure` when `production`. **Not** a free-form operator override. | Composition-root factory |

**Fail-closed rules:**

1. Missing/invalid `DCA_RUNTIME_MODE` → refuse to start.
2. Missing/invalid `DISCORD_DEVELOPMENT_GUILD_ID` in either mode → refuse to start.
3. `development` without `DISCORD_DEVELOPMENT_APPLICATION_ID`, `DEV_SUPPORT_URL`, or `DEV_COMPOSE_OVERLAY_ACTIVE=true` → refuse to start.
4. `development` with Azure provider selected, Azure SP env vars required for the calling service, or Azure endpoints configured for use → refuse to start (do not construct Azure clients).
5. `production` with local provider, `DEV_SUPPORT_URL`, `WEB_LOCAL_ADMIN_OID`, `DISCORD_DEVELOPMENT_APPLICATION_ID`, or `DEV_COMPOSE_OVERLAY_ACTIVE` → refuse to start.
6. **Discord identity check (development):** after Discord login and before command sync / Gateway command exercise, Bot compares the authenticated application id to `DISCORD_DEVELOPMENT_APPLICATION_ID`. Mismatch → abort (no sync, no continued Gateway use for product work).
7. **Web host exposure (development):** the Web process may bind `0.0.0.0` **inside** the container (required for Docker networking). Compose **must** publish Web only as `127.0.0.1:HOST:CONTAINER`. `dev-support` must **not** publish a host port. Non-loopback host publish for Web is a configuration failure.

---

## 4. Discord Isolation

### 4.1 Separate Discord application (required)

Local development **must** use a Discord Application and bot token that are not the production application. The production bot must not be invited into the development guild as the development test bot; the development bot must not be invited into production guilds.

**Verification:** development requires `DISCORD_DEVELOPMENT_APPLICATION_ID`. Bot authenticates with the development token, then verifies `client.application_id` (or equivalent) equals that value before guild-scoped command sync. A mismatch is a hard startup/activation failure.

### 4.2 Development mode

| Concern | Behavior |
|---|---|
| Command sync | **Guild-scoped** sync to `DISCORD_DEVELOPMENT_GUILD_ID` only (instant iteration). Do **not** call global `tree.sync()` in development. |
| Interactions | Accept slash commands / components / modals only when `interaction.guild_id == DISCORD_DEVELOPMENT_GUILD_ID`. Otherwise reject with an ephemeral error (or ignore if no response channel). |
| Guild events | Process `on_guild_join` / `update` / `remove` and periodic sync **only** for the development guild. Ignore all other guilds; perform **no** repository writes for foreign guild ids. |
| DM / no-guild | Reject for command exercise in development (same as legacy `--dev` spirit). |
| Accidental extra guild | If the development bot is somehow in another guild, Bot still must not sync commands there, not accept interactions there, and not persist config for it. |

### 4.3 Production mode (defense in depth)

| Concern | Behavior |
|---|---|
| Command sync | Global sync (unchanged production behavior). |
| Reserved guild | `DISCORD_DEVELOPMENT_GUILD_ID` is **always required**. Production Bot **rejects** interactions from that guild and skips guild-lifecycle writes for it. |
| Purpose | Prevents a reserved test server from being served by a production Bot if someone invites the prod bot there by mistake. |

Canonical owner for Bot-side wiring: `containers/bot/discord_bot.md`. This contract owns the isolation rules; that file owns env tables and lifecycle hooks.

---

## 5. Provider Boundary

### 5.1 Rejected approach

A shared module that “mirrors Azure APIs” (HTTP Azure-lookalike or SDK-compatible emulator used as the Bot↔Web integration plane) is **rejected**. It would fork lease, Patch/ETag, Queue, and PubSub contracts and falsely imply S01–S10 fidelity.

### 5.2 Domain ports (normative)

Services depend on **domain repositories / adapters**, selected once at process composition root:

| Port | Production adapter | Development adapter |
|---|---|---|
| `GuildRepository` | wraps `shared/azure/services/guilds.py` | HTTP client → `dev-support` guild API (same `GuildConfigDocument` schema) |
| `SuggestionRepository` | wraps `suggestions.py` (+ Queue only in production) | HTTP client → `dev-support` suggestions API; **no** queue enqueue/claim path |
| `StatusRepository` | wraps `status.py` (Blob) | HTTP client → `dev-support` status API |
| `MetricsRepository` | wraps `metrics.py` (Table) | HTTP client → `dev-support` metrics API |
| `ArchiveRepository` | wraps battle/log blob services | Local no-op or SQLite-backed archive in `dev-support` (optional; see §7) |
| Live feed | Azure Web PubSub negotiate + browser WS | `dev-support` live endpoint consumed by Web (no PubSub) |

Factory location (target): `src/shared/storage/` (or equivalent) exporting `build_repositories(mode=...)`. `load_azure_settings` remains production-strict and must not be called in development.

### 5.3 Schema ownership

Document JSON shapes remain owned by existing contracts. Development persistence stores the same validated models; only the transport and concurrency primitives differ.

---

## 6. `dev-support` Service

Compose-only container. Not present in production compose. Not a substitute for Head.

### 6.1 Responsibility

1. Persist guild configs, suggestions, status document sections, and metrics samples in **SQLite** on a named Docker volume.
2. Expose an **internal** HTTP API on the Compose network for Bot and Web repository adapters.
3. Act as a **Mosquitto grant publisher**: while running, publish short-lived, non-retained `control/bot/activation_grant` messages compatible with `contracts/leadership_control.md` §3.2 so Bot can connect without Head/Blob Lease.
4. Optionally ingest Mosquitto log/status topics and expose a live feed for the development Web dashboard.
5. Seed status `identity` + `suggestion_catalog` defaults on first start (`status_document.md` seed semantics).

### 6.2 Explicitly does not

- Acquire or simulate Azure Blob Lease / Web PubSub `cluster` membership.
- Poll GitHub releases or talk to Launcher.
- Call Discord, Gemini, Entra, or any Azure endpoint.
- Participate in planned-update / drain orchestration beyond publishing grants and accepting Bot heartbeats for local dashboard display.

### 6.3 Persistence

| Store | Backend | Lifetime |
|---|---|---|
| Guild / suggestion / status / metrics | SQLite file on named volume (e.g. `dev-support-data`) | Survives container restart until **explicit** volume delete/reset |
| In-flight AI tasks | Unchanged: RabbitMQ + Bot memory | Same as production local node |
| Reset | Documented operator action: remove the named Compose volume (`dev-support-data`) | No automatic wipe on restart; no public reset HTTP API |

### 6.4 Activation grants

- `dev-support` publishes grants with a stable development leadership-term UUID (or a UUID per `dev-support` process start — either is acceptable if Bot’s grant rules are satisfied).
- TTL/renew cadence may match production defaults (45s TTL / 15s renew) so Bot watchdog code paths stay exercised.
- Loss of `dev-support` → grants stop renewing → Bot hard-stops per existing grant expiry (expected development failure mode).

### 6.5 Target tree (documentation)

```
src/dev_support/          # Compose-only service
├── main.py
├── api/                  # internal REST for repositories
├── store/                # SQLite access
├── grants.py             # Mosquitto grant publisher
└── live.py               # local live-feed publisher for Web
```

Exact OpenAPI paths are implementation detail; adapters must preserve domain method semantics already used by Bot (`ensure_active_guild`, `patch_*`, suggestion CRUD) and Web page contracts.

---

## 7. Allowed Side Effects

| Feature | Development | Production |
|---|---|---|
| `/config` read/write | Allowed (local guild only) | Unchanged |
| `/quick-battle` + AI Worker | Allowed (local RabbitMQ; real Gemini keys from guild config still call Google) | Unchanged |
| `/suggest` create + Web suggestion CRUD/UI | Allowed locally | Unchanged |
| Suggestion Queue → Bot DM delivery | **Suppressed** — tickets stay local; no Discord response DMs | Full claim/DM path |
| Webhook broadcast / selected send | **Dry-run / preview only** — validate payload, return would-send result, **no** HTTPS POST to Discord webhooks | Real POSTs with SSRF allowlist |
| Admin audit blob for webhooks | Local SQLite audit row optional | Azure Blob audit (`web_auth.md` §7) |
| Battle archive | Optional local store; may no-op with logged skip | Azure Blob |
| Entra login | **Disabled** — fixed local admin | Required |
| Azure SP / Cosmos / PubSub / Table | **Forbidden** | Required |

**Allowed egress (development):** only (1) the separate development Discord application (Gateway + REST for that app) and (2) configured Gemini/Google AI calls when a guild key is present. **Forbidden egress:** Azure, Entra, Discord webhook HTTPS POSTs, and any use of the production Discord application identity.

---

## 8. Web Development Behavior

Canonical auth carve-out lives here; `contracts/web_auth.md` links here for development and remains production Entra-only for deployed Web.

| Concern | Development |
|---|---|
| Auth | Fixed local admin principal (e.g. synthetic `oid = local-dev-admin`). No MSAL, no Entra JWT validation. |
| Banner | Persistent visible “DEVELOPMENT” banner on every page. |
| API policy | All `/api/*` still require the local-admin credential mechanism (dev header/token or auto-injected session) — not a fully open API. |
| Data | Repository adapters → `dev-support` only. |
| Live charts/logs | Local live transport from `dev-support`; negotiate must **not** call Azure PubSub. |
| Webhooks page | Dry-run only (§7). |
| Network | Process may bind `0.0.0.0` in-container; Compose publishes **only** `127.0.0.1:HOST:CONTAINER` to the host. |
| Compose | Included in development stack (exception to production “Web excluded from compose”). Deferred for the Phase 2.5 foundation spine until `src/web/` exists — see S14 staging. |

---

## 9. Startup Safety Checklist

Every Bot/Web/`dev-support` process in development must verify before serving:

1. `DCA_RUNTIME_MODE=development`
2. `DISCORD_DEVELOPMENT_GUILD_ID` present and well-formed
3. `DISCORD_DEVELOPMENT_APPLICATION_ID` present (Bot); authenticated application id matches before sync
4. `DEV_COMPOSE_OVERLAY_ACTIVE=true` and `DEV_SUPPORT_URL` present (Bot/Web)
5. Storage provider is `local`; no Azure client construction
6. Web host publish is loopback-only (Compose mapping); `dev-support` has no host publish
7. `dev-support` is reachable on the Compose network before Bot accepts grants / Web serves data pages

Production checklist:

1. `DCA_RUNTIME_MODE=production`
2. `DISCORD_DEVELOPMENT_GUILD_ID` present; reserved-guild rejection enabled
3. Azure settings load successfully
4. Local provider / local-admin auth / `DEV_SUPPORT_URL` / `DISCORD_DEVELOPMENT_APPLICATION_ID` / overlay marker absent

---

## 10. Reset and Recovery

| Event | Behavior |
|---|---|
| Container restart | SQLite state preserved |
| Explicit reset | Operator removes the `dev-support-data` volume; status seed re-runs on next start |
| `dev-support` crash | Bot loses grant renewals → hard-stop; Web data APIs fail closed with clear errors |
| Broker crash | Same reconnect semantics as production local node (`rabbitmq.md` / `mosquitto.md`) |

---

## 11. Acceptance Criteria

See `scenarios/14_local_development_isolation.md`. Summary invariants:

1. Development processes make **zero** Azure, Entra, or Discord-webhook egress calls (Discord Gateway/REST for the **development** app and Gemini remain allowed).
2. Only the separate development Discord application is used; authenticated application id must match `DISCORD_DEVELOPMENT_APPLICATION_ID`; commands sync only to the designated guild.
3. Foreign-guild interactions and lifecycle events are rejected/ignored with no repository writes.
4. Production Bot always rejects the reserved development guild (`DISCORD_DEVELOPMENT_GUILD_ID` required).
5. Local suggestion CRUD works without queue/DM delivery; webhooks are dry-run only (full Web/command steps may be deferred — see S14 staging).
6. State persists across restart until explicit volume reset.
7. Passing S14 does **not** claim S01–S10 Azure coordination fidelity.

---

## 12. Related

| Concern | Canonical doc |
|---|---|
| High-level topology / Compose | `architecture.md` |
| Production Azure providers | `containers/azure.md` |
| Bot env / sync / grants | `containers/bot/discord_bot.md` |
| Web env / pages | `containers/web/web.md` |
| Production Entra auth | `contracts/web_auth.md` |
| Document schemas | `guild_config.md`, `suggestion.md`, `status_document.md` |
| Production live PubSub | `contracts/pubsub_live.md` |
| Leadership grant shape (dev publisher differs) | `contracts/leadership_control.md` §1 note; `local_development.md` §6.4 |
| Isolation scenario | `scenarios/14_local_development_isolation.md` |
| Implementation sequence | `to_resolve.md` |
