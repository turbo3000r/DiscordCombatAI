# Service: Mosquitto

## 1. Responsibility

`Mosquitto` is the internal, best-effort MQTT pub/sub broker facilitating lightweight, fire-and-forget communication between all local containers: structured log aggregation, internal coordination signals, and service-to-service control commands (per `architecture.md`'s Container Breakdown).

It operates independently from `RabbitMQ`, which remains dedicated exclusively to the AI task request/response queue. `Mosquitto` is best-effort for logs, heartbeats, and progress, but the leadership-control topics use QoS 1, retained safe mode, short-lived non-retained grants, acknowledgements, and Bot-local deadlines. The complete safety contract is `contracts/leadership_control.md`; this broker does not itself establish leadership.

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
| `control/bot/desired_state` | `Head` | QoS 1, retained safe mode: `inactive \| draining \| stopped`. It can never contain `active`; exact schema and per-term sequence semantics are in `contracts/leadership_control.md` §3.1. |
| `control/bot/activation_grant` | `Head` | QoS 1, **not retained**. Short-lived `active` or `draining` grant derived from the current Blob Lease term. This is the only message that can authorize a Gateway connection; see `contracts/leadership_control.md` §3.2. |
| `status/bot/control_ack` | `Bot` | QoS 1, not retained. Best-effort acknowledgement of applied control state, correlated by leadership term and command sequence (`contracts/leadership_control.md` §3.3). |
| `control/ai_worker/desired_state` | `Head` | **Same redesign, same reasoning.** Payload `{"state": "running" \| "paused"}`, retained. Node-local, not cluster-wide (corrected, `architecture.md`'s `AI Worker` note): each node's `Head` only ever controls its own local `AI Worker`. |
| `status/ai_worker/pause_ack` | `AI Worker` | **New this revision (P0.3).** QoS 1, not retained. `{"schema_version": 1, "node_id": ..., "paused_at": ISO8601}`, published once `AI Worker` finishes its current claim and goes idle after a `paused` request. Informational/diagnostic only — does not gate `Head`'s drain-complete decision (`contracts/drain_status.md` §3). |
| `status/bot/drain_progress` | `Bot` | **New this revision (P0.3).** QoS 1, not retained. `{"schema_version": 1, "node_id": ..., "leadership_term": ..., "in_flight_workflows": N, "observed_at": ISO8601}`, published every `BOT_DRAIN_PROGRESS_INTERVAL_SEC` while `bot.draining` is set. This is the authoritative signal `Head` watches during `DRAINING` (`contracts/drain_status.md` §1), replacing inference from empty RabbitMQ queues. |
| `status/<service>/heartbeat` | All services | Liveness signal. **`Bot`'s payload is `{latency_ms, guild_count}`** (`bot/discord_bot.md` §6.3); leader `Head` samples those fields into Table telemetry rows (`contracts/telemetry.md` §1). Other services may use a bare ping. |
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
| `logs/#` (wildcard, all levels/services) | `Head` (sole aggregator, per `head.md` §7) | Real-time, on every log emission — **not retained**, logs are a stream, not a state (§6 contrasts this with the control topics below) |
| `control/bot/desired_state` | `Bot` | On publish and every subscribe; retained delivery restores only a safe mode (`inactive`, `draining`, or `stopped`), never active authorization. |
| `control/bot/activation_grant` | `Bot` | Live delivery only. An offline/restarting Bot cannot receive a stale grant. |
| `status/bot/control_ack` | `Head` | After Bot applies a control transition; used for bounded best-effort demotion/update waiting. |
| `control/ai_worker/desired_state` | `AI Worker` | Same reconnect-safe behavior as above |
| `status/ai_worker/pause_ack` | `Head` (diagnostic only) | Once, after `AI Worker` finishes its current claim and goes idle post-pause — never blocks `Head`'s drain-complete decision |
| `status/bot/drain_progress` | `Head` | Every `BOT_DRAIN_PROGRESS_INTERVAL_SEC` while `Bot` is draining — `Head`'s authoritative drain-completion signal (`contracts/drain_status.md` §1) |
| `status/<service>/heartbeat` | `Head` | Periodic, per originating service's own heartbeat interval |
| `progress/ai_worker/#` (wildcard) | `Bot` | On every phase change of a task `Bot` itself submitted — `Bot` holds one static wildcard subscription (same pattern `Head` uses for `logs/#`) and filters by `task_id` against the task map it already keeps locally, rather than subscribing/unsubscribing per task. |

**Isolation principle (already decided, restated):** `Mosquitto` is strictly internal to the Docker Compose network. It is never exposed externally, and it is never used for AI task distribution — that remains `RabbitMQ`'s exclusive responsibility (see `rabbitmq.md` §1).

---

## 6. Internal Logic

- **Wildcard subscriptions:** `Head` subscribes to `logs/#` to catch every level/service in one subscription rather than six individual ones (per `head.md` §7).
- **No persistent sessions:** per `architecture.md`, session persistence is disabled — logs (and, before this revision, control signals) are transient by nature; a subscriber that's offline when a message is published simply never receives it, and must resubscribe fresh on every reconnect (no queued backlog). This is still consistent with the "best-effort, occasional loss acceptable" design boundary for `logs/*` and `status/*/heartbeat`. **Retained messages (below) are a separate broker feature from persistent sessions and are unaffected by this setting** — a retained message is held by the broker per-topic regardless of session persistence, and is delivered to a client immediately upon subscribing (fresh connection or reconnect alike). This is exactly why retain, not persistent sessions, is the right fix for `control/bot/desired_state`/`control/ai_worker/desired_state` below — it doesn't require reversing the "no persistent sessions" decision.
- **QoS:** leadership-control topics in `contracts/leadership_control.md` use QoS 1. Duplicate delivery is expected and suppressed by `(leadership_term, command_seq)`/`grant_id`. Logs, ordinary service heartbeats, and progress remain best-effort; their exact QoS remains a P1 broker-configuration decision.
- **Retention:** `control/bot/desired_state` is retained but contains safe modes only. `control/bot/activation_grant` and `status/bot/control_ack` are never retained. `control/ai_worker/desired_state` remains retained. Retained heartbeat policy remains open and can never turn a heartbeat into proof of current liveness.
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
| `Mosquitto` unreachable | Bot control connection drops | `Bot` soft-stops immediately: bounded drain, reject new AI work, then hard-stop at drain timeout. The activation-grant deadline is an independent hard-stop backstop. `Head` must not issue activation while control is unavailable. |
| `Bot` misses retained desired mode while disconnected | Re-subscribe | The safe retained mode is delivered on subscribe. It cannot reactivate Bot; a new live grant is still required. |
| Activation grant is lost or duplicated | Grant deadline / duplicate identifiers | Loss prevents activation or causes deadline hard-stop. Duplicate/lower/equal per-term sequence is ignored idempotently. |

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
