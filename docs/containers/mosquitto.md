# Service: Mosquitto

## 1. Responsibility

`Mosquitto` is the internal, best-effort MQTT pub/sub broker facilitating lightweight, fire-and-forget communication between all local containers: structured log aggregation, internal coordination signals, and service-to-service control commands (per `architecture.md`'s Container Breakdown).

It operates independently from `RabbitMQ`, which remains dedicated exclusively to the AI task request/response queue. Per the explicit design boundary already stated in `architecture.md`: `Mosquitto` is for *best-effort broadcast* where occasional message loss is acceptable and multiple subscribers may exist — the inverse of RabbitMQ's exactly-once, single-consumer guarantees. See `rabbitmq.md` §1 for the same boundary from the other side.

---

## 2. File Structure

Like `RabbitMQ`, `Mosquitto` runs as a **standard, unmodified container** — per `architecture.md`: *"A standard Mosquitto container with no custom modifications, configured with local-only access (no external port exposure) and persistent session support disabled."* There is no project-owned application code for this container.

Its only project-specific configuration is whatever config file enforces those two properties (no external listener, no persistent sessions).

> **Open item:** no `mosquitto/` directory or config file currently appears anywhere in `architecture.md`'s documented project file structure. A mounted `mosquitto.conf` (or equivalent Compose-level config) will be needed to actually enforce "no external port exposure" and "persistent session support disabled" — flagged as a gap to add to the project file structure when the Compose setup is written, not decided in this doc.

---

## 3. Environment Variables

**Broker-side:** none. Per Section 1/2, this is a stock image with no custom modifications — configuration (if any beyond the mounted conf file) would be Compose-level, not environment-variable-driven.

**Consumer-side (per-service connection variables):** the only ones formally defined anywhere today belong to `Head`:

| Variable | Required | Default | Description | Source |
|---|---|---|---|---|
| `HEAD_MOSQUITTO_HOST` | No | `mosquitto` | Hostname of the local Mosquitto broker. | `head.md` §3 |
| `HEAD_MOSQUITTO_PORT` | No | `1883` | Mosquitto broker port. | `head.md` §3 |

| Variable | Required | Default | Description | Source |
|---|---|---|---|---|
| `AI_WORKER_MOSQUITTO_HOST` | No | `mosquitto` | Hostname of the local Mosquitto broker. | `ai_worker.md` §3 |
| `AI_WORKER_MOSQUITTO_PORT` | No | `1883` | Mosquitto broker port. | `ai_worker.md` §3 |

| Variable | Required | Default | Description | Source |
|---|---|---|---|---|
| `BOT_MOSQUITTO_HOST` | No | `mosquitto` | Hostname of the local Mosquitto broker. | `bot/discord_bot.md` §3 |
| `BOT_MOSQUITTO_PORT` | No | `1883` | Mosquitto broker port. | `bot/discord_bot.md` §3 |

> **Resolved in this revision:** the row previously here flagged `BOT_MOSQUITTO_HOST`/`BOT_MOSQUITTO_PORT` as open pending `bot/discord_bot.md`. That doc is now written and formalizes both, following the same `<SERVICE>_MOSQUITTO_HOST/PORT` pattern `Head` and `AI Worker` already use.

---

## 4. Inbound Communication (Publish Side)

Reproduced from `architecture.md`'s Mosquitto Topic Structure table — this is the canonical, already-decided topic map:

| Topic Pattern | Publishers | Purpose |
|---|---|---|
| `logs/info/<service>` | All services | Informational log records |
| `logs/warning/<service>` | All services | Warning-level log records |
| `logs/errors/<service>` | All services | Error/critical log records |
| `control/bot/<action>` | `Head` | `activate` \| `drain` (soft — stop *new* `/quick-battle` acceptance, stay connected) \| `stop` (hard — purges `ai_tasks`, terminates any in-flight `AI Worker` execution, notifies affected Discord threads, **then** disconnects the Gateway — exact sequence confirmed in `bot/discord_bot.md` §6.5 this revision). `drain` and `stop` were split from one overloaded action in an earlier revision — `bot/discord_bot.md` §6.5 identified that `drain` was previously being asked to mean two different things (the soft per-command gate `architecture.md`'s Bot section describes, and the "immediately stop `Bot`" case `head.md` §9 describes for loss-of-internet). |
| `control/ai_worker/<action>` | `Head` | Pause/resume task consumption — **node-local, not cluster-wide** (corrected, `architecture.md`'s `AI Worker` note): each node's `Head` only ever controls its own local `AI Worker`. |
| `status/<service>/heartbeat` | All services | Liveness signal, used for internal health tracking. **`Bot`'s payload is a confirmed exception, not a bare ping** — `{latency_ms, guild_count}` (`bot/discord_bot.md` §6.3), superset of the bare-ping shape every other service still uses. |
| `progress/ai_worker/<task_id>` | `AI Worker` | Best-effort task-phase updates for a running `ai_tasks` job (`queued`/`launching`/`composing`/`refining`/`finishing`) — powers a live Discord status bar. See `docs/contracts/task_progress.md` for the full message contract. |

**Log message format** (applies to all `logs/*` topics, per `architecture.md`, restated verbatim since every subscriber depends on this exact shape):

```
[%time%][%level%][%service%][%file/module%]<any additional tags: trace_id, command, guild_id>: [%message%]
```

---

## 5. Outbound Communication (Subscribe / Delivery Side)

Same topic map as Section 4, viewed from the delivery side:

| Topic Pattern | Subscribers | Trigger |
|---|---|---|
| `logs/#` (wildcard, all levels/services) | `Head` (sole aggregator, per `head.md` §7) | Real-time, on every log emission |
| `control/bot/<action>` | `Bot` | On `Head` publishing `activate`/`drain`/`stop` (§4's corrected three-action model) |
| `control/ai_worker/<action>` | `AI Worker` | On `Head` publishing a pause/resume signal |
| `status/<service>/heartbeat` | `Head` | Periodic, per originating service's own heartbeat interval |
| `progress/ai_worker/#` (wildcard) | `Bot` | On every phase change of a task `Bot` itself submitted — `Bot` holds one static wildcard subscription (same pattern `Head` uses for `logs/#`) and filters by `task_id` against the task map it already keeps locally, rather than subscribing/unsubscribing per task. |

**Isolation principle (already decided, restated):** `Mosquitto` is strictly internal to the Docker Compose network. It is never exposed externally, and it is never used for AI task distribution — that remains `RabbitMQ`'s exclusive responsibility (see `rabbitmq.md` §1).

---

## 6. Internal Logic

- **Wildcard subscriptions:** `Head` subscribes to `logs/#` to catch every level/service in one subscription rather than six individual ones (per `head.md` §7).
- **No persistent sessions:** per `architecture.md`, session persistence is disabled — logs and control signals are transient by nature; a subscriber that's offline when a message is published simply never receives it. This is consistent with the "best-effort, occasional loss acceptable" design boundary.
- **QoS level:** not specified anywhere in current docs. Flagged as an open item — MQTT QoS (0/1/2) directly affects whether "occasional message loss is acceptable" (Section 1's stated tolerance) is actually QoS 0 by design, or whether it's merely an accepted side effect of not having configured a higher QoS. Worth an explicit decision rather than an implicit default.
- **Retained messages:** not specified. An open design question worth raising (not decided here): should `status/<service>/heartbeat` use MQTT retained messages, so `Head` can immediately read each service's last-known state on its own reconnect, instead of waiting for the next heartbeat interval? Currently unaddressed.
- **`progress/ai_worker/<task_id>` is the one topic pattern in this table that is not routed through or aggregated by `Head`** — it goes directly `AI Worker` → `Bot`, bypassing `Head` entirely, since `Head` has no reason to know about individual task phases. This is a deliberate exception to `Head`'s usual "sole aggregator" role (Section 5), not an oversight — see `docs/contracts/task_progress.md` for the full rationale.

---

## 7. Logging

This container **is** the transport layer for the entire system's logging pipeline (Section 4/5), but it's worth flagging explicitly: **does Mosquitto log about itself?**

- `RabbitMQ`'s own broker-level events are bridged into `logs/warning|errors/rabbitmq` by a subscriber hosted in `Head` (see `rabbitmq.md` §7).
- **No equivalent bridging is documented for Mosquitto's own broker log** (client connect/disconnect events, authentication failures, dropped-message warnings). `Head`'s log aggregator (per `head.md` §7) only describes subscribing to `logs/#` and bridging RabbitMQ's event exchange — nothing about Mosquitto's own internal log output.
- **Flagged as a real design gap**, not just missing detail: if the Mosquitto broker itself is misbehaving (e.g. rejecting connections, silently dropping messages under load), there is currently no path for that fact to reach the same centralized log archive everything else uses. An administrator would only find out by reading the raw Mosquitto container's `docker logs` directly.

---

## 8. Metrics

No dedicated metrics are defined for Mosquitto itself (e.g. connected client count, message throughput, dropped-message count). Flagged as an open item — could be a natural addition to `Head`'s existing election-related metrics (per `head.md` §8), since `Head` already owns both the telemetry pipeline and the Mosquitto connection, but this has not been decided or assigned anywhere.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `Mosquitto` unreachable | Publish/subscribe failures | Per `head.md` §9 (restated here for completeness): log aggregation and control signaling halt locally. `Head` continues its own election logic (independent of Mosquitto) but cannot signal `Bot`/`AI Worker` or collect logs until Mosquitto recovers. Logged as `ERROR` only once Mosquitto itself reconnects (the outage itself can't be logged in real time without the broker). |
| `Bot`/`AI Worker` cannot receive `control/*` signals while Mosquitto is down | No documented detection — this is the flagged gap below | **Not specified.** Both `Bot` and `AI Worker` rely entirely on `control/*` topics for activation, drain, pause, and resume (per `architecture.md`). If Mosquitto is unreachable exactly when `Head` needs to send one of these signals, there is no documented fallback: does `Bot` default to inactive and simply never activate? Does it hang indefinitely waiting for `control/bot/activate`? This is a real, currently-unanswered failure mode worth resolving before implementation, not just a documentation nicety. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| None (self-contained) | — | Mosquitto depends on nothing else in the stack. |

Consumed by: `Head` (log aggregation, RabbitMQ event bridging output), `Bot` (control signal reception, task progress reception), `AI Worker` (control signal reception).

---

## 11. Health Check

Not specified as a standalone check on the Mosquitto container itself (no documented Docker Compose `healthcheck:` block). The only existing signal of Mosquitto's health today is indirect: `Head`'s own `/status` endpoint already exposes `"mosquitto_connected": true` (per `head.md` §11), reflecting whether `Head`'s own client connection to the broker is currently up — not a check of the broker process itself.

---

## 12. Versioning & Update Behavior

`Mosquitto` is an off-the-shelf broker image, pinned via its tag in `docker-compose.yml` — like `RabbitMQ`, it is **not** part of the coordinated version tag shared by `bot`/`head`/`ai_worker`/`web` (per `Launcher.md` §12). Its own image tag/upgrade policy is a separate, infrastructure-level decision not yet made anywhere.
