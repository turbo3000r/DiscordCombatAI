# Service: Mosquitto

## 1. Responsibility

`Mosquitto` is the internal, best-effort MQTT pub/sub broker facilitating lightweight, fire-and-forget communication between all local containers: structured log aggregation, internal coordination signals, and service-to-service control commands (per `architecture.md`'s Container Breakdown).

It operates independently from `RabbitMQ`, which remains dedicated exclusively to the AI task request/response queue. `Mosquitto` is best-effort for logs, heartbeats, and progress, but the leadership-control topics use QoS 1, retained safe mode, short-lived non-retained grants, acknowledgements, and Bot-local deadlines. The complete safety contract is `contracts/leadership_control.md`; this broker does not itself establish leadership.

---

## 2. File Structure

Like `RabbitMQ`, `Mosquitto` runs as a **standard, unmodified container** — per `architecture.md`: *"A standard Mosquitto container with no custom modifications, configured with local-only access (no external port exposure) and persistent session support disabled."* There is no project-owned application code for this container.

Project-owned config lives under the **target** path `infra/mosquitto/mosquitto.conf` (may not exist on disk until Phase 0 scaffolding):

```
infra/mosquitto/
└── mosquitto.conf
```

### 2a. `mosquitto.conf` contract (resolved, P1.5)

Minimum required settings:

```
listener 1883
allow_anonymous true
persistence false
# No listener on 0.0.0.0 outside Compose — Compose does not publish host ports for 1883 in production.
# allow_anonymous is acceptable only because the broker is Compose-network-internal (same threat model as RabbitMQ plaintext).
```

**Image pin:** `eclipse-mosquitto:2.0.20` (exact patch tag pinned in Compose when scaffolding). Compose mounts `infra/mosquitto/mosquitto.conf` to `/mosquitto/config/mosquitto.conf`.

**Auth:** no username/password in v1 — network isolation is the boundary. Do not expose MQTT on the host in production compose.

---

## 3. Environment Variables

**Broker-side:** none. Configuration is file-mounted (§2a), not env-driven.

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
| `control/ai_worker/desired_state` | `Head` | **Same redesign, same reasoning.** Payload `{"schema_version": 1, "state": "running" \| "paused"}`, retained, QoS 1. Node-local, not cluster-wide (corrected, `architecture.md`'s `AI Worker` note): each node's `Head` only ever controls its own local `AI Worker`. Receivers reject unknown `schema_version`. |
| `status/ai_worker/pause_ack` | `AI Worker` | **New this revision (P0.3).** QoS 1, not retained. `{"schema_version": 1, "node_id": ..., "paused_at": ISO8601}`, published once `AI Worker` finishes its current claim and goes idle after a `paused` request. Informational/diagnostic only — does not gate `Head`'s drain-complete decision (`contracts/drain_status.md` §3). |
| `status/bot/drain_progress` | `Bot` | **New this revision (P0.3).** QoS 1, not retained. `{"schema_version": 1, "node_id": ..., "leadership_term": ..., "in_flight_workflows": N, "observed_at": ISO8601}`, published every `BOT_DRAIN_PROGRESS_INTERVAL_SEC` while `bot.draining` is set. This is the authoritative signal `Head` watches during `DRAINING` (`contracts/drain_status.md` §1), replacing inference from empty RabbitMQ queues. |
| `status/<service>/heartbeat` | All services | Liveness signal. **`Bot`'s payload is `{latency_ms, guild_count}`** (`bot/discord_bot.md` §6.3); leader `Head` samples those fields into Table telemetry rows (`contracts/telemetry.md` §1). Other services may use a bare ping. |
| `progress/ai_worker/<task_id>` | `AI Worker` | Best-effort task-phase updates for a running `ai_tasks` job (`launching`/`composing`/`refining`/`finishing`; Bot creates `queued` locally) — powers a live Discord status bar. QoS 0, not retained. See `docs/contracts/task_progress.md` for the full message contract. |

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
- **No persistent sessions:** per `architecture.md`, session persistence is disabled — logs are transient by nature; a subscriber that's offline when a message is published simply never receives it, and must resubscribe fresh on every reconnect (no queued backlog). This is still consistent with the "best-effort, occasional loss acceptable" design boundary for `logs/*`, `progress/*`, and `status/*/heartbeat`. **Retained messages (below) are a separate broker feature from persistent sessions and are unaffected by this setting.**
- **QoS (resolved, P1.5):**
  | Topic class | QoS | Notes |
  |---|---|---|
  | Leadership control (`control/bot/*`, `status/bot/control_ack`) | **1** | Fixed in `contracts/leadership_control.md` |
  | `control/ai_worker/desired_state`, `status/ai_worker/pause_ack`, `status/bot/drain_progress` | **1** | Control / drain correctness |
  | `logs/*`, `progress/ai_worker/*`, `status/<service>/heartbeat` | **0** | Best-effort; loss is acceptable |
- **Retention (resolved, P1.5):** `control/bot/desired_state` and `control/ai_worker/desired_state` remain retained. `control/bot/activation_grant`, `status/bot/control_ack`, drain/pause acks, **and all heartbeats** are **never retained**. A heartbeat is never proof of current liveness — subscribers use local monotonic elapsed time since last receipt (same principle as grant deadlines in `leadership_control.md`).
- **Head republish after Mosquitto outage (resolved, P1.5):** if Head could not publish a state change while the broker was down, on successful reconnect Head **re-publishes the current retained desired modes** (`control/bot/desired_state`, `control/ai_worker/desired_state`) from its in-memory authority. It does **not** replay buffered historical grants. Short-lived `activation_grant` messages resume on the next normal grant cycle only — never flush a backlog of stale grants.
- **`progress/ai_worker/<task_id>` bypasses Head** — direct `AI Worker` → `Bot`; see `docs/contracts/task_progress.md`.

---

## 7. Logging

This container **is** the transport layer for the entire system's logging pipeline (Section 4/5). Mosquitto's **own** broker log (connect/disconnect, auth, drops) is **not** bridged into `logs/#` in v1.

**Resolved (P1.5):** operators diagnose Mosquitto via host `docker logs <mosquitto-container>` only. Unlike RabbitMQ (§7 / `rabbitmq.md` §7), there is no Head event-exchange bridge for Mosquitto. This is an accepted observability gap for the safety-critical control broker — mitigated by Head/Bot connection flags (`mosquitto_connected`) and leadership soft-stop on control loss, not by centralized broker-log archival.

---

## 8. Metrics

**Mosquitto broker metrics are not part of v1 telemetry.** Optional later; do not leave unrouteable counters in service docs.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `Mosquitto` unreachable | Bot control connection drops | `Bot` soft-stops immediately: bounded drain, reject new AI work, then hard-stop at drain timeout. The activation-grant deadline is an independent hard-stop backstop. `Head` must not issue activation while control is unavailable. |
| `Bot` misses retained desired mode while disconnected | Re-subscribe | The safe retained mode is delivered on subscribe. It cannot reactivate Bot; a new live grant is still required. |
| Activation grant is lost or duplicated | Grant deadline / duplicate identifiers | Loss prevents activation or causes deadline hard-stop. Duplicate/lower/equal per-term sequence is ignored idempotently. |
| Head missed a state-change publish while broker was down | Publish failure / disconnect | On reconnect, republish current retained desired modes only (§6) — no grant backlog. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| None (self-contained) | — | Mosquitto depends on nothing else in the stack. |

Consumed by: `Head` (log aggregation, RabbitMQ event bridging output), `Bot` (control signal reception, task progress reception), `AI Worker` (control signal reception).

---

## 11. Health Check

**Compose `healthcheck` (required, P1.5):**

```yaml
healthcheck:
  test: ["CMD-SHELL", "mosquitto_sub -h localhost -t '$$SYS/broker/uptime' -C 1 -W 3 || exit 1"]
  interval: 10s
  timeout: 5s
  retries: 5
  start_period: 10s
```

`Head` continues to expose `"mosquitto_connected"` on `/status` for its own client session — that flag is complementary to the container healthcheck, not a substitute.

---

## 12. Versioning & Update Behavior

`Mosquitto` uses a **pinned** `eclipse-mosquitto:2.0.20` (patch-pinned in Compose). Like `RabbitMQ`, it is **not** part of the coordinated application tag (`Launcher.md` §12). Operators bump the pin manually; broker upgrades are outside Scenario 5.
