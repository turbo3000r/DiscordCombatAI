# Service: RabbitMQ

## 1. Responsibility

`RabbitMQ` is the local, exactly-once message broker dedicated **strictly** to communication between `Bot` and the `AI Worker(s)` (per `architecture.md`'s Container Breakdown). It exists to decouple the moment a user requests a battle from the moment that battle is actually generated, so `Bot` never blocks the Discord event loop waiting on Gemini/Langgraph.

Per the explicit design boundary already stated in `architecture.md`: `RabbitMQ` handles *exactly-once task delegation requiring acknowledgment, retry, and single-consumer guarantees*. It must never be merged with or substituted for `Mosquitto`, which handles a fundamentally different pattern (best-effort broadcast, see `mosquitto.md`).

---

## 2. File Structure

`RabbitMQ` runs as an off-the-shelf broker image — there is no project-owned application code for this container, unlike `Head`/`Launcher`. Its only project-specific configuration is:

- The two application queues it holds: `ai_tasks` and `ai_tasks_results`.
- The `rabbitmq_event_exchange` plugin, which must be enabled for `Head`'s log-bridging behavior to work at all (per `architecture.md`'s Mosquitto section: *"A lightweight bridge process (hosted within `Head`) subscribes to RabbitMQ's `rabbitmq_event_exchange` plugin..."*). This is a direct, load-bearing requirement — without this plugin enabled, `Head`'s RabbitMQ log bridging (see Section 7) silently does nothing.

> **Open item:** where this configuration actually lives (a Dockerfile, an `enabled_plugins` file, or `docker-compose.yml` environment/command overrides) is not specified anywhere in the current project file structure (`architecture.md`'s tree has no `rabbitmq/` directory). Flagged as a gap to fill in when the Compose setup is actually written — not decided in this doc.

---

## 3. Environment Variables

**No RabbitMQ-specific environment variables are formally defined anywhere in the project yet.** Per the `formalize existing documentation only` scope of this pass, the following is presented as an honest gap rather than an invented decision:

| Variable | Required | Default | Status |
|---|---|---|---|
| `AI_WORKER_RABBITMQ_HOST` | No | `rabbitmq` | **Formalized** — see `ai_worker.md` §3. |
| `AI_WORKER_RABBITMQ_PORT` | No | `5672` | **Formalized** — see `ai_worker.md` §3. |
| `BOT_RABBITMQ_HOST` | No | `rabbitmq` | **Formalized in this revision** — see `bot/discord_bot.md` §3. Previously flagged as open pending `bot/discord_bot.md`, now written. |
| `BOT_RABBITMQ_PORT` | No | `5672` | **Formalized in this revision** — see `bot/discord_bot.md` §3. |
| Broker credentials (username/password) and vhost | — | — | **Open.** Not mentioned anywhere in `architecture.md` or any written container doc. `ai_worker.md` §3 explicitly flags this as inherited, not resolved. Needs an explicit decision before implementation — default `guest`/`guest` is not viable outside a fully-local, no-exposed-port setup, and even then should be a deliberate choice, not a default left unexamined. |

Unlike Azure (`azure.md`), there is no single shared credential model to point to here — RabbitMQ connection config is expected to be defined per-consumer (`Bot`, `AI Worker`) when their respective docs are written, not centralized in this file.

---

## 4. Inbound Communication

*(Messages arriving at the broker — i.e., publishes.)*

| Source | Channel | Format | Trigger |
|---|---|---|---|
| `Bot` | `ai_tasks` queue (publish, with `reply_to` set) | AI task payload — exact JSON schema not yet defined; belongs in `contracts/` (`NOT YET WRITTEN` per `Readme.md`) | On `/quick-battle` (and presumably any future command routed the same way, per `architecture.md`'s Scenario 2) |
| `AI Worker` | `ai_tasks_results` queue (publish, correlated via `reply_to`) | Generated battle result JSON/text — exact schema not yet defined; belongs in `contracts/` | After Langgraph/Gemini generation completes |
| RabbitMQ (internal) | `rabbitmq_event_exchange` (plugin-internal) | Broker event payloads: consumer disconnects, queue overflows, channel errors | On the underlying broker event occurring |

---

## 5. Outbound Communication

*(Messages leaving the broker — i.e., deliveries to consumers.)*

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| `AI Worker` | `ai_tasks` queue (consume) | Same AI task payload as Section 4 | Continuous consumption loop — `AI Worker` "pulls a task from RabbitMQ" per `architecture.md` |
| `Bot` | `ai_tasks_results` queue (consume, correlated via `reply_to`) | Same result payload as Section 4 | On result delivery |
| `Head` (indirectly, via Mosquitto — not a direct RabbitMQ consumer) | `rabbitmq_event_exchange` → re-published by `Head` onto `logs/warning/rabbitmq` / `logs/errors/rabbitmq` | Broker event, normalized to the standard structured log format | On broker-level event — see `mosquitto.md` §4 for the topic definitions this lands on |

---

## 6. Internal Logic

- **Request/response correlation:** each `ai_tasks` message carries a `reply_to` property so `AI Worker` can respond without a hardcoded destination. Per `architecture.md`: *"Each message from `ai_tasks` should eventually get its response in `ai_tasks_results` using RabbitMQ's `reply_to` functionality."*
  > **Open item:** whether `ai_tasks_results` is one shared queue with per-message `correlation_id` filtering (each `Bot` instance filters for its own requests), or whether `reply_to` actually resolves to a temporary, auto-delete, per-`Bot`-instance queue, is not specified. This matters if `Bot` is ever scaled beyond one active instance per cluster — currently only one `Bot` is ever active at a time (per the leader-election design), so it hasn't mattered in practice, but it's an unresolved detail worth deciding explicitly rather than assuming.
- **Soft stop vs. hard stop — corrected and expanded this revision** (previously a single, less specific "light crash" description; now split to match `bot/discord_bot.md` §6.5's confirmed `drain`/`stop` distinction):
  - **Soft stop (`control/bot/drain`):** No RabbitMQ-level action at all. `Bot` simply stops accepting *new* `/quick-battle` requests; anything already in `ai_tasks` or already claimed by `AI Worker` runs to completion normally.
  - **Hard stop (`control/bot/stop`)** — triggered when `Head` loses internet connectivity while leader: (1) every message currently sitting in `ai_tasks` is purged; (2) any `AI Worker` execution that had already claimed a message and is actively running is **terminated**, not left to finish — `AI Worker`'s Celery task is revoked with `terminate=True` (a hard kill of the worker process handling that task, not a cooperative cancellation flag), since a leader-less `Bot` about to disconnect has no way to consume that task's eventual result anyway; (3) a synthetic error result is published to `ai_tasks_results` for every purged/terminated task, so `Bot` doesn't wait forever; (4) `Bot` delivers that error back to each task's originating Discord thread **before** it disconnects from the Gateway (`bot/discord_bot.md` §6.5) — the ordering matters, since `Bot` can no longer reach Discord at all once disconnected.
- **Design boundary (already decided, restated for this doc's own context):** `RabbitMQ` must not be used for logs, metrics, or coordination signals — that traffic belongs exclusively to `Mosquitto`. `RabbitMQ` is reserved for exactly-once AI task delegation only.

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
| `Head` loses internet connectivity while leader (hard stop) | `Head`'s own election/backoff logic, see `head.md` §6/§9 | Per §6's corrected soft/hard split: `ai_tasks` is purged, any actively-running `AI Worker` execution is terminated (`revoke(terminate=True)`), and a synthetic error result is added to `ai_tasks_results` for each affected task so `Bot` doesn't hang indefinitely and can notify the originating thread before disconnecting. |
| `AI Worker` crashes mid-task | Depends entirely on whether `ai_tasks` uses manual or automatic acknowledgment | **Not specified.** `architecture.md` states the *intent* ("Ensures no tasks are lost if an AI Worker crashes mid-generation") which strongly implies manual ack + requeue-to-another-worker is the intended mechanism, but this has not been explicitly confirmed anywhere as an actual decision. Flagged as an important open item, since the stated goal and the undecided mechanism are currently just an assumption, not a documented guarantee. |
| Queue overflow / broker resource exhaustion | `rabbitmq_event_exchange` event | Bridged to `logs/warning\|errors/rabbitmq` via `Head` (Section 7) for visibility; no automatic corrective action is defined beyond that. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| None (self-contained) | — | `RabbitMQ` itself depends on nothing else in the stack; it uses a local Docker volume for its own data directory. |

Consumed by: `Bot` (publish `ai_tasks`, consume `ai_tasks_results`), `AI Worker` (consume `ai_tasks`, publish `ai_tasks_results`). Bridged (event-level only, not task data) by: `Head`.

> **Open item:** whether `ai_tasks`/`ai_tasks_results` are declared as durable queues with persistent messages (survive a RabbitMQ container restart) or transient/in-memory is not specified. Given the existing "light crash" design already accounts for message loss under a specific failure (Head losing internet), it's unclear whether durability across a plain container restart is also expected — worth an explicit decision.

---

## 11. Health Check

Not specified anywhere. RabbitMQ ships its own diagnostic tooling (`rabbitmq-diagnostics check_running`, management-plugin HTTP API) but whether any of it is wired into this project's own health-check conventions (a Docker Compose `healthcheck:` block, or surfaced through `Head`'s `/status`) is undecided. Flagged as an open item.

---

## 12. Versioning & Update Behavior

`RabbitMQ` is an off-the-shelf broker image, pinned via its tag in `docker-compose.yml` — it is **not** part of the coordinated version tag that `bot`/`head`/`ai_worker`/`web` share (per `Launcher.md` §12). Its own image tag/upgrade policy is a separate, infrastructure-level decision not covered by the application's update flow (`architecture.md` Scenario 5) and not yet decided anywhere.
