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
| **Leader Mutual Exclusion**     | **Azure Blob Lease** (the actual split-brain-preventing mutex — cheap, low-frequency renewal) |
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
│  [Azure Web PubSub] <--- Leader Election & Live Metrics Stream  │
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

*Note: Multiple "LOCAL NODE" setups can run simultaneously on different physical PCs. Update note: The `Launcher` process is not pictured above as it lives outside the Docker Compose boundary, on the host OS itself. It communicates with `Head` via local IPC only — it has no network access to Azure or RabbitMQ.*

*Leader election, corrected: Azure Web PubSub group membership is **additive, not exclusive** — the service allows any number of clients to join the same group, so group presence alone cannot guarantee only one `Bot` is ever active (confirmed against Microsoft's own Web PubSub documentation, which states plainly that "a group can contain multiple clients"). The actual mutual-exclusion primitive is an **Azure Blob Lease** on a single fixed blob (`head.md` §3/§6). Web PubSub keeps its original job — a cheap, real-time, push-based broadcast that every `Head` (leader or follower) stays joined to for its entire lifetime — but it now carries a leader heartbeat message rather than being the source of truth for who holds leadership. Sequence: a follower that stops receiving the leader's heartbeat over PubSub attempts to acquire the Blob lease (one HTTP call, `409` if it loses the race); only the winner activates its local `Bot` and starts publishing the heartbeat. The leader renews the lease on the same cadence it already uses for the PubSub heartbeat (`HEAD_ELECTION_HEARTBEAT_SEC`), so this closes the split-brain window without adding request volume in steady state — Blob is only touched during an actual claim attempt, not on every check.*

---

  

## Container Breakdown (Docker Compose)

### `Head` — The Session Coordinator (Watchdog)
* **Role:** Handles Leader Election, node consensus, and hybrid telemetry gathering.
* **Behavior:** Joins a single, permanent **Azure Web PubSub** cluster broadcast group on startup — every `Head`, leader or follower, stays in this one group for its entire process lifetime (cheap, push-based). Listens for the current leader's heartbeat message in that group.
* **Active state:** If no leader heartbeat is observed within timeout, it attempts to acquire the **Azure Blob Lease** that is the actual mutual-exclusion mechanism (see High-Level Architecture note above). Only the node that wins the lease claims leadership, signals the local `Bot` container to "Wake up and connect" (via Mosquitto, §"Service coordination" below — not a raw HTTP/socket call), and begins publishing its own heartbeat into the broadcast group. It also begins caching system logs and hardware metrics in memory.
* **Telemetry routing:** Every **60 seconds**, it pushes a batch of stored metrics to **Azure Table Storage** and appends logs to **Azure Blob Storage**. If a Web Client connects, it additionally streams live batches of metrics/logs via **Azure Web PubSub** every **10 seconds**.
* **Passive state:** If a leader heartbeat is already present in the broadcast group, it enters standby mode. If the leader's heartbeat stops arriving (e.g., PC crashes), every follower observes the gap and races for the Blob lease; only the winner takes over and activates its local `Bot`.
* **Not critical crash:** If leader loses connection to internet (Azure), it should immediately stop its local `Bot`. And try to reconnect using exponential time delay for retries, up to 30 minutes. After that it is considered as new follower.
* **Log aggregation:** Subscribes to `logs/#` on `Mosquitto`. Parses incoming structured log lines, buffers them in memory, and flushes to **Azure Blob Storage** alongside the existing 60-second telemetry batch.
* **Service coordination:** Publishes control signals to `control/<service>/<action>` topics to activate, pause, or gracefully drain individual services, replacing the need for internal HTTP endpoints between containers. HTTP is reserved exclusively for the `Head` ↔ `Launcher` channel, which must remain independent of the Compose network's lifecycle.

### `Bot` — The Discord Interface
* **Role:** The only component that connects to the Discord Gateway.
* **Behavior:** Starts alongside other containers but **does not execute `bot.run()**` until authorized by the local `Head`.
* **Inbound data:** Listens to Discord slash commands. Periodically polls **Azure Queue Storage** for new web-submitted `Suggestions`.
* **Outbound data:** Instead of waiting for AI, it immediately pushes the battle generation prompt to the local **RabbitMQ** queue and tells the user "Battle is generating...". Reads the finished story from RabbitMQ and posts it to Discord.
* **New Suggestions**: If new suggestion is added via bot command, adds it to `Azure Cosmos DB`
* **On `Head` termination:** Waits up to `BOT_SHUTDOWN_GRACE_SEC` for all `ai_tasks_results` to be completed and tries to send back at least error response, then is destroyed — see `docs/containers/bot/discord_bot.md` §6.5 for the bounded value and the distinct signal that triggers this (`control/bot/stop`, not `drain`).
* **Activation signal:** Instead of a direct internal HTTP/socket call from `Head`, `Bot` subscribes to `control/bot/activate`, `control/bot/drain`, and `control/bot/stop` on `Mosquitto` and reacts accordingly — connecting to Discord on activation; ceasing new `/quick-battle` acceptance only (soft gate, other commands unaffected) on `drain`; disconnecting from Discord entirely on `stop`. **Three actions, not two** — corrected in this revision, see `docs/containers/bot/discord_bot.md` §6.5 for why `drain` and `stop` needed to be split.
* **Full container-level design** (env vars, guild config schema, task-progress plumbing, background services) now lives in `docs/containers/bot/discord_bot.md` — not duplicated here.

### `RabbitMQ` — Local Message Broker
* **Role:** Facilitates asynchronous communication strictly between the `Bot` and the `AI Worker(s)`.
* **Behavior:** Holds queues for `ai_tasks` and `ai_tasks_results`. Ensures no tasks are lost if an AI Worker crashes mid-generation.
* **Response:** Each message from `ai_tasks` should eventually get its response in `ai_tasks_results` using RabbitMQ's `reply_to` functionality
* **Local**: `RabbitMQ` is local and local only — there is no cross-node routing of any kind. A non-leader node's `AI Worker` is not processing work "for" the cluster leader; its local `ai_tasks` queue simply never receives anything, because only the active leader's local `Bot` ever publishes into its own local queue.
* **Soft stop (`drain`):** stops accepting *new* `/quick-battle` requests only; anything already in `ai_tasks`/in-flight is left to finish normally. Used for planned updates.
* **Hard stop (`stop`), formerly "`Head` light crash":** In case of internet failure, and `Bot` being terminated, all messages in `ai_tasks`  are purged, and any `AI Worker` execution already claimed and running is actively terminated (Celery `revoke(terminate=True)`, not left to finish) — a new synthetic error result message is added to `ai_tasks_results` for each purged/terminated task. `Bot` delivers that error back to each task's originating Discord thread before disconnecting from the Gateway — see `bot/discord_bot.md` §6.5 for the exact sequencing.

### `Mosquitto` — Internal Pub/Sub Broker

* **Role:** Facilitates lightweight, fire-and-forget communication between all local containers: structured log aggregation, internal coordination signals, and service-to-service control commands. Operates independently from `RabbitMQ`, which remains dedicated exclusively to the AI task request/response queue.
* **Behavior:** A standard Mosquitto container with no custom modifications, configured with local-only access (no external port exposure) and persistent session support disabled (logs/control are transient by nature).
* **Topic Structure:**

| Topic Pattern | Publishers | Subscribers | Purpose |
|---|---|---|---|
| `logs/info/<service>` | All services | `Head` | Informational log records |
| `logs/warning/<service>` | All services | `Head` | Warning-level log records |
| `logs/errors/<service>` | All services | `Head` | Error/critical log records |
| `control/bot/<action>` | `Head` | `Bot` | `activate` \| `drain` (soft — stop new `/quick-battle` acceptance only) \| `stop` (hard — full immediate Gateway disconnect). See `mosquitto.md` §4 and `docs/containers/bot/discord_bot.md` §6.5 for the corrected three-action split. |
| `control/ai_worker/<action>` | `Head` | `AI Worker` | Pause/resume task consumption |
| `status/<service>/heartbeat` | All services | `Head` | Liveness signal, used for internal health tracking |
| `progress/ai_worker/<task_id>` | `AI Worker` | `Bot` | Best-effort task-phase updates (`queued`/`launching`/`composing`/`refining`/`finishing`) for a running `ai_tasks` job, so `Bot` can render a live status bar in Discord. Deliberately **not** on `RabbitMQ` — a dropped tick is harmless (the next tick or the final `ai_tasks_results` message still lands), which is exactly the tolerance this broker is for. See `docs/contracts/task_progress.md` for the full contract. |

* **Log Message Format:** Every service publishes log records as a single structured string, regardless of topic level:  `[%time%][%level%][%service%][%file/module%]<any additional tags: trace_id, command, guild_id>: [%message%]`
* `Head` is the sole subscriber responsible for parsing this format, batching records, and forwarding them to **Azure Blob Storage** every 60 seconds (per the existing hybrid telemetry model). * **RabbitMQ Log Bridging:** A lightweight bridge process (hosted within `Head`) subscribes to RabbitMQ's `rabbitmq_event_exchange` plugin and re-publishes relevant broker-level events (consumer disconnects, queue overflows, channel errors) onto `logs/warning/rabbitmq` or `logs/errors/rabbitmq`, normalized to the standard log format above. This keeps `Head` as the single point of log aggregation without requiring direct AMQP event subscriptions from unrelated services. * **Isolation principle:** `Mosquitto` is strictly internal to the Docker Compose network. It is never exposed externally and is not used for AI task distribution, which remains the responsibility of `RabbitMQ`.
### `AI Worker` — The Generation Engine
* **Role:** Handles the blocking/heavy AI logic.
* **Behavior:** A **Celery** worker running in an infinite loop. It pulls a task from **RabbitMQ**, initializes a **Langgraph** state machine, interacts with the Gemini API to resolve the battle logic, and pushes the final JSON/text back to RabbitMQ.
* **Credentials are per-guild, not global:** `AI Worker` holds no Gemini credential of its own. Each `ai_tasks` message carries the requesting guild's own `api_key` and `model` (sourced from `contracts/guild_config.md`, staged via `/config`) — confirmed gap closed in this revision, see `ai_worker.md` §3/§4. **Accepted risk, not solved here:** the key travels plaintext on the message and is held plaintext in Cosmos DB (`contracts/guild_config.md` §7) — a per-user-supplied-API-key, cheap/self-hosted deployment model makes an Azure Key Vault indirection impractical for v1; this is a deliberate, documented trade-off to revisit if the project's cost model ever changes, not an oversight.
* **Scaling:** Can be scaled to multiple instances per PC, and to additional PCs (each running its own local `RabbitMQ` + `AI Worker` pair). Always running, even if the local `Head` is NOT the leader — but a non-leader node's `AI Worker` simply idles, since its local `ai_tasks` queue never receives anything (RabbitMQ is strictly node-local, no cross-node task routing — see the corrected `RabbitMQ` note above).
* **Coordination signal:** Subscribes to `control/ai_worker/pause` and `control/ai_worker/resume` on `Mosquitto`, allowing the **local** `Head` to halt task consumption on its own node during an update or maintenance window, without touching RabbitMQ queue state directly. This is node-local, not cluster-wide — each node's `Head` only ever controls its own local `AI Worker`.

### `Web` — The Independent Dashboard (Remote, not included at docker-compose but is container like)


* **Role:** FastAPI application serving the dashboard and user suggestions UI.
* **Behavior:** Completely decoupled from the Bot's local network. It has no direct connection to RabbitMQ or the Bot container.
* **Integration:** When an admin submits a new message to a suggestion, the `Web` container writes it to **Azure Cosmos DB** and sends a notification payload directly into **Azure Queue Storage**. For live dashboard stats, it listens to Web PubSub.
* **Authentication — explicitly deferred, not assumed either way:** `Web` has no application-layer authentication/authorization today (`web.md` §3/§9). This is an acknowledged open item, not a silent gap. Network-level exposure (public internet, LAN-only, VPN-only, etc.) is a **deployment-time decision left to whoever operates the cluster** — this doc deliberately does not hardcode an assumption that `Web` either is or isn't publicly reachable. Treat any admin action documented under `pages/suggestions.md`/`pages/webhook.md` as unauthenticated until this is revisited.
### `Launcher` — The Update Orchestrator (Host Process, NOT in Docker Compose)

* **Role:** Lives on the host machine as a system service (systemd/Windows Service). Survives container restarts and is the only component capable of pulling new images and recreating containers.
* **Behavior:** Listens for an update signal from the local `Head` over a local IPC channel (Unix socket / named pipe / loopback HTTP). On signal, it:
    1. Waits for confirmation that `Head` has relinquished leadership and `Bot` has gracefully drained active tasks.
    2. Runs `docker compose pull` for the target version tag.
    3. Runs `docker compose up -d --force-recreate`.
    4. Monitors the new `Head` container's health check for a configurable verification window.
    5. If verification fails, rolls back by re-pulling and recreating with the previous version tag.
* **Manual mode:** Exposes a CLI (`launcher update --version vX.Y.Z`, `launcher rollback`, `launcher status`) for direct administrator control, bypassing the automatic detection flow.
* **Isolation principle:** The Launcher is intentionally excluded from `docker-compose.yml`. It must never be restarted as a side effect of the update process it itself triggers.


*Note: > **Design boundary:** `RabbitMQ` and `Mosquitto` serve fundamentally different communication patterns and must not be merged or substituted for one another:
> - `RabbitMQ` — exactly-once task delegation requiring acknowledgment, retry, and single-consumer guarantees (AI generation tasks).
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
| Battle Results               | `Azure Blob Storage`                | `.txt` files with JSON metadata | On-demand                                       |
| Performance Metrics          | `Azure Table Storage`               | Structured NoSQL rows           | Batched (1 minute)                              |
| Active Logs                  | `Azure Blob Storage`                | Append Blob (Plain text)        | Batched (1 minute)                              |
| Live Telemetry Stream        | `Azure Web PubSub`                  | Real-time WebSocket payloads    | Batched (10 seconds) *Only if active listeners* |
| Local AI Task Queue          | `RabbitMQ` (Docker Vol.)            | AMQP format                     | On-demand                                       |
| Internal Service Logs        | `Mosquitto` (MQTT, in-transit only) | Plain structured text           | Real-time, batched to Blob every 60s by `Head`  |
| Service Coordination Signals | `Mosquitto` (MQTT, in-transit only) | Plain text / JSON payload       | On-demand (activation, shutdown, pause/resume)  |

---

  

## How the Parts Connect (Call Flow Summary)


### Scenario 1: System Boot & Failover
1. PC turns on → `docker-compose up` starts `Head`, `Bot`, `RabbitMQ`, `Mosquitto`, `AI Worker`.
2. `Bot` waits. `AI Worker` connects to `RabbitMQ` and waits.
3. `Head` connects to `Azure Web PubSub` and joins the permanent cluster broadcast group.
4. If no leader heartbeat is observed in that group within timeout → `Head` attempts to acquire the Blob Lease. If it wins → becomes Leader → publishes its heartbeat into the broadcast group → signals `Bot` to activate (`control/bot/activate` on Mosquitto).
5. `Bot` connects to Discord WebSocket.
6. (Failover case) If the leader's heartbeat stops arriving, every follower races for the Blob Lease; only the winner proceeds through step 4/5 — losers observe the lease is held and fall back to standby.

### Scenario 2: Generating a Battle
1. User types `/quick-battle` in Discord.
2. `Bot` formats the request and pushes to `RabbitMQ` (`ai_tasks` queue).
3. `Bot` instantly returns a "Please wait" message to Discord (no blocking).
4. `AI Worker` picks up the task, processes it via `Langgraph` & Gemini API.
5. `AI Worker` pushes the result to `RabbitMQ` (`ai_tasks_results` queue).
6. `Bot` consumes the result, uploads the log to `Azure Blob Storage`, and sends the final message to the Discord channel.

### Scenario 3: Suggestion Submission
1. User submits a form using command `/suggest`.
2. Bot sends json with all the information to `Azure Cosmos DB`
3. `Web` loads suggestion from `Azure Cosmos DB` when opened.
4. Admin writes response to suggestion.
5. `Web` saves the full suggestion to `Azure Cosmos DB`.
6. `Web` pushes a lightweight notification event into `Azure Queue Storage`.
7. The active `Bot` (polling the queue) receives the event and sends a **DM to the original suggester** (via the `contact.user_id` field already recorded on the suggestion, `docs/containers/bot/commands/suggest.md` §9). **Corrected in this revision** — this line previously said "sends an Embed to the Discord Admin Channel," which conflicted with `web/pages/suggestions.md` and the suggestion payload's own `contact.method: "dm"` field; see `docs/containers/bot/discord_bot.md` §6.6 for the confirmed resolution.

### Scenario 4: Telemetry & Log Monitoring (Hybrid Cost-Saving Mode)
1. The active `Head` continually samples CPU/RAM usage and stores logs temporarily in local memory.
2. Every **60 seconds**, `Head` performs a single bulk write of the accumulated metrics to **Azure Table Storage** and appends the logs to **Azure Blob Storage** for cold archiving.
3. A user opens the Web Dashboard. The browser fetches the last 24h of history via a static API call.
4. The Web Dashboard connects to **Azure Web PubSub**.
5. The `Head` detects an active listener and begins intercepting the telemetry, pushing a live payload through Web PubSub every **10 seconds**, but keep sending data to **Azure Blob Storage** and **Azure Table Storage** every 60 seconds.
6. User closes the dashboard. `Head` detects the disconnect, stops the 10-second stream, and falls back exclusively to the 60-second database batching.
### Scenario 5: Automatic Update Flow

**Corrected in this revision:** the previous version of this scenario had only the leader's own `Launcher` ever receiving the update signal, with no mechanism for follower nodes to learn about it at all — they would stay on the old version indefinitely and could later win leader election while incompatible. The fix: every `Head` (leader and follower) is already permanently joined to the same cluster broadcast group used for the leader heartbeat (see High-Level Architecture note), so the `update_available` broadcast reaches everyone over that same cheap channel, and **each node's `Head` acts on it independently**, not just the leader's.

1. The active leader `Head` periodically polls the GitHub Releases API for a newer published version.
2. On detecting a newer version, the leader `Head` publishes an `update_available` event (`{"version": "vX.Y.Z"}`) into the cluster broadcast group — every `Head` in the cluster, leader or follower, receives it immediately (they're all already members, per the corrected election design).
3. **Leader's own path:** the current leader `Head` begins a graceful drain — signals `Bot` to stop accepting new `/quick-battle` requests (`control/bot/drain`) and waits for in-flight `ai_tasks` to resolve (or timeout). Once drained, it relinquishes the Blob Lease and signals its local `Launcher` via local IPC: "update to vX.Y.Z".
4. **Follower path (new in this revision):** a follower `Head` has no active `Bot` to drain, so on receiving the broadcast it signals its own local `Launcher` directly, without any drain step.
5. Every node's `Launcher` (leader and followers alike) independently pulls the new image tags from GHCR and recreates its local containers.
6. New `Head` instances re-enter leader election (rejoin the broadcast group, listen for a heartbeat, race for the Blob Lease if none is heard) as usual.
7. Each node's own `Launcher` monitors its own `Head` container's health check for a verification window (e.g. 5 minutes).
8. If the health check fails repeatedly within the window, that node's `Launcher` automatically rolls back to the previous image tag and recreates containers again — independently per node.
9. An administrator may bypass steps 1–2 entirely via `launcher update --version vX.Y.Z` for manual control, on any node individually.

---

## Project File Structure

```
discord-combat-ai/
│
├── docker-compose.yml           # Orchestrates all local containers (Head, Bot, RabbitMQ, Mosquitto, AI Worker)
├── docker-compose.dev.yml       # Dev overrides (volume mounts, exposed ports, hot reload)
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
│   │   ├── ipc/
│   │   │   └── listener.go       # Local socket/pipe listener for Head signals
│   │   ├── docker/
│   │   │   ├── pull.go           # Docker Engine API: image pull
│   │   │   └── recreate.go       # Docker Engine API: container recreate
│   │   ├── healthcheck/
│   │   │   └── verify.go         # Polls Head's health endpoint post-update
│   │   └── state/
│   │       └── version_history.go # Tracks current + previous version tags for rollback
│   ├── install/
│   │   ├── launcher.service       # systemd unit file (Linux)
│   │   └── launcher.exe.config    # Windows Service wrapper config
│   └── Dockerfile.build     # Multi-stage build to compile binary (not for runtime)
|
├── src/
│   │
│   ├── shared/                  # Internal library, imported by all services
│   │   ├── azure/
│   │   │   ├── services/        # Shared Azure implementations
│   │   │   │   ├── suggestions.py #  + Azure Queue Storage client wrappers
│   │   │   │   ├── guilds.py    # Guild configs, and battle logs
│   │   │   │   ├── status.py    # Bot status, version, invite link, etc. `bot.json`
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
│   │   ├── models/              # Pydantic models shared across services
│   │   └── utils/               # Generic helpers (logging, retry logic, etc.)
│   │
│   ├── head/                    # Container: Session Coordinator / Watchdog
│   │   ├── Dockerfile
│   │   ├── entrypoint.sh
│   │   ├── main.py              # Entry point: leader election loop
│   │   └── modules/
│   │       ├── election.py      # Azure Web PubSub leader election logic
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
│   │   ├── main.py              # Entry point: starts Celery worker
│   │   ├── tasks.py             # Celery task definitions
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
|       |   ├── services/        # Thin per-domain logic, delegates to src/shared/azure/services/
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
- **Single `pyproject.toml` at root.** All dependencies for all services are declared here. This simplifies local development — a single `pip install -e .` makes all code available with live reload.
- **Build context is always the repo root.** Every `Dockerfile` uses `.` as context, allowing access to both the service directory and `src/shared/`.
- **`docker-compose.dev.yml`** mounts `src/` as a volume into each container, so local code changes are reflected immediately without rebuilding images.
- **`Web` is excluded from `docker-compose.yml`** by design. It is deployed independently and has no direct network access to local containers.
- **`prompts/` lives at the root** and is mounted into `ai_worker` at runtime, making prompt iteration possible without rebuilding the image.