# Service: RabbitMQ

## 1. Responsibility

`RabbitMQ` is the local message broker dedicated **strictly** to communication between `Bot` and the `AI Worker(s)` (per `architecture.md`'s Container Breakdown). It exists to decouple the moment a user requests a battle from the moment that battle is actually generated, so `Bot` never blocks the Discord event loop waiting on Gemini/Langgraph.

**Delivery guarantee — corrected this revision (P0.4):** `RabbitMQ` provides **at-least-once delivery, effectively-once outcome**, not "exactly-once" — every prior occurrence of that phrase in this doc and elsewhere was stale and has been corrected. Manual acknowledgement (§6, `contracts/ai_task.md` §2) bounds duplicate *generation* to the crash window between claim and ack; `contracts/ai_task.md` §6's discard-by-task_id-absent-from-map logic is what makes the user-visible *outcome* effectively-once even though the underlying transport can redeliver. Per the design boundary already stated in `architecture.md`: `RabbitMQ` handles task delegation requiring acknowledgment, retry, and single-consumer guarantees — it must never be merged with or substituted for `Mosquitto`, which handles a fundamentally different pattern (best-effort broadcast, see `mosquitto.md`).

**Wire protocol — resolved this revision (P0.4):** `ai_tasks` is a real Celery queue — `Bot` dispatches via `send_task("ai_worker.tasks.run_graph", kwargs={"envelope": {...}}, task_id=task_id, queue="ai_tasks")` and `AI Worker` runs as an actual Celery worker (`celery -A ai_worker.celery_app worker -Q ai_tasks`), not a bare Kombu/AMQP JSON consumer. `ai_tasks_results` stays a plain AMQP/Kombu queue, manually published to by `AI Worker` — **not** Celery's own result backend. Full wire contract: `contracts/ai_task.md` §2/§3/§11.

---

## 2. File Structure

`RabbitMQ` runs as an off-the-shelf broker image — there is no project-owned application code for this container, unlike `Head`/`Launcher`. Project-owned configuration lives under the **target** tree `infra/rabbitmq/` (paths below are the documented target architecture; they may not exist on disk until Phase 0 scaffolding lands):

```
infra/rabbitmq/
├── enabled_plugins          # Must enable rabbitmq_event_exchange (+ rabbitmq_management, §2a)
├── rabbitmq.conf            # Loads definitions and management settings for the official image
└── definitions.json         # Canonical topology: durable queues/exchanges/bindings + dlx/dead_letter
                             # matching contracts/ai_task.md §9. Clients may passive-declare/verify;
                             # they must not create conflicting topology.
```

Compose mounts those files into the official image (see `architecture.md` → Target Compose skeleton). The broker holds:

- Durable `ai_tasks` and `ai_tasks_results`, both with `x-dead-letter-exchange` → shared `dlx` → `dead_letter` (`contracts/ai_task.md` §9).
- Non-default credentials and vhost (§3, §13) — `guest`/`guest` is never used.

**Topology ownership (Phase 2, resolved):** `definitions.json` is the single owner of exchanges, queues, bindings, and DLX arguments. Application code and `shared/messaging/rabbitmq_topology.py` must match those definitions. Passive-time declare is verification-only — never a second competing source of truth.

### 2a. Plugins (resolved, P1.5)

| Plugin | Required | Why |
|---|---|---|
| `rabbitmq_event_exchange` | **Yes** | `Head`'s log bridge subscribes here (`architecture.md` Mosquitto section, this doc §7). Without it, bridging is silently dead. |
| `rabbitmq_management` | **Yes (internal only)** | Diagnostics + Compose health probe convenience. Management UI/API must remain on the Compose network only — **do not** publish host port `15672` in production `docker-compose.yml`. Dev compose may publish it optionally. |

`infra/rabbitmq/rabbitmq.conf` loads `definitions.json` at startup and keeps the management listener internal-only; the official image consumes that file directly, so no custom Dockerfile is required.

### 2b. Threat model / TLS (resolved, P1.5)

**v1 accepts plaintext AMQP on the Compose-internal network only.** Do not publish host port `5672` in production compose. TLS between containers is **not** required for v1; the accepted exposure boundary is “same Docker network + host filesystem for the broker volume,” consistent with plaintext Gemini keys already accepted in §13. Revisit TLS only if brokers are ever exposed beyond that boundary.

---

## 3. Environment Variables

| Variable | Required | Default | Status |
|---|---|---|---|
| `AI_WORKER_RABBITMQ_HOST` | No | `rabbitmq` | **Formalized** — see `ai_worker.md` §3. |
| `AI_WORKER_RABBITMQ_PORT` | No | `5672` | **Formalized** — see `ai_worker.md` §3. |
| `BOT_RABBITMQ_HOST` | No | `rabbitmq` | **Formalized** — see `bot/discord_bot.md` §3. |
| `BOT_RABBITMQ_PORT` | No | `5672` | **Formalized** — see `bot/discord_bot.md` §3. |
| `RABBITMQ_DEFAULT_USER` | Yes | — | **Resolved this revision (P0.4).** Set once on the `rabbitmq` service itself (standard RabbitMQ image variable) — replaces `guest`. |
| `RABBITMQ_DEFAULT_PASS` | Yes | — | **Resolved this revision.** Set once on the `rabbitmq` service. Never logged, never committed — same handling rules as any other secret in this project (`launcher_ipc.md` §2's secret-file convention is the pattern to follow, not literally reused). |
| `RABBITMQ_DEFAULT_VHOST` | No | `/discordcombatai` | **Resolved this revision.** One shared vhost, created once at broker startup. Compose injects this same variable into `head`, `bot`, and `ai_worker`. Clients build the AMQP URL as `amqp://{user}:{pass}@{host}:{port}/{quote(vhost, safe="")}` (default → `/%2Fdiscordcombatai`). |
| `BOT_RABBITMQ_USER` / `BOT_RABBITMQ_PASS` | Yes | — | **Resolved this revision.** `Bot`'s own credentials, same values as `RABBITMQ_DEFAULT_USER`/`_PASS` above — the broker only has one credential pair in v1 (no per-consumer RabbitMQ users yet), but each consumer still reads its own `<SERVICE>_RABBITMQ_USER`/`_PASS` pair rather than a shared unscoped name, mirroring the per-service credential *naming* pattern `azure.md` §3 already established (even though, unlike Azure's per-service Service Principals, this is not yet per-service *scoped* access — flagged in §13). |
| `AI_WORKER_RABBITMQ_USER` / `AI_WORKER_RABBITMQ_PASS` | Yes | — | **Resolved this revision.** Same as above, `AI Worker`'s own env var names. |
| `HEAD_RABBITMQ_USER` / `HEAD_RABBITMQ_PASS` | Yes | — | Head event-bridge credentials (same v1 shared pair). |

Unlike Azure (`azure.md`), RabbitMQ connection host/port remain defined per-consumer (`Bot`, `AI Worker`); credentials/vhost are now defined once here and referenced by name from each consumer's own doc.

---

## 4. Inbound Communication

*(Messages arriving at the broker — i.e., publishes.)*

| Source | Channel | Format | Trigger |
|---|---|---|---|
| `Bot` | `ai_tasks` queue (Celery `send_task("ai_worker.tasks.run_graph", ...)` dispatch, `correlation_id` = `task_id` = Celery task id, `reply_to` not used) | AI task envelope — **schema in `contracts/ai_task.md` §3** | Harness (Phase 2) or `/quick-battle` (later) |
| `AI Worker` | `ai_tasks_results` queue (publish, correlated via `correlation_id`) | Success or synthetic-error result — **schema now defined, `contracts/ai_task.md` §4** (was previously undefined) | After Langgraph/Gemini generation completes, or on hard-stop termination |
| RabbitMQ (internal) | `rabbitmq_event_exchange` (plugin-internal) | Broker event payloads: consumer disconnects, queue overflows, channel errors | On the underlying broker event occurring |

---

## 5. Outbound Communication

*(Messages leaving the broker — i.e., deliveries to consumers.)*

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| `AI Worker` | `ai_tasks` queue (consume) | Same AI task payload as Section 4 | Continuous consumption loop — `AI Worker` "pulls a task from RabbitMQ" per `architecture.md` |
| `Bot` | `ai_tasks_results` queue (consume, auto-ack, correlated via `correlation_id` — not `reply_to`, `contracts/ai_task.md` §2) | Same result payload as Section 4 | On result delivery |
| `Head` (indirectly, via Mosquitto — not a direct RabbitMQ consumer) | `rabbitmq_event_exchange` → re-published by `Head` onto `logs/warning/rabbitmq` / `logs/errors/rabbitmq` | Broker event, normalized to the standard structured log format | On broker-level event — see `mosquitto.md` §4 for the topic definitions this lands on |

---

## 6. Internal Logic

- **Request/response correlation — see `contracts/ai_task.md` §2:** each node has one local durable `ai_tasks_results` queue; its local `AI Worker` copies `task_id` into `correlation_id`, and its local `Bot` filters by that. The design does not depend on a cluster-wide strict single-active-Bot invariant; accepted dual-active overlap still uses separate node-local RabbitMQ instances.
- **Soft stop vs. hard stop — resolved this revision** (matches `bot/discord_bot.md` §6.5's `drain`/`stop` distinction and the full cancellation matrix now canonical in `contracts/ai_task.md` §8):
  - **Soft stop (`control/bot/desired_state` = `draining` or draining grant):** No RabbitMQ-level action at entry. `Bot` rejects new AI work; existing work may finish only within the bounded drain defined by `contracts/drain_status.md` (P0.3), then hard-stop occurs unless control is safely restored.
  - **Hard stop (`control/bot/desired_state` = `stopped` or autonomous grant/watchdog expiry):** (1) purge queued `ai_tasks`; (2) `revoke(task_id, terminate=True)` for claimed/running worker executions; (3) synthesize `AiTaskResultFailed` (`node: "worker_terminated"`) records; (4) notify originating Discord threads before Gateway disconnect (`bot/discord_bot.md` §6.5). **`Bot` performs all of steps 1–3** — resolved in `contracts/ai_task.md` §2/§8: `task_id` is a real Celery task id (`Bot` dispatches via `send_task`), and `Bot` already holds the broker connection, making it the natural and only actor.
- **Design boundary (already decided, restated for this doc's own context):** `RabbitMQ` must not be used for logs, metrics, or coordination signals — that traffic belongs exclusively to `Mosquitto`. `RabbitMQ` is reserved for AI task delegation only, at-least-once delivery with an effectively-once outcome (§1, `contracts/ai_task.md` §2).

---

## 7. Logging

`RabbitMQ` does not participate directly in the shared structured logging pipeline — it has no `logs/<level>/rabbitmq` publisher of its own, since it isn't an application container running project code.

Instead, broker-level events are **bridged** into that pipeline by `Head`: a subscriber (hosted within `Head`) listens to the `rabbitmq_event_exchange` plugin and republishes normalized entries onto `logs/warning/rabbitmq` / `logs/errors/rabbitmq` in the standard format (see `head.md` §7 for the bridging side, `mosquitto.md` §4 for the topic definitions).

**Sensitive data exclusion:** bridged broker events should only ever contain broker-level metadata (consumer ID, queue name, error code) — never the actual message payload, which may contain user-submitted battle prompts or other user content.

---

## 8. Metrics

**Queue-depth / broker metrics are not part of the v1 telemetry contract** (`contracts/telemetry.md`). Operators may inspect depth via the internal management plugin; Head does not upload RabbitMQ queue depth to Table Storage in v1. (P2 if a dashboard consumer is ever defined.)

---

## 8a. Client reconnect / backoff (resolved, P1.5)

Shared policy for `Bot` and `AI Worker` AMQP/Celery clients:

| Parameter | Value |
|---|---|
| Initial delay | `1s` |
| Multiplier | `2` |
| Cap | `60s` |
| Jitter | ±20% |
| Consumer reconnect | Infinite while the process is alive |
| Publisher confirm wait | `5s` — treat timeout/nack as “not sent” |

Celery/Kombu connection retry settings must implement this policy (exact library knobs are implementation). Do not invent a second ad-hoc reconnect loop beside the broker client.

**Celery client settings (Phase 2, resolved):** JSON serialization only; publisher confirms; `task_acks_late=True` on the worker; `worker_prefetch_multiplier=1`; no Celery result backend; no Redis transport. Bot optional dependency is plain `celery` + `kombu` (not `celery[redis]`).

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `RabbitMQ` container itself down (standalone outage; Head/Mosquitto/Azure healthy) | `Bot` `send_task` / confirm fails; `AI Worker` connection drops | **Resolved (P1.5 / S05):** Leadership fencing unchanged — Bot does **not** hard-stop. **Bot:** do not create a `TaskRecord`; surface a localized ephemeral/command error on the AI-backed step that tried to publish (Phase 2 harness asserts the error path — no slash command yet); keep Gateway active. Background result consumer and Celery client reconnect per §8a. **AI Worker:** stop consuming until reconnected; any unacked claim is redelivered after recovery (`contracts/ai_task.md` §2). In-flight Discord workflows still count in `in_flight_workflows` until timeout/cancel (`contracts/drain_status.md`). Stall/overall timers still fire if a result never returns (`contracts/ai_task.md` §8). |
| `Head` loses internet connectivity while leader (hard stop) | `Head`'s own election/backoff logic, see `head.md` §6/§9 | Per §6's corrected soft/hard split: `ai_tasks` is purged, any actively-running `AI Worker` execution is terminated (`revoke(terminate=True)`), and an `AiTaskResultFailed` (`contracts/ai_task.md` §4, `node: "worker_terminated"`) is added to `ai_tasks_results` for each affected task so `Bot` doesn't hang indefinitely and can notify the originating thread before disconnecting. |
| `AI Worker` crashes mid-task | RabbitMQ consumer channel closes without an ack | **Resolved, `contracts/ai_task.md` §2:** manual ack only after publishing `ai_tasks_results` — mid-task crash → redelivery. Accepted cost: graph re-runs (possible duplicate LLM spend); Bot duplicate-result handling is `ai_task.md` §6. |
| `AI Worker` loses broker mid-result-publish | Result publish confirm fails / connection drops before ack | Do **not** ack the inbound `ai_tasks` message. On reconnect, the claim redelivers; Bot discards unknown/`task_id`-absent duplicates if a prior result already completed the map (`ai_task.md` §6). |
| Queue overflow / broker resource exhaustion | `rabbitmq_event_exchange` event | Bridged to `logs/warning\|errors/rabbitmq` via `Head` (Section 7); no automatic purge. |
| Malformed/poison message on either queue | Schema validation failure, either side | **Resolved (P0.4):** nack-without-requeue → `dead_letter` (§2, `contracts/ai_task.md` §9). |
| Publish nacked / unconfirmed | Confirm callback nack or §8a confirm timeout | Treated as “not sent.” Bot: same user-visible failure as standalone outage row. AI Worker result path: leave inbound unacked (redeliver). |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| None (self-contained) | — | `RabbitMQ` itself depends on nothing else in the stack; it uses a local Docker volume for its own data directory. |

Consumed by: `Bot` (publish `ai_tasks`, consume `ai_tasks_results`), `AI Worker` (consume `ai_tasks`, publish `ai_tasks_results`). Bridged (event-level only, not task data) by: `Head`.

> **Resolved this revision, see `contracts/ai_task.md` §2:** both queues are durable, and messages on both are published with `delivery_mode=2` (persistent) — they survive a plain RabbitMQ container restart. This is a distinct guarantee from the hard-stop "light crash" purge (§6), which is a deliberate, explicit clear, not message loss from a restart.

---

## 11. Health Check

**Compose `healthcheck` (required, P1.5):**

```yaml
healthcheck:
  test:
    [
      "CMD-SHELL",
      "rabbitmq-diagnostics -q check_running && rabbitmqctl -q list_queues -p \"$RABBITMQ_DEFAULT_VHOST\" name | grep -qx ai_tasks_results",
    ]
  interval: 10s
  timeout: 5s
  retries: 8
  start_period: 90s
```

Healthy means the broker process is up **and** `definitions.json` has created `ai_tasks_results`. `check_running` alone can pass before the entrypoint's `import_definitions`, which is too early for Bot/AI Worker passive-declare.

Dependent services (`bot`, `ai_worker`, and Head's event-bridge readiness) use `depends_on: condition: service_healthy`. Bot and AI Worker expose `rabbitmq_connected` in their exact heartbeat schemas (`contracts/telemetry.md` §2); it is optional for Head's own status. The broker container check remains separate and fixed here.

---

## 12. Versioning & Update Behavior

`RabbitMQ` uses a **pinned** official image tag in Compose — **target pin: `rabbitmq:3.13-management`** (management plugin image so §2a is available without a custom Dockerfile). It is **not** part of the coordinated `bot`/`head`/`ai_worker`/`web` application tag (`Launcher.md` §12).

**Upgrade policy:** operators bump the pin manually and recreate the broker container. Broker upgrades are outside Scenario 5's application rolling update. Named volume data may require RabbitMQ's normal major-version upgrade notes; v1 does not automate broker migrations.

**Resource limits (Compose target):** `mem_limit: 512m` (or equivalent deploy.resources); rely on RabbitMQ's default memory watermark relative to that cgroup. No separate CPU hard-limit required for v1.

---

## 13. Secret Exposure, Retention, and Credentials (P0.4, resolved)

- **Broker-side plaintext, explicitly accepted:** a guild's `api_key` (`contracts/guild_config.md`) travels plaintext inside the `ai_tasks` message body and sits plaintext in RabbitMQ's own data directory (the named Docker volume, §10) until the message is consumed and removed from the queue. This is consistent with the already-accepted Cosmos-at-rest and transit plaintext-API-key risk documented in `azure.md` §3 and `ai_worker.md` §1 — same trade-off, same reasoning (cheap, self-hosted, user-supplied-key model), now stated for this additional at-rest copy instead of leaving it undocumented. No Key Vault indirection for v1, matching that existing trade-off.
- **Scope of the accepted risk:** RabbitMQ's Docker named volume must be treated as sensitive — excluded from any backup/log-shipping automation that doesn't carry the same protection Cosmos already has. A message's contents (including any `api_key`) carry the same retention/exposure risk as that field already has in Cosmos, only for the shorter window between publish and consume-plus-ack.
- **Broker credentials — resolved this revision:** `guest`/`guest` is never used. `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS`/`RABBITMQ_DEFAULT_VHOST` (§3) are set once on the `rabbitmq` service; `Bot` and `AI Worker` read the same values via their own `<SERVICE>_RABBITMQ_USER`/`<SERVICE>_RABBITMQ_PASS` variables (§3), following the same per-service env-var naming convention `azure.md` §3 established for Service Principals — though unlike Azure, this is a single shared credential pair in v1, not per-service scoped broker permissions (flagged below). Never logged, at any level, by any consumer.
- **Not resolved by this revision:** per-consumer RabbitMQ permissions (vhost-scoped user/permission tags limiting `Bot` to publish-`ai_tasks`/consume-`ai_tasks_results` and `AI Worker` to the inverse) would be a natural next hardening step but are not required for v1's single-shared-credential model; flagged as a candidate P1/P2 follow-up, not a blocker.
