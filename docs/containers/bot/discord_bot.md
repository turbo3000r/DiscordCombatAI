# Service: Bot

> **Scope note, mirroring `web.md`'s own framing for `web/pages/*.md`:** this doc covers only what's shared across the whole `Bot` container — Gateway lifecycle, guild onboarding, the AI task pipeline's Bot-side plumbing, background polling, health/telemetry, and update behavior. It deliberately does **not** re-describe any single command's own flow, permissions, or visuals — those live in `bot/commands/quick-battle.md`, `bot/commands/config.md`, `bot/commands/suggest.md`. Shared UI component *designs* (embeds/views/modals reused across commands) live in `bot/visuals.md`, not here. This doc is what those files point back to for anything container-level.

---

## 1. Responsibility

`Bot` is the Discord Interface (`architecture.md`'s Container Breakdown) — the **only** component in the system that connects to the Discord Gateway. Concretely, it owns:

- The Gateway connection lifecycle itself, gated by `Head`'s leader signal (§6.1) — never self-activating.
- Slash command registration/dispatch and localization setup (each command's own logic lives in its own doc, §6 here only covers the shared plumbing every command sits on top of).
- Guild onboarding and configuration persistence — one Cosmos DB document per guild (`contracts/guild_config.md`), replacing legacy's per-guild local JSON file entirely.
- The Bot-side half of the AI task pipeline: publishing `ai_tasks`, consuming `ai_tasks_results`, and tracking best-effort progress ticks to drive a live Discord status bar (§6.3, `contracts/task_progress.md`).
- Polling Azure Queue Storage for suggestion-response notifications and relaying them back to the original suggester (§6.6).
- Reporting its own liveness/latency to `Head` (Mosquitto heartbeat) and, via the shared `status.py` document, to `Web` (§6.3).

`Bot` has no AI logic of its own (that's `AI Worker`'s job entirely) and no leader-election logic of its own (that's `Head`'s) — it is purely the Discord-facing edge of the system, reacting to signals from `Head` and results from `AI Worker`/`RabbitMQ`.

---

## 2. File Structure

```
src/bot/
├── Dockerfile
├── entrypoint.sh
├── main.py                        # Entry point: waits for Head signal (§6.1), then runs the bot
├── modules/
│   ├── client.py                  # discord.py Bot subclass — setup_hook, on_ready, translator wiring
│   ├── configs/
│   ├── UI/                        # Bot-scope shared UI elements — see bot/visuals.md for design
│   │   ├── embeds/
│   │   ├── modals/
│   │   └── views/
│   │       └── task_progress_container.py   # TaskProgressContainer — plumbing lives here (§6.3), concrete render design in visuals.md §3
│   ├── events/
│   │   ├── guild_events.py        # on_guild_join / on_guild_update / on_guild_remove — writes contracts/guild_config.md's document (§6.2)
│   │   └── control_events.py      # Safe desired mode + short-lived activation-grant subscribers (§6.1, §6.5)
│   ├── services/                  # NEW in this revision — Bot-container-wide background infra, not owned by any single command
│   │   ├── task_tracker.py        # Local task_id map + progress/ai_worker/# subscription (§6.3)
│   │   ├── heartbeat.py           # status/bot/heartbeat publisher + periodic status.py push (§6.3)
│   │   └── guild_sync.py          # Periodic reconciliation sweep for Discord-side guild metadata (§6.2)
│   └── commands/                  # Bot commands — see each command's own doc
│       ├── suggestions/           # /suggest — bot/commands/suggest.md. queue_poller.py + notification_sweep.py (§6.6) live here, per architecture.md's existing tree
│       ├── battle/                # /quick-battle — bot/commands/quick-battle.md
│       └── config/                # /config — bot/commands/config.md
└── localization/
    ├── handler.py
    └── lang/                       # en.json, es.json, ua.json — contracts/localization.md
```

> **Correction applied in this revision:** `architecture.md`'s Project File Structure had no home for genuinely container-wide background infra (task tracking, heartbeat, guild sync) that isn't any single command's responsibility — everything under `modules/` was either `client.py`-level or per-command. `modules/services/` is added here (and should be mirrored into `architecture.md`) to close that gap, following the same "shared, not command-specific" reasoning `ai_worker/nodes.md` already used to justify its own `nodes/` folder.

---

## 3. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `APPLICATION_VERSION` | Yes | — | Exact coordinated release tag injected by Compose. Included in the canonical heartbeat and required to match `contracts/launcher_ipc.md` §4's grammar. Startup fails if absent/invalid. |
| `DCA_RUNTIME_MODE` | Yes | — | `production` \| `development`. Fail-closed. Canonical: `contracts/local_development.md` §3. |
| `DISCORD_DEVELOPMENT_GUILD_ID` | **Yes in both modes** | — | Discord snowflake of the designated development guild. Development: only accepted guild. Production: interactions/lifecycle for this id are always rejected/ignored. |
| `DISCORD_DEVELOPMENT_APPLICATION_ID` | Yes in `development` | — | Expected Discord application id. Verified against the authenticated application before guild-scoped sync. Forbidden in production. |
| `DEV_SUPPORT_URL` | Yes in `development` | — | Internal Compose URL for `dev-support` (e.g. `http://dev-support:8080`). Forbidden in production. |
| `DEV_COMPOSE_OVERLAY_ACTIVE` | Yes in `development` | — | Must be `true` when started via `docker-compose.dev.yml`. Forbidden in production. |
| `BOT_NODE_ID` | Yes | — | Host node identity. Must equal the deployment's `NODE_ID` / `HEAD_NODE_ID` / `AI_WORKER_NODE_ID`. Grammar `^[A-Za-z0-9._-]+$`, length 1–128. Compose injects from host `NODE_ID`. |
| `DISCORD_BOT_TOKEN` | Yes | — | The Discord bot token used to connect to the Gateway. **Development Compose maps `DISCORD_DEVELOPMENT_BOT_TOKEN` into this slot** — never the production token (`local_development.md` §4). Never logged (§7). |
| `BOT_MOSQUITTO_HOST` | No | `mosquitto` | Hostname of the local Mosquitto broker. |
| `BOT_MOSQUITTO_PORT` | No | `1883` | Mosquitto broker port. |
| `BOT_RABBITMQ_HOST` | No | `rabbitmq` | Hostname of the local RabbitMQ broker. |
| `BOT_RABBITMQ_PORT` | No | `5672` | RabbitMQ broker port. |
| `BOT_RABBITMQ_USER` / `BOT_RABBITMQ_PASS` | Yes | — | **Resolved (P0.4)** — `Bot`'s own broker credentials, canonical definition in `rabbitmq.md` §3/§13. Never logged. |
| `RABBITMQ_DEFAULT_VHOST` | No | `/discordcombatai` | Shared vhost for broker URL construction (`contracts/ai_task.md` §2). Injected by Compose. |
| `BOT_DRAIN_PROGRESS_INTERVAL_SEC` | No | `5` | **New this revision (P0.3)** — cadence of the `status/bot/drain_progress` publish (§6.3a, `contracts/drain_status.md` §1) while `bot.draining` is set. |
| `BOT_QUEUE_POLL_INTERVAL_SEC` | No | `300` | How often `Bot` polls Azure Queue Storage for suggestion-response notifications (§6.6). Formalizes the "every 5 minutes" already stated in `architecture.md`'s Data Storage table. |
| `BOT_AI_TASK_STALL_TIMEOUT_SEC` | No | `120` | **New this revision** — per-task stall timer (`contracts/ai_task.md` §5): if no `progress/ai_worker/<task_id>` message (phase-change **or** heartbeat tick, `contracts/task_progress.md` §3) arrives within this window, `Bot` gives up on the task locally. Resets on every progress message, not just phase changes. |
| `BOT_AI_TASK_TIMEOUT_SEC` | No | `900` | **New this revision, replaces an earlier flat `300` default** — absolute per-task cap from publish to result, regardless of how healthy the progress ticks look (`contracts/ai_task.md` §5). **Proposed default, not yet confirmed against real Gemini latency** — see that contract's §8. |
| `BOT_SUGGESTION_SWEEP_INTERVAL_SEC` | No | `900` | How often `Bot`'s Cosmos-side reconciliation sweep (§6.6) checks for suggestions stuck at `notification_status: "pending"` (after min-age), independent of Queue Storage. |
| `BOT_SUGGESTION_SWEEP_MIN_AGE_SEC` | No | `600` | **P0.5.1.** Sweep only claims `pending` tickets whose last update into pending is older than this (avoids racing the queue fast path). Canonical: `contracts/suggestion.md` §3. |
| `BOT_SUGGESTION_MAX_DM_ATTEMPTS` | No | `5` | Combined attempt ceiling (fast path + sweep) before `notification_status` is set to `"failed"` (`contracts/suggestion.md` §3). |
| `BOT_GUILD_SYNC_INTERVAL_SEC` | No | `3600` | How often the periodic guild-metadata reconciliation sweep (§6.2, `contracts/guild_config.md` §4) runs, on top of the `on_guild_join`/`on_guild_update` event-driven writes. Hourly default — guild metadata (mainly `member_count`) doesn't need tighter freshness than that. |
| `BOT_HEARTBEAT_INTERVAL_SEC` | No | `30` | Cadence of the `status/bot/heartbeat` publish (§6.3) — matches the order of magnitude `head.md` §3 already uses for its own election heartbeat. |
| `BOT_STATUS_PUSH_INTERVAL_SEC` | No | `60` | Cadence of pushing the same heartbeat snapshot into the shared `status.py` cloud document (§6.3), so `Web`'s Dashboard can read it without any Mosquitto access. Deliberately slower than the Mosquitto heartbeat itself — `Head`'s own Table/Blob batching already uses this order of magnitude (`head.md` §3's `HEAD_TELEMETRY_BATCH_INTERVAL_SEC`), and this is the same class of "batched cloud write," not a live stream. |
| `BOT_SHUTDOWN_GRACE_SEC` | No | `30` | On `control/bot/desired_state` resolving to `stopped` (§6.5), how long `Bot` waits for any still-in-flight `ai_tasks_results` before giving up on a clean response and disconnecting anyway. Formalizes `architecture.md`'s "waits a little bit" phrasing into an actual bounded value. |
| `BOT_CONTROL_DRAIN_TIMEOUT_SEC` | No | `45` | Maximum bounded failure soft-stop drain. At expiry, Bot escalates autonomously to hard-stop unless safe control in the same term has been restored by a fresh grant. Separate from Head's planned-update drain timeout. |
| `BOT_ACTIVATION_GRANT_MAX_TTL_SEC` | No | `60` | Reject any activation grant with a larger TTL. Normal default grant TTL is 45s and is renewed every 15s; canonical timer semantics are in `contracts/leadership_control.md` §3.2. |

> **Azure configuration lives in `azure.md`, not here.** Per that doc's §4, production `Bot` depends on **Queue Storage** and **Cosmos DB** — all Azure authentication and endpoint variables are defined once in `azure.md` §3. In `DCA_RUNTIME_MODE=development`, Bot injects local repository adapters toward `dev-support` and **must not** construct Azure clients (`contracts/local_development.md` §5).
>
> **`GEMINI_API_KEY` is not a `Bot` environment variable.** Unlike `AI Worker` (`ai_worker.md` §3), `Bot` never holds a project-wide Gemini key — every Gemini call `Bot` itself makes (only `/config`'s model-listing call, `bot/commands/config.md` §6) uses the *guild's own* staged key, read from `contracts/guild_config.md`'s `api_key` field, never an environment variable.

---

## 4. Inbound Communication

| Source | Channel | Format | Trigger |
|---|---|---|---|
| Discord Gateway | WebSocket | Interactions (slash commands, component/modal submits) | Continuous, only once activated (§6.1) |
| `Head` (local) | Mosquitto, `control/bot/desired_state` (QoS 1, retained) | Safe desired mode only: `inactive \| draining \| stopped` (`contracts/leadership_control.md` §3.1) | Startup, demotion, update, and failure transitions |
| `Head` (local) | Mosquitto, `control/bot/activation_grant` (QoS 1, not retained) | Short-lived `active \| draining` grant with leadership-term UUID, per-term sequence, and TTL (`contracts/leadership_control.md` §3.2) | Renewed while local Head is confidently leader; never replayed from retained state |
| `AI Worker` | RabbitMQ, `ai_tasks_results` (consume, **auto-ack** — resolved this revision, `contracts/ai_task.md` §2, was previously undefined — correlated via `correlation_id`, not `reply_to`) | Finished graph output — `Environment` or `story`+`winners` | On task completion (success or synthetic error, per `rabbitmq.md` §6) |
| `AI Worker` | Mosquitto, `progress/ai_worker/#` (wildcard subscribe) | Phase-update message (`contracts/task_progress.md` §4) | On each phase transition of a task `Bot` itself submitted |
| Azure Queue Storage | Polled (not pushed) | Suggestion-response notification event | Every `BOT_QUEUE_POLL_INTERVAL_SEC` (§6.6) |

---

## 5. Outbound Communication

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| Discord Gateway | WebSocket | Slash command responses, DMs, guild messages, embed/view edits | Continuous |
| RabbitMQ (via Celery `send_task`) | `ai_tasks` (dispatch, `kwargs={"envelope": {...}}`, `task_id=task_id`, task name `ai_worker.tasks.run_graph`, publisher confirms) | AI task envelope (`contracts/ai_task.md` §3) | Harness (Phase 2) or `/quick-battle` graph invocations (later) |
| Mosquitto | `status/bot/heartbeat` | Canonical versioned Bot liveness/dependency payload (`contracts/telemetry.md` §2.1) | Every `BOT_HEARTBEAT_INTERVAL_SEC` |
| Mosquitto | `status/bot/drain_progress` (QoS 1, not retained) — **new this revision, P0.3** | `{"schema_version": 1, "node_id": ..., "leadership_term": ..., "in_flight_workflows": N, "observed_at": ISO8601}` (`contracts/drain_status.md` §1) | Every `BOT_DRAIN_PROGRESS_INTERVAL_SEC` while `bot.draining` is set (§6.4a) |
| Mosquitto | `status/bot/control_ack` | Applied state, Gateway connection flag, leadership term, and command sequence (`contracts/leadership_control.md` §3.3) | After each control transition |
| Mosquitto | `logs/<level>/bot` | Structured log string, per §7 | On every log emission |
| Azure Cosmos DB | Upsert | Guild config document writes (`contracts/guild_config.md` §4) | `/config` Apply, `on_guild_join`/`on_guild_update`/periodic sweep (§6.2), `on_guild_remove` (`left_at`) |
| Azure Cosmos DB | Insert | Suggestion ticket document (`bot/commands/suggest.md` §9) | `/suggest` modal submit |
| Azure Queue Storage | `receive_messages` (poll, then delete on ack) | — | Every `BOT_QUEUE_POLL_INTERVAL_SEC` (§6.6) |
| Azure Blob Storage (`status.py` document) | Upsert | **Only** the `status` section (`{latency_ms, guild_count, updated_at}`) via ETag RMW — never identity/catalog (`contracts/status_document.md`) | Every `BOT_STATUS_PUSH_INTERVAL_SEC` |
| Discord (DM) | HTTPS (via Gateway) | Suggestion-response notification to the original suggester (§6.6, confirmed decision — not an admin channel) | On a new Queue Storage event resolving to a known suggestion |

---

## 6. Internal Logic

### 6.0 Phase 2 scope and concurrency (resolved)

**Phase 2 includes:** Gateway lifecycle under grants, fencing/watchdog, guild join/update/remove + periodic metadata sync, Celery dispatch/`ai_tasks_results` consumer/task tracker/progress plumbing, heartbeat + status Blob push, drain progress, hard-stop (purge/revoke/synthetic `worker_terminated`). Transport is exercised only via tests/acceptance harnesses (`contracts/ai_task.md` §11).

**Phase 2 excludes:** user-facing slash commands; the full `ProcessCommand` command-dispatch abstraction (deferred to `/config`, `/suggest`, `/quick-battle`); suggestion queue poller/sweep; lobby/collector/vote workflow units beyond counting AI-task map entries in `in_flight_workflows`.

**Concurrency model (mandatory):**

| Owner | Responsibility |
|---|---|
| asyncio event loop | discord.py Gateway, interaction/command hooks (later phases), all mutations of task map / `in_flight_workflows` / drain flags that Discord observes |
| Dedicated Celery/Kombu thread(s) | Blocking `send_task` + publisher confirm wait, `ai_tasks_results` consume loop, purge/revoke |
| MQTT client thread(s) | paho callbacks only enqueue work |

- Never run blocking Celery/Kombu I/O on the asyncio event loop.
- Hand off broker/MQTT events into the loop with `call_soon_threadsafe` / `run_coroutine_threadsafe` (or an asyncio queue drained by a loop task).
- MQTT callbacks must not call discord.py or write Bot maps directly.
- **Shutdown order:** stop accepting new AI work → hard-stop/drain path as commanded → cancel per-task timers → stop MQTT → stop result consumer + Celery client → close Gateway → exit.
- **Reconnect ownership:** Discord = discord.py; AMQP = Celery/Kombu (`rabbitmq.md` §8a); MQTT control = Bot MQTT client with fail-closed grant semantics (`leadership_control.md`).

### 6.1 Activation Lifecycle

`Bot` starts `inactive`. No Mosquitto control connection, no current grant, malformed control data, or an absent retained state all mean no Gateway connection. Retained `control/bot/desired_state` deliberately cannot contain `active`; it reconciles only the safe modes `inactive`, `draining`, and `stopped`.

The **only** activation authority is a fresh, non-retained `control/bot/activation_grant` from the local `Head`, accepted under `contracts/leadership_control.md`. `Bot` starts a local monotonic deadline on receipt, ignores lower/equal sequences within the same leadership-term UUID, and never orders UUID terms. A new term is accepted only through a live grant over the current local control connection. On grant expiry or loss of the Head grant/watchdog, `Bot` executes hard-stop autonomously. MQTT disconnect triggers an immediate bounded soft-stop, with grant expiry as the hard-stop backstop.

On accepting a valid `active` grant:
1. Connects to the Discord Gateway.
2. Sets up localization (`self.l10n`, `tree.set_translator` — carried forward from legacy as-is, per `contracts/localization.md`).
3. Syncs the slash command tree:
   - **Production:** global `tree.sync()`.
   - **Development:** guild-scoped sync to `DISCORD_DEVELOPMENT_GUILD_ID` only — never global sync (`contracts/local_development.md` §4).
4. Starts the three background services under `modules/services/` (§6.2, §6.3, §6.6) and the Queue Storage poller (§6.6). In development, suggestion queue poller/sweep DM delivery is **suppressed** (§6.6 / `local_development.md` §7); guild/status repositories talk to `dev-support`.
5. Clears `bot.draining`; restoration is explicit through the fresh grant and does not depend on stale retained state.

**Development activation source:** when `DCA_RUNTIME_MODE=development`, grants come from Compose-only `dev-support` over Mosquitto (same grant schema), not from Head/Blob Lease. Head is absent from the development stack. Production activation rules above are unchanged.

### 6.1a Discord Guild Isolation

Canonical rules: `contracts/local_development.md` §4.

- **Development:** accept interactions and process guild lifecycle/sync **only** for `DISCORD_DEVELOPMENT_GUILD_ID`. Foreign guild events produce no repository writes. DMs / no-guild command exercise are rejected. Authenticated application id must equal `DISCORD_DEVELOPMENT_APPLICATION_ID` before sync.
- **Production:** `DISCORD_DEVELOPMENT_GUILD_ID` is always required; reject interactions and skip lifecycle writes for that reserved guild (defense in depth).

### 6.2 Guild Lifecycle & Config Persistence

All guild state lives in the single document defined by `contracts/guild_config.md` — no local files. Production persists via Cosmos (`guild_config.md` §2); development via `GuildRepository` → `dev-support` with the **same** document schema. Writes use field-scoped Patch + ETag semantics in production (`guild_config.md` §4a); development adapters preserve method semantics with SQLite optimistic concurrency appropriate to single-node use.

- **`on_guild_join` / rejoin:** call guild repository create-or-reactivate (`contracts/guild_config.md` §7), subject to §6.1a isolation. Fresh create uses defaults (§5 there) plus Discord metadata. Rejoin clears `left_at`, refreshes metadata, preserves `created_at` and admin config. Also sends the existing legacy welcome flow (`WelcomeView`/`WelcomeLocaleSelect`, unchanged — see `bot/visuals.md`'s component catalog).
- **`on_guild_update`:** Patch only Discord-sourced fields that changed (name/icon/owner) — never touches admin-configured fields. Ignored for foreign guilds in development / reserved guild in production.
- **`on_guild_remove`:** Patch `left_at` to now. **Does not delete** — soft-delete confirmed (`guild_config.md` §7).
- **Periodic reconciliation sweep** (`modules/services/guild_sync.py`, every `BOT_GUILD_SYNC_INTERVAL_SEC`): iterates `bot.guilds` and Patches Discord-sourced fields for every currently joined guild that §6.1a allows. Catching Cosmos rows for guilds **absent** from `bot.guilds` (missed removals while offline) remains a remaining P1.3 item — not required to scaffold `guilds.py`.

**AI locale:** when publishing `ai_tasks`, map `language` → `language_locale` per `contracts/localization.md` §4 (`ua` → `uk-UA`).

### 6.3 Task Tracking, Heartbeat, and the Status-Bar Backbone

This section is the shared **plumbing** underneath the live task-progress status bar — the concrete checklist *rendering* design (icons, layout, the exact mockup) is owned by `bot/visuals.md` §3's `TaskProgressContainer` entry, not duplicated here, per this project's "define once" convention.

**Local task map** (`modules/services/task_tracker.py`): an in-memory dict, `task_id -> TaskRecord`:

```python
class TaskRecord(TypedDict):
    command: str                              # e.g. "quick-battle" — which command owns this task
    discord_message_ref: Any                  # the message/interaction Bot needs to edit as phases progress
    graph: Literal["environment", "battle"]
    current_phase: str                        # queued | launching | composing | refining | finishing
    phase_history: list[tuple[str, str]]      # ordered (phase, ISO-8601-timestamp) pairs — every phase this task has visibly entered, used both to render each checklist line's state and to compute Duration
    created_at: str                            # ISO 8601 — set the moment Bot publishes the ai_tasks message, before AI Worker's own first "launching" tick even arrives
    last_progress_at: str                      # NEW this revision — ISO 8601, updated on EVERY progress/ai_worker/<task_id>
                                                 # message (phase-change or heartbeat alike, contracts/task_progress.md §3),
                                                 # unlike phase_history which only records phase CHANGES. This is what the
                                                 # stall timer (contracts/ai_task.md §5) actually checks against.
```

- **On publish:** `Bot` creates the `TaskRecord` immediately when it pushes the `ai_tasks` message — `current_phase` starts at `queued` locally, matching `task_progress.md` §4's phase vocabulary, even though that first tick technically originates from `Bot` itself, not `AI Worker`. `last_progress_at` is also initialized to this same moment, so the stall timer (§9) has a valid starting point even before `AI Worker`'s first real tick arrives.
- **On a `progress/ai_worker/<task_id>` tick:** looks up `task_id` in the map (silently discards a miss, per `task_progress.md` §7's already-documented "no error" stance). Always updates `last_progress_at` to the tick's timestamp, resetting the stall timer — regardless of whether this tick is a phase change or a same-phase heartbeat (§3, new this revision). Only appends to `phase_history` and updates `current_phase`/re-renders `TaskProgressContainer` if the phase actually changed — a heartbeat tick with an unchanged phase resets the stall timer silently, with no visible UI update, since nothing about the checklist state actually changed.
- **On the terminal `ai_tasks_results` message (RabbitMQ), or on either timeout firing (§9):** the `TaskRecord` is removed from the map — these are the **only** cleanup paths (expanded this revision — previously only the RabbitMQ result path existed). A task whose result never arrives **and** never stalls/times out (impossible by construction once both timers are running, §9) is no longer a real gap — the dangling-entry risk is now bounded to "`Bot` itself crashes/restarts mid-task," which loses all in-memory state including the timers themselves, still flagged as an open item (§13).

**Heartbeat / status reporting** (`modules/services/heartbeat.py`), feeding both `Head` telemetry and `Web` “now” cards (`contracts/telemetry.md`, `contracts/status_document.md`):
- Every `BOT_HEARTBEAT_INTERVAL_SEC`, publishes `status/bot/heartbeat` using the exact schema in `contracts/telemetry.md` §2.1: `node_id`, required `APPLICATION_VERSION`, audit timestamp, Gateway state, `latency_ms`, `guild_count`, and the latest `rabbitmq_connected` / `cosmos_ok` / `azure_queue_ok` / `status_blob_ok` results. No active probing is performed just to build the heartbeat; dependency booleans reflect the latest real connection/operation.
- Leader `Head` samples fresh latency/guild fields into Table rows. After `HEAD_SERVICE_HEARTBEAT_STALE_SEC` (default 90) without a valid heartbeat, Head marks Bot stale and writes those fields as `null`; staleness does not itself change leadership.
- Every `BOT_STATUS_PUSH_INTERVAL_SEC`, updates **only** the `status` section via `status.py`'s ETag RMW — never `identity` / `suggestion_catalog` (Web-owned after seed). Dashboard “now” latency/guild count read this section; do not use Cosmos document counts.

### 6.3a In-Flight Workflow Counter and Drain Progress (P0.3, resolved)

**Canonical contract: `contracts/drain_status.md`.** This subsection states only what `Bot` itself does; the wire schema, QoS, and `Head`'s consuming side live there.

`Bot` maintains one authoritative local counter, `in_flight_workflows` — a strict superset of the task map (§6.3): every `/quick-battle` lobby, collector, vote, **or** `ai_tasks` entry currently open counts as one unit, incremented on creation and decremented on terminal resolution (success, user cancel, error, or a user-visible timeout). This closes the backlog finding that waiting only for RabbitMQ work does not drain the command workflow — a lobby, collector, or vote can be open with nothing yet published to `ai_tasks` at all, and still must count.

On receiving a `draining` grant (`contracts/leadership_control.md` §3.2):
1. `Bot` immediately sets `bot.draining = True` (existing behavior, §6.4's opt-in block).
2. `Bot` begins publishing `status/bot/drain_progress` on Mosquitto (QoS 1, not retained) every `BOT_DRAIN_PROGRESS_INTERVAL_SEC` (default `5`, §3) with the current `in_flight_workflows` count, so `Head` has real visibility instead of inferring drain completion from empty RabbitMQ queues.

### 6.4 Command Availability During Drain

**Confirmed decision (project owner):** `bot.draining` is one global container-wide flag. Whether a given command respects it is a per-command opt-in on the future `ProcessCommand` helper — **not implemented in Phase 2** (no user-facing slash commands).

When command phases land:

```python
def ProcessCommand(bot, ..., blocked_during_drain: bool = False):
    ...
```

- Default `False` — most commands keep working during drain.
- `/quick-battle` opts in (`blocked_during_drain=True`) — see `bot/commands/quick-battle.md` §4.
- `/config` and `/suggest` stay unblocked.

Phase 2 still sets `bot.draining` and rejects **new AI task publishes** from the harness/dispatch API while draining. Localized ephemeral “temporarily unavailable” copy for slash commands is deferred with `ProcessCommand`.

### 6.5 Fenced Active, Bounded Drain, and Hard Stop

`contracts/leadership_control.md` is canonical for grant, sequence, TTL, acknowledgement, and failure transitions. The container behavior is:

- **Inactive:** default. Disconnect/no grant means no Gateway connection.
- **Active:** only a fresh live `active` grant authorizes Gateway connection and new work.
- **Soft-stop/draining:** immediately set `bot.draining`, reject new AI work, and remain Gateway-connected only to finish existing work. `Bot` begins publishing `status/bot/drain_progress` (§6.3a). At `BOT_CONTROL_DRAIN_TIMEOUT_SEC`, escalate to hard-stop unless the same safe leadership/control has been restored first by a fresh grant. `Head`'s own, separate planned-update drain timeout (`HEAD_DRAIN_TIMEOUT_SEC`, `contracts/drain_status.md` §2) escalates the same way — see the row immediately below, which now applies uniformly whether the drain timed out due to `Head`'s planned-update window or `Bot`'s own local failure-drain bound.
- **Hard-stop/stopped:** (1) purge `ai_tasks` and `revoke(task_id, terminate=True)` every actively-claimed/running `AI Worker` task (`contracts/ai_task.md` §8's cancellation matrix — `Bot` is the actor); synthesize `AiTaskResultFailed`/`node: "worker_terminated"` for each affected task; **(1a) new this revision, P0.3 — also cancel any open lobby/collector/vote component views `Bot` is still tracking and edit their messages to a localized "update in progress, please retry" notice**, since these can be in flight with no `ai_tasks` entry at all (`quick-battle.md` §6); (2) notify every affected originating Discord thread within `BOT_SHUTDOWN_GRACE_SEC`; (3) disconnect the Gateway; (4) publish `status/bot/control_ack` with `state: "stopped"` and `gateway_connected: false`.

Hard-stop is triggered by an explicit retained `stopped`, active-grant expiry, Head watchdog/process disappearance, learning that the local Head is not leader, simultaneous total loss of Blob Lease and Web PubSub coordination, **or a drain timeout elapsing with `in_flight_workflows > 0`** (`contracts/drain_status.md` §2 — resolves the previously-flagged inconsistency between "proceed and abandon work" text and a hard-stop path only defined for loss-of-internet: drain timeout now explicitly escalates into this same hard-stop sequence, including the new lobby/collector/vote cancellation step 1a, and that is where cancellation/revoke actually happens — no revoke occurs at drain-timeout itself, consistent with `contracts/ai_task.md` §8's cancellation matrix). PubSub-only, Blob-renew-only, and Mosquitto failures use bounded soft-stop as specified in the canonical contract.

Strict at-most-one Gateway connection is **not guaranteed** in every partition/delay case. The accepted limitation and best-effort voluntary-demotion ordering are documented in `contracts/leadership_control.md` §1/§6.

### 6.6 Suggestion Queue Storage Polling

**Canonical contract: `contracts/suggestion.md`.** This subsection is the Bot runtime; schemas and the claim state machine live there.

**Product development:** when `DCA_RUNTIME_MODE=development`, do **not** start the Azure Queue poller or the DM reconciliation sweep. Local `/suggest` and Web suggestion CRUD persist via `SuggestionRepository` → `dev-support` only; no Discord response DMs and no queue enqueue/claim (`contracts/local_development.md` §7). Production behavior below is unchanged.

`modules/commands/suggestions/service/queue_poller.py` polls Azure Queue Storage every `BOT_QUEUE_POLL_INTERVAL_SEC` (fast path). Queue message shape, visibility timeout (60s), poison after 5 dequeues, and delete-only-after-`sent`/`failed` rules are in the contract §4.

On a notification event, `Bot`:
1. Loads the ticket by Cosmos `id` + `guild_id` from the queue message (preferred durable key). `ticket_uid` on the same message is for ops/logs/UI correlation only (`contracts/suggestion.md` §4).
2. **Atomically claims** via ETag-conditional patch `notification_status: pending → claiming` (sets `notification_claimed_at` / `notification_claimed_by`). On conflict → skip.
3. Sends a **DM** to `contact.user_id` (`contact.method: "dm"`), localized via the ticket's `locale.stored` (full `LocaleInfo` on the document).
4. On success → `claiming → sent`, **then** delete the Queue message. On failure → increment `notification_attempts` / set `notification_last_error`; if attempts &lt; `BOT_SUGGESTION_MAX_DM_ATTEMPTS` → back to `pending`; else → `failed`. Only the claimant may perform these transitions.

**Reconciliation sweep** (`notification_sweep.py`): every `BOT_SUGGESTION_SWEEP_INTERVAL_SEC`, query Cosmos for `notification_status == "pending"` **and** age ≥ `BOT_SUGGESTION_SWEEP_MIN_AGE_SEC` (default 600), then the same claim → DM → `sent`/`pending`/`failed` path — no Queue dependency. Already-`sent` / foreign-`claiming` tickets are skipped.

---

## 7. Logging

Same shared structured format as every other service (`contracts/log_archive.md`):

```
[%time%][%level%][bot][%file/module%]<trace_id, guild_id, user_id, command, task_id>: [%message%]
```

- **Recommended minimum tags:** `guild_id`, `user_id`, `command` on every command-originated log line; `task_id` additionally on anything related to the AI task pipeline (§6.3).
- **Sensitive data exclusion:** `DISCORD_BOT_TOKEN` must never be logged, at any level. A guild's `api_key` (`contracts/guild_config.md`) must never be logged either, at any level — even though it's stored plaintext in Cosmos DB (§3, that contract's §7), logging it would additionally expose it in `Head`'s aggregated Blob Storage log archive, a strictly worse exposure surface. This restates `bot/commands/config.md` §11's own logging note at the container level, since it applies to every code path that ever reads the field back, not just `/config` itself.

---

## 8. Metrics

The v1 Bot telemetry surface is deliberately limited to the heartbeat in `contracts/telemetry.md` §2.1. Dashboard metrics sourced from it are `latency_ms` and `guild_count`; Gateway/dependency fields are operational health.

Business counters such as `commands_invoked_total`, `ai_tasks_published_total`, `suggestion_tickets_created_total`, and `active_lobby_count` have no agreed transport or v1 consumer and are explicitly deferred. Implementations must not emit an undocumented Mosquitto metric topic or add them to Table rows ad hoc.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Discord Gateway disconnects unexpectedly (network blip, not a `Head`-driven stop) | discord.py's own connection-state events | Relies on discord.py's built-in automatic reconnect — no `Bot`-specific override decided. |
| Cosmos DB unreachable (guild config or suggestion read/write) | Exception from `cosmos.py` (`azure.md` §9) | Surfaced per-command — see each command's own Failure Modes section (`config.md` §12, `suggest.md` §12). No container-wide fallback beyond what each command already documents. |
| Azure Queue Storage poll fails (§6.6) | Exception from `queue.py` | Skip this poll cycle; after 3 consecutive failures set heartbeat `dependencies.azure_queue_ok: false` (`azure.md` §6a, `contracts/telemetry.md` §2.1). Retry at the next `BOT_QUEUE_POLL_INTERVAL_SEC`; the next successful receive restores `true`. Sweep path unaffected. |
| A DM to a suggestion's original author fails (§6.6) | `discord.Forbidden` or similar from the DM send call | **Resolved this revision:** retried by the reconciliation sweep (§6.6) up to `BOT_SUGGESTION_MAX_DM_ATTEMPTS` combined attempts, then `notification_status` is set to `"failed"` — a terminal state the admin can eventually see reflected on `web/pages/suggestions.md`, rather than an indefinite retry or a silent drop. |
| Mosquitto unreachable — affects progress, heartbeat, and leadership control | Control connection loss | Progress remains best-effort. For safety, immediately enter bounded soft-stop and reject new AI work; hard-stop at drain timeout or earlier grant expiry unless safe control is restored. |
| Active grant expires or Head grant/watchdog disappears | Local monotonic deadline | Hard-stop autonomously; no Head publish is required. |
| **New this revision** — a task's progress/heartbeat ticks stop arriving for longer than `BOT_AI_TASK_STALL_TIMEOUT_SEC` (§3, §6.3's `last_progress_at`) | `Bot`'s own per-task stall timer expires | `Bot` synthesizes an `AiTaskResultFailed` (`contracts/ai_task.md` §4) with `node: "bot_stall_timeout"`, removes the `TaskRecord` (§6.3), and notifies the user — without waiting for RabbitMQ. If the task was actually still alive (e.g. a transient Mosquitto hiccup on `AI Worker`'s side only), the eventual real result is safely discarded on arrival (`ai_task.md` §6) — accepted false-positive cost, not a bug. |
| **New this revision** — a task's total duration exceeds `BOT_AI_TASK_TIMEOUT_SEC`, regardless of how healthy its progress ticks looked | `Bot`'s own per-task overall timer expires | Same synthesis/cleanup as the stall-timeout row, with `node: "bot_task_timeout"` instead — this is the absolute ceiling against a task that's ticking normally but never actually converging. |
| `Bot` process crashes or restarts mid-task (after publishing `ai_tasks`, before consuming the matching `ai_tasks_results`) | N/A — no detection mechanism | The in-memory task map (§6.3), including both timers above, is lost entirely. On restart, if the matching `ai_tasks_results` message still arrives, it has no `TaskRecord` to update and is effectively orphaned — no reconciliation exists. This is now the **only** remaining shape of this gap — a task that survives `Bot`'s own process lifetime is always eventually resolved by either a real result or one of the two timers above. Flagged in §13. |
| `RabbitMQ` unreachable when publishing `ai_tasks` | `send_task` / confirm failure | **Resolved (P1.5):** no `TaskRecord`; localized command error (Phase 2: harness asserts this); Gateway stays up; client reconnects per `rabbitmq.md` §8a. Canonical scenario: S05. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| Discord Gateway | Everything — this is `Bot`'s entire reason to exist | See §9's Gateway-disconnect row. |
| `RabbitMQ` (local) | Publishing `ai_tasks`, consuming `ai_tasks_results` (`/quick-battle` only) | See §9. |
| `Mosquitto` (local) | Leadership control, task progress, heartbeat, logs | Leadership control is safety-critical and fail-closed per §6.1/§6.5; other traffic remains best-effort. |
| Azure Cosmos DB | Guild config (`contracts/guild_config.md`) + Suggestions (`bot/commands/suggest.md`) | See §9 and each command's own doc. |
| Azure Queue Storage | Suggestion-response polling (§6.6) | See §9. |
| Azure Blob Storage (`status.py` document) | Status snapshot push (§6.3) | Not specified beyond the generic Blob Storage failure mode already in `azure.md` §9 — a failed push simply means `Web`'s Dashboard sees a stale `updated_at` until the next successful cycle. |
| Google Gemini API (direct, not via `AI Worker`) | `/config`'s model-listing call only (`bot/commands/config.md` §6) | See that doc's own §12 — unrelated to `AI Worker`'s separate Gemini usage. |
| `discord.py >= 2.6` | Gateway connection, Components V2 UI (`bot/visuals.md` §1) | Hard dependency, not something the system degrades gracefully without. |
| `Head` (local, via Mosquitto only — no direct call) | Activation/drain/stop signals (§6.1, §6.5) — **production** | Without `Head` ever signaling `activate`, production `Bot` simply never connects to Discord at all — this is by design, not a failure mode to recover from. |
| `dev-support` (Compose-only) | Development grants + local repositories | Required when `DCA_RUNTIME_MODE=development`; forbidden in production (`contracts/local_development.md`). |

---

## 11. Health Check

No HTTP endpoint — unlike `Head`/`Launcher`, `Bot` exposes nothing for another service to poll directly. Its liveness/health surface is the Mosquitto heartbeat in `contracts/telemetry.md` §2.1, consumed by `Head`. It includes Gateway state and proportionate per-resource dependency flags; no additional readiness endpoint or active dependency probes are part of v1.

---

## 12. Versioning & Update Behavior

- `Bot` shares the coordinated version tag with `Head`, `AI Worker`, and `Web` (`Launcher.md` §12) — it does not version independently.
- The local container receives required `APPLICATION_VERSION` from Compose; Launcher recreates it only as part of the fixed `head`/`bot`/`ai_worker` image set.
- Participates in the planned update sequence by draining, then completing hard-stop and best-effort acknowledgement **before** Head voluntarily releases the lease (`contracts/leadership_control.md` §6). The drain-completion signal/counts are resolved in `contracts/drain_status.md` (P0.3) — `in_flight_workflows` (§6.3a) is what `Head` actually watches.
- No persistent state to preserve across a restart beyond what already lives in Cosmos DB (`contracts/guild_config.md`) — the in-memory task map (§6.3) and the `bot.draining` flag (§6.4) are both lost on restart by design, and a fresh instance starts clean once `Head` signals `activate` again.

---

## 13. Open Items / Future Work

*(Additive section, mirroring the same pattern already used in `ai_worker.md` §13 and every `graphs/*.md`'s own Open Items — collecting this doc's own surfaced gaps in one place.)*

- Leadership control is resolved in `contracts/leadership_control.md`: retained safe desired mode plus non-retained short-lived grants, including the confirmed hard-stop sequence. ~~The separate P0.3 drain-completion/count contract remained open~~ — **resolved this revision**: `contracts/drain_status.md`.
- ~~No fallback exists if a DM to a suggestion's original author fails (§6.6, §9)~~ — **resolved this revision**: reconciliation sweep + `notification_status: "failed"` terminal state after `BOT_SUGGESTION_MAX_DM_ATTEMPTS`.
- ~~Dangling task-map entries for a task whose result never arrives~~ — **resolved this revision** (§6.3, §9): the stall timer (`BOT_AI_TASK_STALL_TIMEOUT_SEC`) and overall timer (`BOT_AI_TASK_TIMEOUT_SEC`) together guarantee every task is eventually resolved one way or another, as long as `Bot` itself stays alive. **Narrowed, not eliminated:** dangling entries are still possible if `Bot` itself crashes/restarts mid-task, since the timers are in-memory and don't survive that — a task whose result arrives after `Bot` has already forgotten about it (crash, not timeout) is still simply dropped, no reconciliation exists for that specific case.
- **New this revision** — `BOT_AI_TASK_TIMEOUT_SEC=900` and `BOT_AI_TASK_STALL_TIMEOUT_SEC=120` (§3) are proposed defaults, not confirmed against real Gemini/LangGraph timing — see `contracts/ai_task.md` §8 for the full reasoning and the explicit flag that these need revisiting once real latency data exists.
- ~~Whether `Bot` publishes to `ai_tasks` via a Celery client or raw AMQP was undecided~~ — **resolved P0.4 / Phase 2**: `Bot` uses `send_task("ai_worker.tasks.run_graph", ...)` (`contracts/ai_task.md` §2/§3), so `task_id` doubles as a revocable Celery task id and `revoke(terminate=True)` (§6.5) is directly reachable.
- `DISCORD_BOT_TOKEN` (§3) was a previously-undocumented gap across the entire docs tree, not specific to this revision's scope — formalized here for the first time; worth double-checking no other in-progress doc silently assumed a different variable name for it.
- Bot-owned counters beyond the canonical heartbeat are explicitly deferred from v1 (§8); this is a scope decision, not an unresolved transport contract.
- Bot heartbeat dependency health and staleness are resolved in `contracts/telemetry.md` §2 (§11).
- The exact copy/localization key for the "temporarily unavailable during drain" response (§6.4) hasn't been written yet — deferred with `ProcessCommand` to command phases.
- **Phase 2 concurrency, node identity, Celery task name, and transport-shell trigger rules are resolved** — see §6.0 and `docs/to_resolve.md` → Phase 2.
- **Product-development mode** (separate Discord app, guild isolation, `dev-support` grants/providers, suppressed suggestion DM/queue delivery) is resolved in `contracts/local_development.md` and mirrored in §3 / §6.1 / §6.1a. Implementation sequence lives in `to_resolve.md`.
