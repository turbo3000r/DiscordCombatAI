# DiscordCombatAI — Project Documentation

## Overview

**DiscordCombatAI** is a highly available, distributed AI-powered Discord bot. To overcome process blocking and ensure 24/7 uptime across multiple physical machines, the application is divided into decoupled microservices. It utilizes an **Active-Passive (Leader-Follower)** failover strategy, delegating heavy AI generation to background workers, routing web infrastructure through cloud queues, and employing a highly optimized hybrid telemetry model to stream and store performance metrics without incurring massive cloud costs.

---

  

## Technology Stack


| Layer                           | Technology                                                                   |
| ------------------------------- | ---------------------------------------------------------------------------- |
| **Bot framework**               | `discord.py` ≥ 2.6 (slash commands via `app_commands`; ≥2.6 required for Components V2 — see `bot/visuals.md` §1) |
| **AI framework**                | Google Gemini API + **Langgraph** (for complex reasoning loops)              |
| **Worker orchestrator**         | **Celery** (background task management)                                      |
| **Message broker**              | **RabbitMQ** (local task queue for AI generation)                            |
| **Web server**                  | FastAPI + Uvicorn (independent external container)                           |
| **Head Leadership Authority**   | **Azure Blob Lease** (claim/renew mutex; Bot fencing is separately time-bounded and best-effort) |
| **Live Telemetry & Cluster Broadcast** | **Azure Web PubSub** (cheap real-time "who's alive" signal + update broadcast; does **not** itself provide exclusivity — see High-Level Architecture note) |
| **Cloud Event Queue**           | **Azure Queue Storage** (async messaging from Web to Bot)                    |
| **Persistence (NoSQL & Blobs)** | **Azure Cosmos DB** (NoSQL), **Azure Blob Storage**, **Azure Table Storage** |
| **Deployment**                  | Docker Compose (all local containers run simultaneously in wait-state)       |
| **Launcher / Updater**          | Go (compiled binary, runs outside Docker)                                    |
| **Internal Message Broker**     | **Eclipse Mosquitto** (MQTT — logs, metrics signals, service coordination)   |

---

  

## High-Level Architecture

  

``` schema

┌─────────────────────────────────────────────────────────────────┐
│                          AZURE CLOUD                            │
│                                                                 │
│  [Azure Web PubSub] <--- Leader Heartbeat & Live Metrics Stream │
│  [Azure Queue Storage] <--- Suggestions (Web -> Bot messaging)  │
│  [Azure Cosmos DB] <--- Guild Configs, Permanent Suggestions    │
│  [Azure Table] <--- System Metrics                              │
│  [Azure Blob] <--- 1-Minute Batched Logs & Battle logs          │
└──────▲──────────────────────▲──────────────────────▲────────────┘
       │                      │                      │
.......│......................│......................│.............
       │                      │                      │
┌──────▼──────────────────────▼────────┐   ┌─────────┴────────────┐
│    LOCAL NODE (Docker Compose)       │   │ EXTERNAL WEB SERVER  │
│                                      │   │                      │
│ ┌──────────────┐ Leader ┌──────────┐ │   │ ┌──────────────────┐ │
│ │ Head         ├───────►│ Bot      ├─┼───┼─┤ Web (FastAPI)    │ │
│ │ (Watchdog)   │ Signal │ (Active) │ │   │ └──────────────────┘ │
│ └──────────────┘        └─┬────▲───┘ │   │                      │
│                           │    │     │   └──────────────────────┘
│            AI Task        │    │     │
│            (Push)         ▼    │     │
│                         ┌──────────┐ │
│                         │ RabbitMQ │ │
│                         └─┬────▲───┘ │
│                           │    │     │
│            AI Task        │    │     │
│            (Pull)         ▼    │     │
│                         ┌──────────┐ │
│                         │ AI Worker│ │
│                         │ (Celery+ │ │
│                         │Langgraph)│ │
│                         └──────────┘ │
└──────────────────────────────────────┘

  

```

*Note: Multiple "LOCAL NODE" setups can run simultaneously on different physical PCs. `Launcher` lives outside Docker on the host. Head → Launcher uses authenticated host-reachable TCP; Launcher → Head uses a host-loopback-published container port. Exact Linux/Windows mapping, firewall, HMAC, and schemas are in `contracts/launcher_ipc.md`. Launcher has no Azure or RabbitMQ access.*

*Leader election and Bot fencing: Web PubSub group membership is additive and its canonical `leader_heartbeat` is informational only. A fixed Azure Blob Lease is Head's leadership authority. The lease winner generates an opaque leadership-term UUID and may issue its local Bot short-lived, non-retained activation grants. Retained desired mode can contain only `inactive`, `draining`, or `stopped`; Head publishes `inactive` before election, and Bot defaults inactive whenever control/grant is absent. Grant and heartbeat timers use elapsed local monotonic time. The full schemas and failure/demotion protocol are in `contracts/leadership_control.md`. These safeguards reduce overlap but do **not** prove strict at-most-one Gateway-connected Bot in every partition/delay; bounded dual-active overlap is an accepted availability/safety limitation.*

*Mixed-version compatibility (P0.3 + P0.5.5, resolved): every durable/shared contract carries `schema_version` (see `contracts/drain_status.md` §5, extended to suggestion/telemetry/status/battle archive/logs/queue/pubsub). Receivers reject unknown versions. `Web` tolerates one prior and one following additive schema for documents it reads. No cluster-wide update barrier.*

### Environments: production vs product development

Production topology above (Head + Blob Lease + Azure-mediated Web) is unchanged. **Product-development mode** is a separate, intentionally incomplete stack for Discord command/UI and Web page exercise — canonical contract: `contracts/local_development.md`.

| | Production | Product development |
|---|---|---|
| Discord | Production application/token; rejects reserved `DISCORD_DEVELOPMENT_GUILD_ID` | **Separate** Discord application/token; guild-scoped sync + accept only that guild |
| Persistence | Azure via domain repository adapters wrapping `src/shared/azure/` | Local adapters → Compose-only `dev-support` (SQLite); **no** Azure clients |
| Activation | Head + Blob Lease grants | `dev-support` publishes Mosquitto grants; **no** Head/Launcher |
| Web | Standalone; Entra; Azure PubSub live | Compose-included; fixed local admin; local live feed |
| Validates | S01–S13 production acceptance | S14 isolation only — **does not** claim Azure failover fidelity |

**Rejected:** a shared “mirror Azure API” container or guild-filter against production Azure as the Bot↔Web integration plane.

---

  

## Container Breakdown (Docker Compose)

### `Head` — The Session Coordinator (Watchdog)
* **Role:** Handles Leader Election, node consensus, and hybrid telemetry gathering.
* **Behavior:** Joins a single, permanent **Azure Web PubSub** cluster broadcast group on startup — every `Head`, leader or follower, stays in this one group for its entire process lifetime (cheap, push-based). Listens for the current leader's heartbeat message in that group.
* **Active state:** If no heartbeat is observed within timeout, it attempts to acquire the Blob Lease. The winner creates a leadership term and issues renewable short-lived activation grants over Mosquitto; a heartbeat or retained message alone cannot activate Bot.
* **Telemetry routing:** Every **60 seconds**, the **leader** `Head` pushes a batch of metrics to **Azure Table Storage** and appends logs to **Azure Blob Storage** (`contracts/telemetry.md`, `contracts/log_archive.md`). Followers do not upload dashboard telemetry. While leader, it **always** streams live batches to the `dashboard-live` Web PubSub group every **10 seconds** — no listener detection (`contracts/pubsub_live.md`).
* **Passive state:** If a leader heartbeat is already present in the broadcast group, it enters standby mode. If the leader's heartbeat stops arriving (e.g., PC crashes), every follower observes the gap and races for the Blob lease; only the winner takes over and activates its local `Bot`.
* **Coordination failures:** PubSub-only, Blob-renew-only, and Mosquitto failures soft-stop Bot (immediate bounded drain, reject new AI work, then hard-stop at timeout unless safely restored). Blob-renew-only recovery requires confirmation of the same lease term plus a fresh same-term grant. Simultaneous loss of both Azure coordination dependencies, known non-leadership, Head watchdog/grant expiry, or Head crash causes hard-stop.
* **Log aggregation:** Subscribes to `logs/#` on `Mosquitto`. Parses incoming structured log lines, buffers them in memory, and flushes to **Azure Blob Storage** alongside the existing 60-second telemetry batch.
* **Service coordination:** Uses retained desired modes plus non-retained Bot grants (`contracts/leadership_control.md`) and retained AI Worker desired state. HTTP is reserved for authenticated Head ↔ Launcher IPC.

### `Bot` — The Discord Interface
* **Role:** The only component that connects to the Discord Gateway.
* **Behavior:** Starts alongside other containers but **does not execute `bot.run()**` until authorized by the local `Head`.
* **Inbound data:** Listens to Discord slash commands. Periodically polls **Azure Queue Storage** for new web-submitted `Suggestions`.
* **Outbound data:** Instead of waiting for AI, it immediately pushes the battle generation prompt to the local **RabbitMQ** queue and tells the user "Battle is generating...". Reads the finished story from RabbitMQ and posts it to Discord.
* **New Suggestions**: If new suggestion is added via bot command, adds it to `Azure Cosmos DB`
* **On `Head` crash/disappearance:** The activation grant/watchdog expires and Bot autonomously runs the hard-stop sequence; no final Head publish is required (`contracts/leadership_control.md` §5).
* **Activation signal:** `Bot` subscribes to retained `control/bot/desired_state` (safe modes only) and non-retained `control/bot/activation_grant`. A current grant is mandatory to connect; exact behavior is in `contracts/leadership_control.md`.
* **Full container-level design** (env vars, guild config schema, task-progress plumbing, background services) now lives in `docs/containers/bot/discord_bot.md` — not duplicated here.

### `RabbitMQ` — Local Message Broker
* **Role:** Facilitates asynchronous communication strictly between the `Bot` and the `AI Worker(s)`.
* **Behavior:** Holds queues for `ai_tasks` and `ai_tasks_results`, both dead-letter-configured (`contracts/ai_task.md` §9). Manual ack after result-publish ensures no tasks are lost if an AI Worker crashes mid-generation. **Delivery guarantee — corrected, P0.4:** at-least-once delivery, effectively-once outcome, not "exactly-once" — manual ack bounds duplicate generation to the crash window, and `contracts/ai_task.md` §6's discard-by-unknown-`task_id` logic makes the user-visible outcome effectively-once. **Wire protocol — resolved, P0.4 / Phase 2:** `ai_tasks` is a real Celery queue (`Bot` dispatches via `send_task("ai_worker.tasks.run_graph", ...)`, `AI Worker` is `celery -A ai_worker.celery_app worker -Q ai_tasks`); `ai_tasks_results` stays a plain, manually-published queue — Celery's own result backend is never used. `task_id` = the Celery task id = the AMQP `correlation_id` = the domain idempotency key.
* **Response:** Each message dispatched to `ai_tasks` (via Celery `send_task`, `contracts/ai_task.md` §2) should eventually get its response manually published to `ai_tasks_results`, correlated by `correlation_id = task_id` — `reply_to` is not used.
* **Local**: `RabbitMQ` is local and local only — there is no cross-node routing of any kind. A non-leader node's `AI Worker` is not processing work "for" the cluster leader; its local `ai_tasks` queue simply never receives anything, because only the active leader's local `Bot` ever publishes into its own local queue.
* **Soft stop (`drain`):** stops accepting *new* `/quick-battle` requests only; anything already in `ai_tasks`/in-flight is left to finish normally, bounded by `contracts/drain_status.md`'s drain protocol (P0.3). Used for planned updates.
* **Hard stop (`stop`), formerly "`Head` light crash":** In case of internet failure, `Bot` termination, or a drain-timeout escalation (`contracts/drain_status.md` §2), all messages in `ai_tasks`  are purged, and any `AI Worker` execution already claimed and running is actively terminated (`Bot`-issued Celery `revoke(task_id, terminate=True)`, not left to finish, `Bot` is the resolved actor, `contracts/ai_task.md` §8) — an `AiTaskResultFailed` message (`contracts/ai_task.md` §4, `node: "worker_terminated"`) is added to `ai_tasks_results` for each purged/terminated task. `Bot` delivers that error back to each task's originating Discord thread before disconnecting from the Gateway — see `bot/discord_bot.md` §6.5 for the exact sequencing.

### `Mosquitto` — Internal Pub/Sub Broker

* **Role:** Facilitates lightweight, fire-and-forget communication between all local containers: structured log aggregation, internal coordination signals, and service-to-service control commands. Operates independently from `RabbitMQ`, which remains dedicated exclusively to the AI task request/response queue.
* **Behavior:** A standard Mosquitto container with no custom modifications, configured with local-only access (no external port exposure) and persistent session support disabled (logs/control are transient by nature). Control/drain topics use QoS 1; logs, progress, and heartbeats use QoS 0; heartbeats are never retained (`mosquitto.md` §6, P1.5).
* **Topic Structure:**

| Topic Pattern | Publishers | Subscribers | Purpose |
|---|---|---|---|
| `logs/info/<service>` | All services | `Head` | Informational log records |
| `logs/warning/<service>` | All services | `Head` | Warning-level log records |
| `logs/errors/<service>` | All services | `Head` | Error/critical log records |
| `control/bot/desired_state` | `Head` | `Bot` | QoS 1 retained safe mode: `inactive \| draining \| stopped`; never active. |
| `control/bot/activation_grant` | `Head` | `Bot` | QoS 1 non-retained short-lived `active \| draining` authorization. |
| `status/bot/control_ack` | `Bot` | `Head` | Best-effort applied-state acknowledgement for bounded demotion/update waiting. |
| `control/ai_worker/desired_state` | `Head` | `AI Worker` | QoS 1, retained. `{"schema_version": 1, "state": "running" \| "paused"}` — pause/resume task consumption, node-local. |
| `status/ai_worker/pause_ack` | `AI Worker` | `Head` (diagnostic only) | QoS 1, not retained. **New this revision (P0.3).** Published once `AI Worker` goes idle after a pause request — informational only, does not gate drain completion. See `contracts/drain_status.md` §3. |
| `status/bot/drain_progress` | `Bot` | `Head` | QoS 1, not retained. **New this revision (P0.3).** `{in_flight_workflows, ...}` published every `BOT_DRAIN_PROGRESS_INTERVAL_SEC` while draining — gives `Head` real drain-completion visibility instead of inferring it from empty RabbitMQ queues. Canonical: `contracts/drain_status.md` §1. |
| `status/bot/heartbeat` | `Bot` | `Head` | Versioned Gateway/latency/guild/dependency health; 30s default, stale after 90s (`contracts/telemetry.md` §2). |
| `status/ai_worker/heartbeat` | `AI Worker` | `Head` | Versioned run/pause, active-task, and RabbitMQ health; 30s default, stale after 90s (`contracts/telemetry.md` §2). |
| `progress/ai_worker/<task_id>` | `AI Worker` | `Bot` | Best-effort task-phase updates (`queued`/`launching`/`composing`/`refining`/`finishing`) for a running `ai_tasks` job, so `Bot` can render a live status bar in Discord. Deliberately **not** on `RabbitMQ` — a dropped tick is harmless (the next tick or the final `ai_tasks_results` message still lands), which is exactly the tolerance this broker is for. See `docs/contracts/task_progress.md` for the full contract. |

* **Log Message Format:** Every service publishes log records as a single structured string, regardless of topic level:  `[%time%][%level%][%service%][%file/module%]<any additional tags: trace_id, command, guild_id>: [%message%]`
* `Head` is the sole subscriber responsible for parsing this format, batching records, and forwarding them to **Azure Blob Storage** every 60 seconds (per the existing hybrid telemetry model). * **RabbitMQ Log Bridging:** A lightweight bridge process (hosted within `Head`) subscribes to RabbitMQ's `rabbitmq_event_exchange` plugin and re-publishes relevant broker-level events (consumer disconnects, queue overflows, channel errors) onto `logs/warning/rabbitmq` or `logs/errors/rabbitmq`, normalized to the standard log format above. This keeps `Head` as the single point of log aggregation without requiring direct AMQP event subscriptions from unrelated services. * **Isolation principle:** `Mosquitto` is strictly internal to the Docker Compose network. It is never exposed externally and is not used for AI task distribution, which remains the responsibility of `RabbitMQ`.
### `AI Worker` — The Generation Engine
* **Role:** Handles the blocking/heavy AI logic.
* **Behavior:** A **Celery** worker running in an infinite loop. It pulls a task from **RabbitMQ**, initializes a **Langgraph** state machine, interacts with the Gemini API to resolve the battle logic, and pushes the final JSON/text back to RabbitMQ.
* **Credentials are per-guild, not global:** `AI Worker` holds no Gemini credential of its own. Each `ai_tasks` message carries the requesting guild's own `api_key` and `model` (sourced from `contracts/guild_config.md`, staged via `/config`) — confirmed gap closed in this revision, see `ai_worker.md` §3/§4. **Accepted risk, not solved here:** the key travels plaintext on the message and is held plaintext in Cosmos DB (`contracts/guild_config.md` §7) — a per-user-supplied-API-key, cheap/self-hosted deployment model makes an Azure Key Vault indirection impractical for v1; this is a deliberate, documented trade-off to revisit if the project's cost model ever changes, not an oversight.
* **Scaling:** Can be scaled to multiple instances per PC, and to additional PCs (each running its own local `RabbitMQ` + `AI Worker` pair). Always running, even if the local `Head` is NOT the leader — but a non-leader node's `AI Worker` simply idles, since its local `ai_tasks` queue never receives anything (RabbitMQ is strictly node-local, no cross-node task routing — see the corrected `RabbitMQ` note above).
* **Coordination signal:** Subscribes to `control/ai_worker/desired_state` (retained `{"schema_version": 1, "state": "running" | "paused"}`) on `Mosquitto`, allowing the **local** `Head` to halt task consumption on its own node during an update or maintenance window, without touching RabbitMQ queue state directly. On `paused`, `AI Worker` finishes any task already claimed, stops claiming new ones, then publishes `status/ai_worker/pause_ack` once idle — informational only, does not gate `Head`'s drain-complete decision (`contracts/drain_status.md` §3, P0.3). This is node-local, not cluster-wide — each node's `Head` only ever controls its own local `AI Worker`.

### `Web` — The Independent Dashboard (Remote, not included at docker-compose but is container like)


* **Role:** FastAPI application serving the dashboard and user suggestions UI.
* **Behavior:** Completely decoupled from the Bot's local network. It has no direct connection to RabbitMQ or the Bot container.
* **Integration:** When an admin submits a response to a suggestion, the `Web` container writes it to **Azure Cosmos DB** and sends a notification payload into **Azure Queue Storage** (`contracts/suggestion.md`). For live dashboard stats, the **browser** connects to Web PubSub (`dashboard-live`); `Web` only negotiates the token (`contracts/pubsub_live.md`) — it does not listen to PubSub itself.
* **Authentication (P0.7 resolved):** Microsoft Entra ID is the Web admin security boundary — single-tenant SPA (MSAL.js, Authorization Code + PKCE), Bearer JWT on all `/api/*`, authorization via Entra security group (`WEB_ENTRA_ADMIN_GROUP_ID`). Static SPA shell remains public; unauthenticated API → **401**, authenticated non-admin → **403**. Web **may** be internet-reachable; VPN/private network is optional hardening, not the v1 minimum. Canonical: `contracts/web_auth.md`.
### `Launcher` — The Update Orchestrator (Host Process, NOT in Docker Compose)

* **Role:** Lives on the host machine as a system service (systemd/Windows Service). Survives container restarts and is the only component capable of pulling new images and recreating containers.
* **Behavior:** Listens on host-reachable authenticated HTTP for an idempotent update request from local `Head`. On signal, it:
    1. Trusts Head's request only after HMAC/replay validation; Head performs Bot stop/ack and lease-release ordering before calling.
    2. Uses the Go Docker API to ping the daemon and pull the fixed local application image map: `head`, `bot`, and `ai_worker` at the admitted tag.
    3. Uses a controlled, no-shell Docker Compose CLI invocation to recreate only those services, injecting required `APPLICATION_VERSION=<target_version>`.
    4. Verifies authenticated Head liveness and exact `version == target_version`; it does not wait for leadership, Bot, or dependency readiness.
    5. If recreate/verification fails, attempts automatic rollback once. A failed rollback stops automatic recovery.
* **Concurrency/restart safety:** HTTP and CLI share one coordinator, persisted operation state, and a host-wide cross-process lock. A Launcher restart marks active work `INTERRUPTED`; it never blindly resumes Compose and requires explicit reconciliation/new admission.
* **Manual mode:** Exposes a CLI (`launcher update --version vX.Y.Z`, `launcher rollback`, `launcher status`) for direct administrator control. Manual rollback is a separate admission, not the automatic rollback attempt.
* **Isolation principle:** The Launcher is intentionally excluded from `docker-compose.yml`. It must never be restarted as a side effect of the update process it itself triggers.


*Note: > **Design boundary:** `RabbitMQ` and `Mosquitto` serve fundamentally different communication patterns and must not be merged or substituted for one another:
> - `RabbitMQ` — at-least-once, effectively-once task delegation requiring acknowledgment, retry, and single-consumer guarantees (AI generation tasks). **Corrected this revision (P0.4):** previously said "exactly-once" — RabbitMQ's manual-ack/redelivery model is at-least-once at the transport level; `contracts/ai_task.md` §2/§6 defines how the pipeline still produces an effectively-once user-visible outcome.
> - `Mosquitto` — best-effort broadcast for logs, metrics signals, and coordination commands where occasional message loss is acceptable and multiple subscribers may exist.* 

---
## Data Storage

  

| What                         | Where                               | Format / Technology             | Storage Frequency                               |
| ---------------------------- | ----------------------------------- | ------------------------------- | ----------------------------------------------- |
| Leader Lease (mutex)          | `Azure Blob Storage`                | Single leased blob, fencing via lease ID | Acquired on claim; renewed every `HEAD_ELECTION_HEARTBEAT_SEC` (default 30s) — low frequency by design, see corrected High-Level Architecture note |
| Leader Heartbeat / Update Broadcast | `Azure Web PubSub`             | Real-time WebSocket, one permanent cluster group | Constant — heartbeat every `HEAD_ELECTION_HEARTBEAT_SEC` (default 30s); `update_available` broadcast on demand |
| Cross-System Events          | `Azure Queue Storage`               | JSON (Web -> Bot messaging)     | Check for new messages every 5 minutes          |
| Guild Configurations         | `Azure Cosmos DB`                   | NoSQL Documents                 | On-demand                                       |
| User Suggestions             | `Azure Cosmos DB`                   | NoSQL Documents                 | On-demand                                       |
| Performance Metrics          | `Azure Table Storage`               | `NodeMetrics` rows (`contracts/telemetry.md`) | Batched 60s, **leader only**                    |
| Active Logs                  | `Azure Blob Storage`                | Append blob (`contracts/log_archive.md`) | Batched 60s, **leader only**                    |
| Live Telemetry Stream        | `Azure Web PubSub`                  | `telemetry_live` to `dashboard-live` | Every 10s **while leader** (always-stream)      |
| Status Document              | `Azure Blob Storage`                | Nested JSON (`contracts/status_document.md`) | Bot pushes `status`; Web owns identity/catalog |
| Battle Results               | `Azure Blob Storage`                | `.txt` + `.meta.json` (`contracts/battle_archive.md`) | On-demand after Discord-ready |
| Local AI Task Queue          | `RabbitMQ` (Docker Vol.)            | AMQP format                     | On-demand                                       |
| Internal Service Logs        | `Mosquitto` (MQTT, in-transit only) | Plain structured text           | Real-time, batched to Blob every 60s by `Head`  |
| Service Coordination Signals | `Mosquitto` (MQTT, in-transit only) | Plain text / JSON payload       | On-demand (activation, shutdown, pause/resume)  |

---

  

## How the Parts Connect (Call Flow Summary)


### Scenario 1: System Boot & Failover
1. PC turns on → `docker-compose up` starts `Head`, `Bot`, `RabbitMQ`, `Mosquitto`, `AI Worker`.
2. `Bot` starts inactive. No control connection/current grant means no Gateway connection.
3. `Head` connects to Mosquitto and publishes retained `inactive` before election; inability to do so prevents activation.
4. `Head` joins Web PubSub. If no leader heartbeat arrives within timeout, it attempts to acquire the Blob Lease.
5. On successful acquisition, `Head` creates a leadership-term UUID and sends a fresh non-retained activation grant. Only then does `Bot` connect.
6. The leader renews lease, heartbeat, and grants. Followers use heartbeat receipt time only to decide when to attempt the lease.
7. If Head crashes, Bot hard-stops autonomously on grant/watchdog expiry. A follower can activate only after acquiring the lease and issuing its own fresh local grant.

### Scenario 2: Generating a Battle
1. User types `/quick-battle` in Discord.
2. `Bot` formats the request and pushes to `RabbitMQ` (`ai_tasks` queue).
3. `Bot` instantly returns a "Please wait" message to Discord (no blocking).
4. `AI Worker` picks up the task, processes it via `Langgraph` & Gemini API.
5. `AI Worker` pushes the result to `RabbitMQ` (`ai_tasks_results` queue).
6. `Bot` consumes the result, archives the story to **Azure Blob Storage** (`contracts/battle_archive.md`, best-effort), and sends the final message to the Discord channel.

### Scenario 3: Suggestion Submission
1. User submits a form using command `/suggest`.
2. Bot sends json with all the information to `Azure Cosmos DB`
3. `Web` loads suggestion from `Azure Cosmos DB` when opened.
4. Admin writes response to suggestion.
5. `Web` saves the full suggestion to `Azure Cosmos DB`.
6. `Web` pushes a lightweight notification event into `Azure Queue Storage`.
7. The active `Bot` (polling the queue) receives the event and sends a **DM to the original suggester** (via the `contact.user_id` field already recorded on the suggestion, `docs/containers/bot/commands/suggest.md` §9). **Corrected in this revision** — this line previously said "sends an Embed to the Discord Admin Channel," which conflicted with `web/pages/suggestions.md` and the suggestion payload's own `contact.method: "dm"` field; see `docs/containers/bot/discord_bot.md` §6.6 for the confirmed resolution.

### Scenario 4: Telemetry & Log Monitoring (Hybrid Cost-Saving Mode)
1. The **leader** `Head` continually samples CPU/RAM and ingests Mosquitto logs; it also samples Bot `status/bot/heartbeat` for latency/guild_count (`contracts/telemetry.md`).
2. Every **60 seconds**, leader `Head` bulk-writes metrics to **Azure Table Storage** and appends logs to **Azure Blob Storage**. Followers do not upload.
3. A user opens the Web Dashboard. The browser fetches history via REST (`/api/metrics/history`) and current “now” latency/guilds from `status.py`.
4. The browser calls `GET /api/pubsub/negotiate` on `Web`, then connects **directly** to **Azure Web PubSub** group `dashboard-live` (join/leave only).
5. Leader `Head` **always** publishes `telemetry_live` every **10 seconds** while leader — no subscribe-event / listener detection. Caps keep Free_F1 budget safe (`contracts/pubsub_live.md`).
6. Closing the browser does not change Head’s stream behavior; empty-group delivery costs ≈0 outbound messages.
### Scenario 5: Automatic Update Flow

**Corrected in this revision:** the previous version of this scenario had only the leader's own `Launcher` ever receiving the update signal, with no mechanism for follower nodes to learn about it at all — they would stay on the old version indefinitely and could later win leader election while incompatible. The fix: every `Head` (leader and follower) is already permanently joined to the same cluster broadcast group used for the leader heartbeat (see High-Level Architecture note), so the `update_available` broadcast reaches everyone over that same cheap channel, and **each node's `Head` acts on it independently**, not just the leader's.

1. The active leader `Head` periodically polls the GitHub Releases API for a newer valid tag using parsed SemVer precedence (`contracts/launcher_ipc.md` §4). Drafts are always ignored and prereleases are ignored by default.
2. On detecting a newer version, the leader `Head` publishes an `update_available` event (`{"schema_version": 1, "type": "update_available", "target_version": "vX.Y.Z"}` — canonical: `contracts/drain_status.md` §4a) into the cluster broadcast group — every `Head` in the cluster, leader or follower, receives it immediately (they're all already members, per the corrected election design).
3. **Leader's own path:** publish retained `draining`, issue only bounded draining grants, and wait for drain completion/timeout — resolved in `contracts/drain_status.md` (P0.3): `Head` watches `Bot`'s `status/bot/drain_progress` (`in_flight_workflows`) rather than inferring completion from empty RabbitMQ queues, and transitions to `UPDATING` when that count reaches zero or `HEAD_DRAIN_TIMEOUT_SEC` elapses, whichever comes first — timeout escalates to the hard-stop sequence rather than silently abandoning work. Then command the hard-stop sequence, wait boundedly for `gateway_connected: false`, release the lease, and send authenticated idempotent `POST /v1/update` to `http://host.docker.internal:${LAUNCHER_IPC_PORT}`. Missing Bot acknowledgement is logged; update may continue after timeout under the accepted overlap limitation.
4. **Follower path (new in this revision):** a follower `Head` has no active `Bot` to drain, so on receiving the broadcast it signals its own local `Launcher` directly, without any drain step.
5. Every node's `Launcher` independently uses the Go Docker API to pull the fixed `head`/`bot`/`ai_worker` images, then uses the controlled Compose CLI to recreate those application services with `APPLICATION_VERSION=target_version`. Broker image pins are untouched.
6. New `Head` instances re-enter leader election (rejoin the broadcast group, listen for a heartbeat, race for the Blob Lease if none is heard) as usual.
7. Each Launcher polls authenticated `GET http://127.0.0.1:${HEAD_IPC_HOST_PORT}/v1/health`; verification requires `status: "alive"` and exact target version, not dependency readiness.
8. If verification fails within the window, that node's Launcher attempts automatic rollback once. Manual `launcher rollback` remains a distinct operator operation.
9. An administrator may bypass steps 1–2 entirely via `launcher update --version vX.Y.Z` for manual control, on any node individually.

---

## Target Compose skeleton (Phase 0 / P1.5)

> Paths and service names below match the **checked-in** `docker-compose.yml` / `infra/` layout. Brokers and Head start by default; Bot and AI Worker use an explicit Compose profile (below). Full operational detail for brokers: `containers/rabbitmq.md`, `containers/mosquitto.md`.

**Services (local node, production):** `mosquitto`, `rabbitmq`, `head`, `bot`, `ai_worker`. **`web` is excluded** (deployed independently). **`launcher` is excluded** (host binary). **`dev-support` is excluded.**

**Services (product development):** `mosquitto`, `rabbitmq`, `bot`, `ai_worker`, `web`, `dev-support`. **`head` and `launcher` are excluded.** Canonical rules: `contracts/local_development.md` §2. Opt-in only via `docker-compose.dev.yml` overlay + `DCA_RUNTIME_MODE=development`.

**Profiles:** `bot` and `ai_worker` use Compose profile `application`. Default `docker compose up` brings up brokers + Head only. Start transport peers with `--profile application` (or Launcher recreate of the fixed application image set). This keeps Head fencing testable without requiring Bot/Worker images during early slices. Development adds a `development` profile (or equivalent overlay services) for `dev-support` and Compose-included Web — never silently enable those in production compose.

**Networks:** one internal bridge (`dca-internal`). Production compose publishes **no** host ports for Mosquitto `1883` or RabbitMQ `5672`/`15672`. Head IPC remains host-loopback only per `contracts/launcher_ipc.md`.

**Volumes:** `rabbitmq-data` (sensitive — plaintext task payloads, see `rabbitmq.md` §13); optional Mosquitto config bind-mount only (persistence disabled).

**Health / ordering:** `bot` and `ai_worker` `depends_on` RabbitMQ and Mosquitto with `condition: service_healthy`. `head` `depends_on` Mosquitto healthy (and may wait on RabbitMQ healthy before enabling the event bridge).

**Broker mounts:**
- `./infra/mosquitto/mosquitto.conf` → Mosquitto config
- `./infra/rabbitmq/enabled_plugins`, `rabbitmq.conf`, and `definitions.json` (canonical topology) → RabbitMQ

**Prompts:** `ai_worker` bind-mounts `./prompts` (or image-copies at build) read-only — exact path ownership for generic arenas remains a P1.1 item; Compose must still reserve the mount point.

**Restart:** `unless-stopped` for all local services.

**Image pins (brokers):** `eclipse-mosquitto:2.0.20` (patch-pin in real Compose), `rabbitmq:3.13-management`. Application images use the coordinated release tag.

**Application version injection:** Compose requires `APPLICATION_VERSION` (no default) and injects the same value into `head`, `bot`, and `ai_worker`. Their fixed image references are `${LAUNCHER_GHCR_NAMESPACE}/head:${APPLICATION_VERSION}`, `${LAUNCHER_GHCR_NAMESPACE}/bot:${APPLICATION_VERSION}`, and `${LAUNCHER_GHCR_NAMESPACE}/ai_worker:${APPLICATION_VERSION}`. During Launcher operations, the coordinator supplies the admitted target as the child Compose process's `APPLICATION_VERSION`; direct/manual Compose use must set it explicitly. Each application process fails startup if the value is absent or does not match `contracts/launcher_ipc.md` §4.

**Node identity injection (Phase 2):** Compose reads one host-level `NODE_ID` and injects it as `HEAD_NODE_ID`, `BOT_NODE_ID`, and `AI_WORKER_NODE_ID`. All three must match on a deployment node. Grammar: `^[A-Za-z0-9._-]+$`, length 1–128.

**Broker URL / vhost:** Compose injects `RABBITMQ_DEFAULT_VHOST` (default `/discordcombatai`) into `head`, `bot`, and `ai_worker`. Clients build `amqp://{user}:{pass}@{host}:{port}/{quote(vhost, safe="")}` (`contracts/ai_task.md` §2).

**RabbitMQ topology:** `infra/rabbitmq/definitions.json` is canonical (not optional). Clients verify; they do not own competing declares.

---

## Project File Structure

```
discord-combat-ai/
│
├── docker-compose.yml           # Production local node (Head, Bot, RabbitMQ, Mosquitto, AI Worker)
├── docker-compose.dev.yml       # Dev overlay: mounts, loopback ports, hot reload, development services
│                                # (web + dev-support). Not data-plane isolation by itself —
│                                # see contracts/local_development.md
├── infra/                       # Target: broker configs (not application code)
│   ├── mosquitto/
│   │   └── mosquitto.conf
│   └── rabbitmq/
│       ├── enabled_plugins
│       ├── rabbitmq.conf
│       └── definitions.json     # canonical topology owner — see rabbitmq.md §2
├── pyproject.toml               # Single workspace root — all dependencies defined here
├── .env                         # Actual secrets (gitignored)
├── .env.example                 # Template with all required variables and descriptions
├── .dockerignore
├── .gitignore
├── launcher/                      # Go binary — runs on host, outside Docker Compose
│   ├── go.mod
│   ├── go.sum
│   ├── main.go                   # Entry point: CLI parsing + daemon mode
│   ├── cmd/
│   │   ├── update.go             # `launcher update --version vX.Y.Z`
│   │   ├── rollback.go           # `launcher rollback`
│   │   └── status.go             # `launcher status`
│   ├── internal/
│   │   ├── coordinator/
│   │   │   └── coordinator.go    # Shared HTTP/CLI admission and operation state machine
│   │   ├── ipc/
│   │   │   └── listener.go       # Authenticated host TCP listener for Head signals
│   │   ├── docker/
│   │   │   ├── ping.go           # Go Docker API: daemon ping
│   │   │   ├── pull.go           # Docker Engine API: image pull
│   │   │   └── auth.go           # GHCR pull authentication
│   │   ├── compose/
│   │   │   └── recreate.go       # Controlled docker compose CLI; fixed services, no shell
│   │   ├── healthcheck/
│   │   │   └── verify.go         # Polls Head's health endpoint post-update
│   │   └── state/
│   │       ├── version_history.go # Tracks current + previous version tags for rollback
│   │       └── process_lock.go    # Host-wide daemon/CLI operation lock
│   ├── install/
│   │   ├── launcher.service       # systemd unit file (Linux)
│   │   └── launcher.exe.config    # Windows Service wrapper config
│   └── Dockerfile.build     # Multi-stage build to compile binary (not for runtime)
|
├── src/
│   │
│   ├── shared/                  # Internal library, imported by all services
│   │   ├── azure/               # Production provider implementations (never loaded in development)
│   │   │   ├── services/        # Shared Azure implementations
│   │   │   │   ├── suggestions.py #  + Azure Queue Storage client wrappers
│   │   │   │   ├── guilds.py    # Guild configs, and battle logs
│   │   │   │   ├── status.py    # Shared status document sections; version is not stored here
│   │   │   │   ├── logging.py   # Bot logs, Send And Receive
│   │   │   │   ├── metrics.py   # Metrics load and unload from Table Storage
│   │   │   │   └── guild_logs.py # Blob Storage client wrapper
│   │   │   ├── clients/         # Azure clients 
│   │   │   │   ├── cosmos.py    # Azure Cosmos DB client wrapper
│   │   │   │   ├── queue.py     # Azure Queue Storage client wrapper
│   │   │   │   ├── blob.py      # Azure Blob Storage client wrapper
│   │   │   │   ├── pubsub.py    # Web PubSub client wrapper
│   │   │   │   └── table.py     # Azure Table Storage client wrapper
│   │   │   ├── configs/         # Azure config
│   │   │   └── models/          # Azure related Pydantic models
│   │   ├── storage/             # Domain repository ports + factory (DCA_RUNTIME_MODE)
│   │   │   ├── protocols.py     # Guild/Suggestion/Status/Metrics/Archive ports
│   │   │   ├── factory.py       # azure vs local adapter selection
│   │   │   ├── azure_adapters.py
│   │   │   └── local_adapters.py # HTTP clients → dev-support
│   │   ├── models/              # Pydantic models shared across services
│   │   └── utils/               # Generic helpers (logging, retry logic, etc.)
│   │
│   ├── dev_support/             # Compose-only product-dev service (contracts/local_development.md)
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   ├── api/                 # Internal REST for repository adapters
│   │   ├── store/               # SQLite persistence
│   │   ├── grants.py            # Mosquitto activation-grant publisher
│   │   └── live.py              # Local dashboard live feed
│   │
│   ├── head/                    # Container: Session Coordinator / Watchdog
│   │   ├── Dockerfile
│   │   ├── entrypoint.sh
│   │   ├── main.py              # Entry point: leader election loop
│   │   └── modules/
│   │       ├── election.py      # Blob Lease authority + Web PubSub heartbeat + Bot grants
│   │       └── telemetry.py     # Metrics sampling, batching and routing
│   │
│   ├── bot/                     # Container: Discord Interface
│   │   ├── Dockerfile
│   │   ├── entrypoint.sh
│   │   ├── main.py              # Entry point: waits for Head signal, then runs bot
│   │   ├── modules/             # all the bot code 
│   │   │   ├── client.py        # discord.py Bot subclass
│   │   │   ├── configs/
│   │   │   ├── UI/              # bot scope UI elements
│   │   │   |   ├── embeds/
│   │   │   |   ├── modals/
│   │   │   |   └── views/
│   │   │   ├── events/          # guild_events.py, control_events.py — docs/containers/bot/discord_bot.md §6
│   │   │   ├── services/        # NEW — container-wide background infra (task tracking, heartbeat, guild
│   │   │   │                    # sync), not owned by any single command — discord_bot.md §2
│   │   │   └── commands/        # bot commands
│   │   │       ├── suggestions/
│   │   │       │   ├── UI/      # command graphics elements 
│   │   │       │   |   ├── embeds/
│   │   │       │   │   ├── modals/
│   │   │       │   |   └── views/
│   │   │       │   ├── service/ # command main logic 
│   │   │       │   |   ├── queue_poller.py # Polls Azure Queue Storage 
│   │   │       │   |   └── suggestion_service.py
│   │   │       │   ├── command.py # initilize command
│   │   │       │   └── models.py # command related models
│   │   │       ├── battle/
│   │   │       │   ├── UI/      # command graphics elements 
│   │   │       │   |   ├── embeds/
│   │   │       │   │   ├── modals/
│   │   │       │   |   └── views/
│   │   │       │   ├── service/ # command main logic 
│   │   │       │   |   ├── environment.py # Optional
│   │   │       │   |   └── battle_process.py 
│   │   │       │   ├── command.py # initilize command
│   │   │       │   └── models.py # command related models
│   │   │       └── config/
│   │   │           ├── UI/      # command graphics elements 
│   │   │           |   ├── embeds/
│   │   │           │   ├── modals/
│   │   │           |   └── views/
│   │   │           ├── service/ # command main logic 
│   │   │           |   └── guild_configuration_service.py 
│   │   │           ├── command.py # initilize command
│   │   │           └── models.py # command related models
│   │   └── localization/
│   │       ├── handler.py
│   │       └── lang/
│   │           ├── en.json
│   │           ├── es.json
│   │           └── ua.json
│   │
│   ├── ai_worker/               # Container: Generation Engine
│   │   ├── Dockerfile
│   │   ├── entrypoint.sh
│   │   ├── main.py              # Entry point / worker process wrapper
│   │   ├── celery_app.py        # Celery app — ai_worker.celery_app:app
│   │   ├── tasks.py             # Task name ai_worker.tasks.run_graph
│   │   ├── nodes/               # shared nodes — see containers/ai_worker/nodes.md
│   │   |   ├── validation.py
│   │   |   └── decider.py
│   │   └── graphs/              # Langgraph graphs
│   │       ├── environment/     # Environment creation graph
│   │       |   ├──graph.py
│   │       |   └── nodes/       # unique graph nodes
│   │       └── battle/          # Battle generation graph
│   │           ├──graph.py
│   │           └── nodes/       # unique graph nodes
│   │
│   └── web/                     # Container: Independent Dashboard — single container, single Dockerfile,
│       │                         # multi-stage build (Node build stage → Python runtime stage). Full design
│       │                         # rationale, page-by-page breakdown, and integration model now live in
│       │                         # docs/containers/web/ (web.md, components.md, pages/*.md) — not duplicated here.
│       ├── Dockerfile            # Stage 1: `npm run build` under frontend/ → dist/. Stage 2: Python image,
│       │                         # copies backend/ + the built dist/, runs FastAPI/Uvicorn.
│       ├── entrypoint.sh
│       ├── backend/
│       │   ├── main.py          # FastAPI app entry point — mounts API routers + serves frontend/dist/ as static files
|       |   ├── routes/          # dashboard.py, guilds.py, suggestions.py, webhook.py — see docs/containers/web/pages/*.md
|       |   ├── services/        # Thin per-domain logic via shared/storage repositories
|       |   │                    # (Azure adapters in production; local → dev-support in development)
|       |   ├── config/
|       |   ├── utils/
|       |   └── models/          # Pydantic request/response models
|       └── frontend/            # React + TypeScript + Vite (corrected from an earlier vanilla-TS draft of this
|           │                     # tree — see docs/containers/web/web.md §2 for why)
|           ├── package.json
|           ├── tsconfig.json
|           ├── vite.config.ts
|           ├── index.html              # Vite entry HTML
|           ├── dist/                   # Build output (gitignored) — copied into the backend image at build time
|           └── src/
|               ├── main.tsx            # App entry point, router setup
|               ├── App.tsx             # Root layout: nav shell + routed page outlet
|               ├── styles/             # Global styles
|               ├── types/              # TypeScript type definitions
|               │   ├── battle.ts       # BattleResult, Fighter, etc.
|               │   ├── suggestion.ts   # Suggestion, SuggestionResponse
|               │   ├── metrics.ts      # MetricsPayload, TelemetryPoint
|               │   └── guild.ts        # GuildConfig
|               ├── api/                # Backend API call wrappers
|               │   ├── client.ts       # Base fetch wrapper (base URL, error handling)
|               │   ├── suggestions.ts
|               │   ├── metrics.ts
|               │   ├── guilds.ts
|               │   └── webhook.ts
|               ├── pubsub/             # Azure Web PubSub — direct browser connection, not proxied through
|               │   │                    # the backend (see docs/containers/web/web.md §6)
|               │   ├── negotiate.ts    # Calls backend's /api/pubsub/negotiate for a client access token
|               │   ├── client.ts       # WebSocket connection setup using that token
|               │   └── handlers.ts     # Incoming message handlers (metrics, logs)
|               ├── pages/              # One React page component per dashboard page — see
|               │   │                    # docs/containers/web/pages/*.md (one doc per page, same pairing)
|               │   ├── HomePage.tsx
|               │   ├── DashboardPage.tsx
|               │   ├── GuildsPage.tsx
|               │   ├── SuggestionsPage.tsx
|               │   ├── PerformancePage.tsx
|               │   └── WebhookPage.tsx
|               ├── components/         # Reusable UI pieces — see docs/containers/web/components.md
|               │   ├── charts/         # Metric charts (e.g. CPU/RAM over time)
|               │   ├── tables/         # Sortable data tables
|               │   ├── modals/         # Confirmation and response modals
|               │   └── layout/         # Nav bar, connection-status indicator, etc.
|               └── hooks/              # Shared React hooks (e.g. usePolling, useWebPubSub)
├── prompts/                     # AI prompt templates (plain text) — TARGET structure below, rewrite in progress,
│   │                             # see containers/ai_worker/prompts.md for the full rationale, per-node mapping,
│   │                             # and the legacy → target file migration table (§6). On-disk layout as of this
│   │                             # revision still matches the OLD (legacy) structure, not what's shown here.
│   ├── nodes/                    # shared code nodes' base prompts — mirrors src/ai_worker/nodes/
│   │   ├── validator_base.txt
│   │   └── decider_base.txt
│   ├── graphs/                   # one prompt per LangGraph node — mirrors src/ai_worker/graphs/
│   │   ├── environment/
│   │   │   ├── generator.txt            # = legacy core/environment_combiner.txt, migrated
│   │   │   ├── normalise.txt
│   │   │   ├── enhancer.txt
│   │   │   ├── validator_criteria.txt
│   │   │   └── decider_criteria.txt
│   │   └── battle/
│   │       ├── predefine.txt
│   │       ├── create_skeleton.txt
│   │       ├── implement_first_episode.txt
│   │       ├── implement_next_episode.txt
│   │       ├── implement_last_episode.txt
│   │       ├── modifier.txt
│   │       ├── validator_criteria.txt
│   │       ├── decider_criteria.txt
│   │       └── resolve_winners.txt
│   ├── elements/                 # reusable injection wrappers
│   │   ├── setting/                     # NOTE: singular "setting", moved here from top-level prompts/setting/
│   │   │   ├── realistic.txt
│   │   │   ├── realistic-urban.txt
│   │   │   ├── realistic-nature.txt
│   │   │   ├── dreamcore.txt
│   │   │   ├── unpredictable-realistic.txt
│   │   │   ├── unpredictable-dreamcore.txt
│   │   │   └── unpredictable-funny.txt
│   │   ├── language.txt                 # standard injection wrapper for output locale
│   │   ├── environment.txt              # `## Environment:\n{env}` — renamed from custom_environment.txt
│   │   └── fighters.txt                 # header template
│   └── static/                   # no-AI, pre-written content — renamed from core/generic_environments/
│       └── generic_environments/
│           ├── generic_environment0.txt
│           └── generic_environment1.txt
│
└── tests/
    ├── unit/
    │   ├── test_shared/
    │   ├── test_bot/
    │   ├── test_head/
    │   └── test_ai_worker/
    └── integration/
        ├── test_battle_flow/    # Bot + RabbitMQ + AI Worker
        └── test_suggestion_flow/
```

### Key Structural Decisions

- **`src/shared/` is not a container.** It is an internal library copied into each service image at build time via the root-level Docker build context. No service imports from another service's directory.
- **Domain persistence ports sit above Azure.** Bot and Web depend on repository interfaces (`GuildRepository`, `SuggestionRepository`, `StatusRepository`, `MetricsRepository`, …) selected at composition root by `DCA_RUNTIME_MODE`. Production adapters wrap `src/shared/azure/services/*`. Development adapters call Compose-only `dev-support`. Canonical: `contracts/local_development.md` §5. An Azure-protocol emulator is rejected.
- **Single `pyproject.toml` at root.** All dependencies for all services are declared here. This simplifies local development — a single `pip install -e .` makes all code available with live reload.
- **Build context is always the repo root.** Every `Dockerfile` uses `.` as context, allowing access to both the service directory and `src/shared/`.
- **`docker-compose.dev.yml`** mounts `src/` as a volume into each container, so local code changes are reflected immediately without rebuilding images. It is also the explicit opt-in for product-development services (`dev-support`, Compose-included Web). It does **not** by itself authorize talking to production Azure or the production Discord application.
- **`Web` is excluded from production `docker-compose.yml`** by design. It is deployed independently and has no direct network access to local containers. **Exception:** product-development mode includes Web in the development overlay and routes data through `dev-support` (`contracts/local_development.md`).
- **`prompts/` lives at the root** and is mounted into `ai_worker` at runtime, making prompt iteration possible without rebuilding the image.
- **Product-development mode does not validate Azure coordination.** Passing local UI/command checks (S14) never substitutes for S01–S10 / production S12–S13.