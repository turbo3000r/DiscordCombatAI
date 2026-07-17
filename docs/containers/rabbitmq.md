# Service: RabbitMQ

## 1. Responsibility

`RabbitMQ` is the local message broker dedicated **strictly** to communication between `Bot` and the `AI Worker(s)` (per `architecture.md`'s Container Breakdown). It exists to decouple the moment a user requests a battle from the moment that battle is actually generated, so `Bot` never blocks the Discord event loop waiting on Gemini/Langgraph.

**Delivery guarantee — corrected this revision (P0.4):** `RabbitMQ` provides **at-least-once delivery, effectively-once outcome**, not "exactly-once" — every prior occurrence of that phrase in this doc and elsewhere was stale and has been corrected. Manual acknowledgement (§6, `contracts/ai_task.md` §2) bounds duplicate *generation* to the crash window between claim and ack; `contracts/ai_task.md` §6's discard-by-task_id-absent-from-map logic is what makes the user-visible *outcome* effectively-once even though the underlying transport can redeliver. Per the design boundary already stated in `architecture.md`: `RabbitMQ` handles task delegation requiring acknowledgment, retry, and single-consumer guarantees — it must never be merged with or substituted for `Mosquitto`, which handles a fundamentally different pattern (best-effort broadcast, see `mosquitto.md`).

**Wire protocol — resolved this revision (P0.4):** `ai_tasks` is a real Celery queue — `Bot` dispatches via `apply_async(kwargs={"envelope": {...}}, task_id=task_id, queue="ai_tasks")` and `AI Worker` runs as an actual Celery worker (`celery -A ai_worker worker -Q ai_tasks`), not a bare Kombu/AMQP JSON consumer. `ai_tasks_results` stays a plain AMQP/Kombu queue, manually published to by `AI Worker` — **not** Celery's own result backend. Full wire contract: `contracts/ai_task.md` §2/§3.

---

## 2. File Structure

`RabbitMQ` runs as an off-the-shelf broker image — there is no project-owned application code for this container, unlike `Head`/`Launcher`. Its only project-specific configuration is:

- The two application queues it holds: `ai_tasks` and `ai_tasks_results` — both durable, both declared with `x-dead-letter-exchange` pointing at one shared `dlx` exchange, bound to a single `dead_letter` queue (**new this revision, P0.4**, canonical policy in `contracts/ai_task.md` §9). A message that fails schema validation on either side is nacked without requeue and lands here instead of being retried forever.
- The `rabbitmq_event_exchange` plugin, which must be enabled for `Head`'s log-bridging behavior to work at all (per `architecture.md`'s Mosquitto section: *"A lightweight bridge process (hosted within `Head`) subscribes to RabbitMQ's `rabbitmq_event_exchange` plugin..."*). This is a direct, load-bearing requirement — without this plugin enabled, `Head`'s RabbitMQ log bridging (see Section 7) silently does nothing.
- Non-default broker credentials and vhost (§3, §13) — `guest`/`guest` is never used.

> **Open item:** where this configuration actually lives (a Dockerfile, an `enabled_plugins` file, or `docker-compose.yml` environment/command overrides) is not specified anywhere in the current project file structure (`architecture.md`'s tree has no `rabbitmq/` directory). Flagged as a gap to fill in when the Compose setup is actually written — not decided in this doc.

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
| `RABBITMQ_DEFAULT_VHOST` | No | `/discordcombatai` | **Resolved this revision.** One shared vhost, created once at broker startup. |
| `BOT_RABBITMQ_USER` / `BOT_RABBITMQ_PASS` | Yes | — | **Resolved this revision.** `Bot`'s own credentials, same values as `RABBITMQ_DEFAULT_USER`/`_PASS` above — the broker only has one credential pair in v1 (no per-consumer RabbitMQ users yet), but each consumer still reads its own `<SERVICE>_RABBITMQ_USER`/`_PASS` pair rather than a shared unscoped name, mirroring the per-service credential *naming* pattern `azure.md` §3 already established (even though, unlike Azure's per-service Service Principals, this is not yet per-service *scoped* access — flagged in §13). |
| `AI_WORKER_RABBITMQ_USER` / `AI_WORKER_RABBITMQ_PASS` | Yes | — | **Resolved this revision.** Same as above, `AI Worker`'s own env var names. |

Unlike Azure (`azure.md`), RabbitMQ connection host/port remain defined per-consumer (`Bot`, `AI Worker`); credentials/vhost are now defined once here and referenced by name from each consumer's own doc.

---

## 4. Inbound Communication

*(Messages arriving at the broker — i.e., publishes.)*

| Source | Channel | Format | Trigger |
|---|---|---|---|
| `Bot` | `ai_tasks` queue (Celery `apply_async` dispatch, `correlation_id` = `task_id` = Celery task id, `reply_to` not used) | AI task envelope — **schema now defined, `contracts/ai_task.md` §3** (was previously undefined) | On `/quick-battle` (and presumably any future command routed the same way, per `architecture.md`'s Scenario 2) |
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
  - **Hard stop (`control/bot/desired_state` = `stopped` or autonomous grant/watchdog expiry):** (1) purge queued `ai_tasks`; (2) `revoke(task_id, terminate=True)` for claimed/running worker executions; (3) synthesize `AiTaskResultFailed` (`node: "worker_terminated"`) records; (4) notify originating Discord threads before Gateway disconnect (`bot/discord_bot.md` §6.5). **`Bot` performs all of steps 1–3** — resolved in `contracts/ai_task.md` §2/§8: `task_id` is a real Celery task id (`Bot` dispatches via `apply_async`), and `Bot` already holds the broker connection, making it the natural and only actor.
- **Design boundary (already decided, restated for this doc's own context):** `RabbitMQ` must not be used for logs, metrics, or coordination signals — that traffic belongs exclusively to `Mosquitto`. `RabbitMQ` is reserved for AI task delegation only, at-least-once delivery with an effectively-once outcome (§1, `contracts/ai_task.md` §2).

---

## 7. Logging

`RabbitMQ` does not participate directly in the shared structured logging pipeline — it has no `logs/<level>/rabbitmq` publisher of its own, since it isn't an application container running project code.

Instead, broker-level events are **bridged** into that pipeline by `Head`: a subscriber (hosted within `Head`) listens to the `rabbitmq_event_exchange` plugin and republishes normalized entries onto `logs/warning/rabbitmq` / `logs/errors/rabbitmq` in the standard format (see `head.md` §7 for the bridging side, `mosquitto.md` §4 for the topic definitions).

**Sensitive data exclusion:** bridged broker events should only ever contain broker-level metadata (consumer ID, queue name, error code) — never the actual message payload, which may contain user-submitted battle prompts or other user content.

---

## 8. Metrics

No dedicated RabbitMQ metrics collection is currently defined in any written doc. `template.md`'s own generic guidance flags `tasks_in_queue` as an illustrative example metric name for `ai_worker` — presumably meaning RabbitMQ queue depth — but:

- Which service is responsible for collecting it (AI Worker self-reporting? Head polling the management API?)
- Where it would be stored (Azure Table Storage, alongside Head's existing metrics batching?)

...are both undecided. Flagged as an open item, not assigned to any service yet.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `RabbitMQ` container itself down (independent of any internet/Head issue) | `Bot` publish to `ai_tasks` fails / `AI Worker` connection drops | **Not specified anywhere.** `architecture.md` only defines behavior for the Head-internet-loss case (below) — a standalone RabbitMQ outage while `Head`/`Bot`/internet are otherwise healthy has no documented recovery path. Flagged as a real gap, not just missing detail — this is arguably the single most likely failure mode for a locally-run broker and currently has zero defined behavior. |
| `Head` loses internet connectivity while leader (hard stop) | `Head`'s own election/backoff logic, see `head.md` §6/§9 | Per §6's corrected soft/hard split: `ai_tasks` is purged, any actively-running `AI Worker` execution is terminated (`revoke(terminate=True)`), and an `AiTaskResultFailed` (`contracts/ai_task.md` §4, `node: "worker_terminated"`) is added to `ai_tasks_results` for each affected task so `Bot` doesn't hang indefinitely and can notify the originating thread before disconnecting. |
| `AI Worker` crashes mid-task | RabbitMQ consumer channel closes without an ack | **Resolved this revision, `contracts/ai_task.md` §2:** manual ack, only after publishing the corresponding `ai_tasks_results` message — so a mid-task crash means the original message is redelivered to another consumer. Accepted cost: a redelivered task re-runs the graph from scratch (possible duplicate LLM spend); `Bot`-side duplicate-result handling is `contracts/ai_task.md` §6. |
| Queue overflow / broker resource exhaustion | `rabbitmq_event_exchange` event | Bridged to `logs/warning\|errors/rabbitmq` via `Head` (Section 7) for visibility; no automatic corrective action is defined beyond that. |
| Malformed/poison message on either queue | Schema validation failure, either side | **Resolved this revision (P0.4):** nack-without-requeue, dead-lettered onto the shared `dead_letter` queue (§2, `contracts/ai_task.md` §9). `Head`'s log bridge surfaces a `logs/errors/rabbitmq` line whenever a message lands there; queue depth is the operational signal, no dedicated consumer exists for v1. |
| Publish nacked / unconfirmed (publisher confirms enabled, §2/`contracts/ai_task.md` §2) | Confirm callback reports nack, or confirm times out | Treated as "not sent." On `Bot`'s `ai_tasks` publish: existing retry/error-surfacing logic (row above, standalone-outage gap still open). On `AI Worker`'s `ai_tasks_results` publish: the inbound `ai_tasks` message is simply not acked yet, so it redelivers naturally per the manual-ack ordering — not a second retry loop. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| None (self-contained) | — | `RabbitMQ` itself depends on nothing else in the stack; it uses a local Docker volume for its own data directory. |

Consumed by: `Bot` (publish `ai_tasks`, consume `ai_tasks_results`), `AI Worker` (consume `ai_tasks`, publish `ai_tasks_results`). Bridged (event-level only, not task data) by: `Head`.

> **Resolved this revision, see `contracts/ai_task.md` §2:** both queues are durable, and messages on both are published with `delivery_mode=2` (persistent) — they survive a plain RabbitMQ container restart. This is a distinct guarantee from the hard-stop "light crash" purge (§6), which is a deliberate, explicit clear, not message loss from a restart.

---

## 11. Health Check

Not specified anywhere. RabbitMQ ships its own diagnostic tooling (`rabbitmq-diagnostics check_running`, management-plugin HTTP API) but whether any of it is wired into this project's own health-check conventions (a Docker Compose `healthcheck:` block, or surfaced through `Head`'s `/status`) is undecided. Flagged as an open item.

---

## 12. Versioning & Update Behavior

`RabbitMQ` is an off-the-shelf broker image, pinned via its tag in `docker-compose.yml` — it is **not** part of the coordinated version tag that `bot`/`head`/`ai_worker`/`web` share (per `Launcher.md` §12). Its own image tag/upgrade policy is a separate, infrastructure-level decision not covered by the application's update flow (`architecture.md` Scenario 5) and not yet decided anywhere.

---

## 13. Secret Exposure, Retention, and Credentials (P0.4, resolved)

- **Broker-side plaintext, explicitly accepted:** a guild's `api_key` (`contracts/guild_config.md`) travels plaintext inside the `ai_tasks` message body and sits plaintext in RabbitMQ's own data directory (the named Docker volume, §10) until the message is consumed and removed from the queue. This is consistent with the already-accepted Cosmos-at-rest and transit plaintext-API-key risk documented in `azure.md` §3 and `ai_worker.md` §1 — same trade-off, same reasoning (cheap, self-hosted, user-supplied-key model), now stated for this additional at-rest copy instead of leaving it undocumented. No Key Vault indirection for v1, matching that existing trade-off.
- **Scope of the accepted risk:** RabbitMQ's Docker named volume must be treated as sensitive — excluded from any backup/log-shipping automation that doesn't carry the same protection Cosmos already has. A message's contents (including any `api_key`) carry the same retention/exposure risk as that field already has in Cosmos, only for the shorter window between publish and consume-plus-ack.
- **Broker credentials — resolved this revision:** `guest`/`guest` is never used. `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS`/`RABBITMQ_DEFAULT_VHOST` (§3) are set once on the `rabbitmq` service; `Bot` and `AI Worker` read the same values via their own `<SERVICE>_RABBITMQ_USER`/`<SERVICE>_RABBITMQ_PASS` variables (§3), following the same per-service env-var naming convention `azure.md` §3 established for Service Principals — though unlike Azure, this is a single shared credential pair in v1, not per-service scoped broker permissions (flagged below). Never logged, at any level, by any consumer.
- **Not resolved by this revision:** per-consumer RabbitMQ permissions (vhost-scoped user/permission tags limiting `Bot` to publish-`ai_tasks`/consume-`ai_tasks_results` and `AI Worker` to the inverse) would be a natural next hardening step but are not required for v1's single-shared-credential model; flagged as a candidate P1/P2 follow-up, not a blocker.
