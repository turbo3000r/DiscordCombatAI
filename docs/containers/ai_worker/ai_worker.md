# Service: AI Worker

> **Scope note:** this doc covers the `AI Worker` **container** itself — the Celery process, task routing, retry policy, health, and scaling. It deliberately does **not** re-describe LangGraph internals (nodes, state schemas, control-flow diagrams) — those live in `ai_worker/nodes.md` (shared nodes), `ai_worker/prompts.md` (prompt system), and each graph's own doc (`graphs/environment.md`, `graphs/battle.md`). This doc is what those three point back to for anything container-level (env vars, Celery mechanics, RabbitMQ/Mosquitto wiring) instead of each redefining it.

---

## 1. Responsibility

`AI Worker` is the Generation Engine (per `architecture.md`'s Container Breakdown): a **real Celery worker process** (`celery -A ai_worker.celery_app worker -Q ai_tasks`, `contracts/ai_task.md` §2) consuming `RabbitMQ`'s `ai_tasks` queue via Celery's own task protocol, at **prefetch = 1** (one task at a time, matching the single-execution LangGraph model, §6). For each task it claims, it unwraps the `envelope` kwarg (`contracts/ai_task.md` §3), then either (Phase 2) runs the **transport shell** for `graph="environment"` when `AI_WORKER_TRANSPORT_SHELL=true`, or (Phase 4+) initializes the correct **LangGraph** state machine (`environment` or `battle`), drives it against **Google Gemini**, and manually publishes the finished result onto `ai_tasks_results` — **not** via Celery's result backend (`contracts/ai_task.md` §2/§11).

It is stateless between tasks, has no direct Discord-facing or Azure-facing responsibility (confirmed, `azure.md` §4 — "No direct Azure dependency today"), and can be scaled to multiple instances per PC and across PCs. Unlike `Bot`, it stays active even when the local `Head` is not the cluster leader — task processing is not gated by leader election, only by the `Head`-driven pause/resume signal (§6, node-local). This does **not** mean it's idle only for cluster reasons: since `RabbitMQ` is strictly node-local (`rabbitmq.md` §1, `architecture.md`'s corrected `RabbitMQ` note), a non-leader node's `AI Worker` simply has nothing in its own local `ai_tasks` queue to pull — there is no cross-node task routing anywhere in this system.

**Credential model — gap closed this revision:** `AI Worker` holds **no Gemini credential of its own**. Each `ai_tasks` message carries the requesting guild's own `api_key` and `model` (§3, §4), sourced from `contracts/guild_config.md`'s per-guild fields, staged via `/config`. This was a real contradiction in the previous revision — this doc previously described a single required global `GEMINI_API_KEY`, which is incompatible with `/config`'s per-guild model/key selection actually reaching the worker at all. **Accepted risk, not solved here:** the key travels in plaintext on the RabbitMQ message and is held in plaintext in Cosmos DB — a Key Vault indirection is impractical for this project's cheap, self-hosted, user-supplied-API-key model; revisit only if that cost model changes.

---

## 2. File Structure

```
ai_worker/
├── Dockerfile
├── entrypoint.sh
├── main.py              # Entry point / process wrapper around the Celery worker
├── celery_app.py        # Celery("ai_worker") app — import path ai_worker.celery_app:app
├── tasks.py             # @app.task name ai_worker.tasks.run_graph — routes envelope.graph (§6)
├── nodes/               # Shared LangGraph nodes — see ai_worker/nodes.md (Phase 4+)
│   ├── validation.py    # Validator (nodes.md §2)
│   └── decider.py       # Decider (nodes.md §3)
└── graphs/              # LangGraph graphs — see graphs/environment.md, graphs/battle.md (Phase 4+)
    ├── environment/
    │   ├── graph.py
    │   └── nodes/        # graph-specific: RouteInput, Generator, Normalise, Enhancer
    └── battle/
        ├── graph.py
        └── nodes/        # graph-specific: Predefine, CreateSkeleton, Implement*Episode, Modifier, ResolveWinners
```

> **Phase 2 note:** `celery_app.py` + `tasks.py` + transport-shell branch are required now. Real graph packages under `graphs/` and shared `nodes/` may exist as stubs but must not be required to pass the transport harness when `AI_WORKER_TRANSPORT_SHELL=true`.

> **Correction applied earlier:** `architecture.md`'s Project File Structure previously listed only `validation.py` under `nodes/` — `decider.py` was missing despite `ai_worker/nodes.md` already documenting it as shared code. Both this doc and `architecture.md` show `decider.py`.

`prompts/` (repo root, **not** under `ai_worker/`) is mounted into this container at runtime — its structure, injection pattern, and per-node mapping are fully owned by `ai_worker/prompts.md`, not duplicated here.

---

## 3. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `APPLICATION_VERSION` | Yes | — | Exact coordinated release tag injected by Compose. Included in the canonical heartbeat and required to match `contracts/launcher_ipc.md` §4's grammar. Startup fails if absent/invalid. |
| `AI_WORKER_NODE_ID` | Yes | — | Host node identity. Must equal the deployment's `NODE_ID` / `HEAD_NODE_ID` / `BOT_NODE_ID`. Grammar `^[A-Za-z0-9._-]+$`, length 1–128. Compose injects from host `NODE_ID`. |
| `AI_WORKER_RABBITMQ_HOST` | No | `rabbitmq` | Hostname of the local RabbitMQ broker. Formalizes the variable `rabbitmq.md` §3 previously flagged as expected-but-open. |
| `AI_WORKER_RABBITMQ_PORT` | No | `5672` | RabbitMQ broker port. |
| `AI_WORKER_RABBITMQ_USER` / `AI_WORKER_RABBITMQ_PASS` | Yes | — | **Resolved (P0.4)** — `AI Worker`'s own broker credentials, canonical definition in `rabbitmq.md` §3/§13. Never logged. |
| `RABBITMQ_DEFAULT_VHOST` | No | `/discordcombatai` | Shared vhost; required for broker URL construction (`contracts/ai_task.md` §2). Injected by Compose. |
| `AI_WORKER_MOSQUITTO_HOST` | No | `mosquitto` | Hostname of the local Mosquitto broker. Formalizes the variable `mosquitto.md` §3 previously flagged as expected-but-open. |
| `AI_WORKER_MOSQUITTO_PORT` | No | `1883` | Mosquitto broker port. |
| `AI_WORKER_LLM_MAX_RETRIES` | No | `2` | **Canonical home for this variable**. Shared retry budget for transient Gemini API failures **and** malformed/unparseable structured LLM output — Phase 4+ graphs. Not used by the Phase 2 transport shell. |
| `AI_WORKER_CELERY_CONCURRENCY` | No | `1` | Number of tasks a single `AI Worker` instance processes concurrently. |
| `AI_WORKER_PROGRESS_HEARTBEAT_SEC` | No | `30` | Confirmed cadence: while a task is in flight, re-publish the current progress tick (`contracts/task_progress.md` §3). Four heartbeat opportunities fit within Bot's 120-second stall window. |
| `AI_WORKER_HEARTBEAT_INTERVAL_SEC` | No | `30` | Cadence of `status/ai_worker/heartbeat`; Head marks it stale after `HEAD_SERVICE_HEARTBEAT_STALE_SEC` (default 90). Canonical schema: `contracts/telemetry.md` §2.2. |
| `AI_WORKER_TRANSPORT_SHELL` | No | `false` | **Phase 2.** When `true`, `run_graph` uses the canned `environment` success path (`contracts/ai_task.md` §11) instead of LangGraph. Harness/compose-test only; production images keep `false`. |

> **`GEMINI_API_KEY` is deliberately NOT listed here (corrected this revision).** A previous revision of this doc listed it as a single required global environment variable — that directly contradicted `/config`'s per-guild key/model selection (`bot/commands/config.md`, `contracts/guild_config.md` §3) ever reaching this container. `AI Worker` is credential-stateless: the key and model it uses for a given task arrive **on that task's own `ai_tasks` message** (§4) — see the Credential model note in §1. Never logged, from any task, at any level (§7).

> **RabbitMQ broker credentials (username/password/vhost) are resolved project-wide this revision** — see `rabbitmq.md` §3/§13, referenced above rather than duplicated.
>
> **Graph-specific tunables are NOT listed here, by design** — `ENVIRONMENT_MAX_ENHANCER_RETRIES` (`graphs/environment.md` §8), `BATTLE_MIN_EPISODES`, and `BATTLE_MAX_MODIFIER_RETRIES` (`graphs/battle.md` §8) belong permanently in their own graph docs, per the same "don't duplicate a variable defined elsewhere" convention `azure.md` §3 established. Only `AI_WORKER_LLM_MAX_RETRIES` lives here, because it's graph-agnostic.
>
> Graph-specific hard bounds are also canonical there: environment 600s / 120k input / 30k output tokens; battle 840s / 350k input / 90k output tokens. `AI Worker` accumulates provider-reported usage across retries, reserves the next node's configured maximum output before calling, and fails before a call that cannot fit the remaining deadline/token budget. There is no fixed USD cap because guild-selected model pricing is external and mutable; call/token/deadline ceilings are the enforceable v1 cost bound.

---

## 4. Inbound Communication

| Source | Channel | Format | Trigger |
|---|---|---|---|
| `RabbitMQ` (via Celery) | `ai_tasks` queue (consume as a real Celery task, `envelope` kwarg, manual ack, prefetch = 1) | AI task envelope — **schema now fully defined, `contracts/ai_task.md` §3** (was previously undefined; this revision's earlier credential-gap fix (`api_key`/`model` per-task) is now formalized there, not just here) | Continuous consumption loop, while in `RUNNING` state (§6) |
| `Mosquitto` | `control/ai_worker/desired_state` (**retained**, redesigned this revision) | `{"state": "running" \| "paused"}` | `Head` publishes during update/maintenance windows, **and** immediately on `AI Worker`'s own every (re)connect to Mosquitto, per MQTT's retained-message semantics (`architecture.md`, `mosquitto.md` §4/§6) |
| Google Gemini API | HTTPS response | Generated text / structured output | Synchronous response to each LLM-backed node's call |

---

## 5. Outbound Communication

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| `RabbitMQ` (manual publish, not Celery result backend) | `ai_tasks_results` queue (publish, `correlation_id` set manually to `task_id`) | Success or error result — **schema now fully defined, `contracts/ai_task.md` §4** | On graph completion — success (§6) or synthetic error (§9), followed by acking the original `ai_tasks` message (`contracts/ai_task.md` §2) |
| `Mosquitto` | `progress/ai_worker/<task_id>` | Phase-update message, per `docs/contracts/task_progress.md` §4 | On each phase transition within a running graph — see §6 and each graph's own §11 phase mapping |
| `Mosquitto` | `logs/<level>/ai_worker` | Structured log string, per §7 | On every log emission |
| `Mosquitto` | `status/ai_worker/heartbeat` | Canonical versioned worker state/dependency payload (`contracts/telemetry.md` §2.2) | Every `AI_WORKER_HEARTBEAT_INTERVAL_SEC` |
| `Mosquitto` | `status/ai_worker/pause_ack` (QoS 1, not retained) — **new this revision, P0.3** | `{"schema_version": 1, "node_id": ..., "paused_at": ISO8601}` | Once, after receiving `control/ai_worker/desired_state = paused` (§4), finishing any in-flight task claim, and going idle (no new claims picked up) — §6 |
| Google Gemini API | HTTPS request | Assembled prompt (system prompt + injected elements, per `ai_worker/prompts.md` §2) | On every LLM-backed node's call |

---

## 6. Internal Logic / State Machine

**Consumption state (control loop, driven by `Head` via Mosquitto):**

```
        ┌───────────┐   desired_state = "paused"    ┌───────────┐
   ┌───►│  RUNNING  │ ─────────────────────────────►│  PAUSED   │────┐
   │    │(consuming)│ ◄─────────────────────────────│(idle, no  │    │
   │    └───────────┘   desired_state = "running"    │new claims)│    │
   └─────────────────────────────────────────────────────────────────┘
```

- **`RUNNING`** (default) — actively consuming from `ai_tasks` at prefetch = 1 (`contracts/ai_task.md` §2). Entered whenever `control/ai_worker/desired_state` resolves to `running` — including immediately on every (re)connect to Mosquitto, per the retained-message redesign (§4, `mosquitto.md` §6), not just on a live publish.
- **`PAUSED`** — on receiving `desired_state = paused`, `AI Worker` finishes its current task claim (does not abandon mid-graph — same "let it finish" behavior prefetch = 1 already implies, since there is at most one claim in flight per worker process) and then stops pulling *new* messages from `ai_tasks`. Once idle (no claim in flight), it publishes `status/ai_worker/pause_ack` once (§5, **new this revision, P0.3**). This is what makes `Head`'s `DRAINING` state (`head.md` §6) actually work: `Head` pauses `AI Worker` at the start of an update sequence (`head.md` §5) to stop new work from starting, while whatever's already running is allowed to finish naturally within the drain window defined by `contracts/drain_status.md`.
  - **`pause_ack` is informational/diagnostic only — it does not gate `Head`'s drain-complete decision.** `Bot`'s own `in_flight_workflows == 0` (`contracts/drain_status.md` §1) already covers whether this node's `AI Worker` claim has finished, since every `ai_tasks` entry is counted there too. Treating `pause_ack` as a second blocking condition would risk stalling drain if the ack itself is lost over best-effort Mosquitto — deliberately avoided.
  - **Recovery when an update is abandoned/superseded mid-drain:** `Head` simply re-publishes `{"state": "running"}` (resume) — no teardown/unwind needed since nothing was torn down while `PAUSED` (no in-flight task is ever aborted by pausing).

Independently of task-progress ticks, the process publishes `status/ai_worker/heartbeat` every `AI_WORKER_HEARTBEAT_INTERVAL_SEC` with required `APPLICATION_VERSION`, `running|paused` state, `active_tasks`, and latest RabbitMQ connection state (`contracts/telemetry.md` §2.2). It does not probe Gemini: credentials and reachability are task/guild-specific. Head uses local receipt time and marks the heartbeat stale after 90 seconds by default; staleness is diagnostic and does not itself change worker consumption state.

**Per-task flow (while `RUNNING`):**

1. Claim one message from `ai_tasks`.
2. Publish a `launching` phase update (§5; `docs/contracts/task_progress.md` §4), and start a background timer that re-publishes the current phase every `AI_WORKER_PROGRESS_HEARTBEAT_SEC` (§3, new this revision) for as long as this task is in flight — independent of whether the phase itself has changed.
3. Read the message's `graph` discriminator field; initialize the corresponding compiled LangGraph (`environment` or `battle`) with the message body as initial state (per that graph's own Input State contract, `graphs/environment.md` §2 / `graphs/battle.md` §2).
4. Invoke the graph to completion. As execution crosses each graph's own internal phase boundaries, publish `composing` / `refining` / `finishing` phase updates — the mapping from internal nodes to these phases is defined once per graph in `docs/contracts/task_progress.md` §6.1, not re-derived here. Each phase-change publish also resets the heartbeat timer from step 2 (no need to publish twice in quick succession).
5. On success, manually publish the graph's output state as the `ai_tasks_results` message (correlated via `correlation_id = task_id`, publisher confirms enabled, `contracts/ai_task.md` §2) and, once that publish is confirmed, acknowledge the `ai_tasks` message. Stop the heartbeat timer.
6. On unrecoverable failure (§9), publish an `AiTaskResultFailed` (`contracts/ai_task.md` §4) instead — `node` set to whichever LangGraph node was actually executing when the failure occurred (not a generic code, corrected this revision). Stop the heartbeat timer.

The progress heartbeat timer runs independently of a pending Gemini request. Graph execution uses a monotonic per-graph deadline and cumulative token ledger; retries are not started when the next attempt cannot fit the remaining bound. Deadline/token exhaustion is an ordinary failed result, never a partial success.

**Graph selection mechanism — resolved P0.4 / Phase 2 path names:** `tasks.py` defines one generic Celery task registered as **`ai_worker.tasks.run_graph`**, bound to the single `ai_tasks` queue, on app **`ai_worker.celery_app:app`**. It unwraps the `envelope` kwarg and branches on `envelope.graph`. Phase 2 transport shell (`AI_WORKER_TRANSPORT_SHELL=true`) handles `environment` with the canned result (`contracts/ai_task.md` §11) and does not call LangGraph. There is no per-graph Celery task, no per-graph queue, and no `stub` graph discriminator.

**Statelessness / scaling:** no state survives across tasks within a worker instance — any `AI Worker` instance can claim any `ai_tasks` message. This is the precondition that makes "scale to multiple instances per PC" (`architecture.md`) safe without any coordination between instances beyond RabbitMQ's own single-consumer-per-message delivery guarantee.

---

## 7. Logging

- Same shared structured format as every other service (`architecture.md`'s Mosquitto section), with graph-execution context folded in per `graphs/environment.md` §11 / `graphs/battle.md` §11:
  ```
  [%time%][%level%][ai_worker][graphs/<graph>/nodes/<node>]<trace_id, guild_id, task_id, attempt_index>: [%message%]
  ```
- Container-level events (task claimed, task completed, pause/resume transitions) log under `[ai_worker][tasks.py]` rather than a specific node path.
- **Recommended minimum tags:** `trace_id`, `guild_id`, `task_id`, `graph`, `attempt_index` (where applicable) — consistent with both graph docs' own logging recommendations.
- **Sensitive data exclusion:** each task's `api_key` field (§1, §4 — sourced per-guild, not a global env var) must never be logged, at any level, by any node in this graph or by the Celery task wrapper itself. Full prompt/response text (which may embed user-submitted battle descriptions or comments) should not be logged at `INFO` or above — reserve full-content logging for `DEBUG` only, since `INFO`-and-above lines are the ones `Head` aggregates and archives to Blob Storage long-term (`head.md` §7). This mirrors the same caution `rabbitmq.md` §7 already applies to bridged broker events.

---

## 8. Metrics

The complete v1 AI Worker observability surface is the heartbeat in `contracts/telemetry.md` §2.2 plus task progress/logs. `state`, `active_tasks`, and `rabbitmq_connected` are operational health, not persisted business metrics.

`tasks_processed_total`, `tasks_failed_total`, per-graph duration, `attempts_used`, `forced_selection`, and RabbitMQ queue depth have no agreed v1 consumer/transport and are explicitly deferred. Do not create a `metrics/ai_worker/*` topic or Azure dependency ad hoc.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Gemini API call fails/times out, or any LLM node returns structurally invalid output | Exception / schema validation error inside a node | Shared retry-with-backoff wrapper, budget `AI_WORKER_LLM_MAX_RETRIES` (§3, default `2`) — one unified budget for both failure shapes, per `ai_worker/nodes.md` §5. If exhausted, the task fails — see next row. |
| A graph's LLM retry budget is exhausted, or any other unhandled exception occurs during graph execution | Exception propagates out of the graph invocation (§6 step 4) | `AiTaskResultFailed` published to `ai_tasks_results` (§6 step 6, `contracts/ai_task.md` §4) with `node` set to whichever node was executing. The original `ai_tasks` message is then acknowledged (publish-before-ack ordering, `contracts/ai_task.md` §2). |
| `AI Worker` process crashes mid-task (before ack) | RabbitMQ consumer channel closes without an ack | **Resolved this revision, `contracts/ai_task.md` §2:** manual ack only after the corresponding `ai_tasks_results` message is published, so the original message is redelivered to another consumer. Accepted cost: possible duplicate LLM generation on redelivery; `Bot`-side dedup is `contracts/ai_task.md` §6, not this container's concern. |
| `RabbitMQ` itself unreachable | Connection failure on consume or publish | **Resolved (P1.5):** Celery/Kombu reconnect per `rabbitmq.md` §8a (1s → ×2 → cap 60s + jitter). Do not ack until result publish succeeds. Process stays alive and idle until the broker returns. See S05. |
| `Mosquitto` unreachable | Publish failure on `progress/*`, `logs/*`, `status/*`, or subscribe failure on `control/*` | **Must never block or fail the task itself** (confirmed, per `docs/contracts/task_progress.md` §7) — log-and-continue for progress/log/heartbeat publishing. For `control/ai_worker/desired_state` specifically: **resolved this revision** via the retained-message redesign (`mosquitto.md` §6) — if `Mosquitto` is down exactly when `Head` needs to pause `AI Worker`, `AI Worker` simply receives the retained `paused` state the instant it next (re)connects/(re)subscribes, rather than never learning about it (the residual case of `Head` itself being unable to publish at all is unchanged, `mosquitto.md` §9). |
| `AI Worker` container itself restarted (e.g. Docker restart policy) | N/A | No persistent state to recover (§6, stateless-between-tasks) — resumes consuming from `ai_tasks` immediately once reconnected. Any task it had claimed but not finished falls under the "process crashes mid-task" row above. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| `RabbitMQ` (local) | Claiming `ai_tasks`, publishing `ai_tasks_results` | Core dependency — reconnect per `rabbitmq.md` §8a / §9. |
| `Mosquitto` (local) | `control/ai_worker/*` signals, `progress/ai_worker/*` updates, logs, heartbeat | Best-effort only — task processing itself never blocks on Mosquitto (§9). |
| Google Gemini API | Every LLM-backed node, across both graphs | Required — failures handled via `AI_WORKER_LLM_MAX_RETRIES` (§3, §9); exhaustion fails the task. |
| `ai_worker/nodes.md`, `ai_worker/prompts.md` | Shared `Validator`/`Decider` code and every node's prompt content | Internal documentation dependency, not a runtime one — listed for completeness per this project's "link, don't duplicate" convention. |
| `graphs/environment.md`, `graphs/battle.md` | The two LangGraph graphs this container actually runs | Same as above. |
| `docs/contracts/task_progress.md`, `docs/contracts/localization.md` | Phase-update contract; `language_locale`'s meaning (arrives pre-resolved in task input, `AI Worker` never sources it itself) | Same as above. |
| **Not a dependency (explicit):** Azure (any resource) | — | Confirmed, `azure.md` §4 — all cloud I/O for AI tasks flows through `Bot`/`Head`, not `AI Worker` directly. |

---

## 11. Health Check

`AI Worker` publishes the exact `status/ai_worker/heartbeat` schema in `contracts/telemetry.md` §2.2. It exposes no HTTP endpoint. RabbitMQ connection state is included; Gemini is deliberately excluded because a global probe cannot represent per-guild credentials. Head marks the worker stale after the canonical monotonic threshold. No additional worker readiness endpoint is part of v1.

---

## 12. Versioning & Update Behavior

- `AI Worker` shares the coordinated version tag with `Bot`, `Head`, and `Web` (per `Launcher.md` §12) — it does not version independently.
- The local container receives required `APPLICATION_VERSION` from Compose; Launcher recreates it only as part of the fixed `head`/`bot`/`ai_worker` image set.
- **Update sequence participation:** during `Head`'s `DRAINING` → `UPDATING` transition (`head.md` §6), `Head` publishes `control/ai_worker/desired_state = {"state": "paused"}` (retained) at update-sequence start (`head.md` §5) — `AI Worker` finishes any task already claimed, then stops claiming new `ai_tasks` messages (§6, `PAUSED` state) and publishes `status/ai_worker/pause_ack` once idle (§5/§6, informational only — does not gate `Head`'s drain-complete decision, `contracts/drain_status.md`). `Head` publishes `{"state": "running"}` once the update sequence concludes or is abandoned — no teardown/unwind needed on abandonment since `PAUSED` never aborts an in-flight claim.
- After `Launcher` recreates the container, a fresh `AI Worker` instance starts directly in `RUNNING` and resumes consuming — no state to restore (§6, §11).

---

## 13. Open Items / Future Work

*(Deviation from `template.md`'s 12-section schema, noted explicitly: this section is additive, mirroring the "Open Items" section already used in `graphs/*.md` and `nodes.md`, since this doc surfaced enough new gaps to warrant collecting them in one place rather than scattering them across §6/§8/§9/§11 only.)*

- ~~`GEMINI_API_KEY` as a single global required env var contradicted `/config`'s per-guild key/model selection~~ — **resolved this revision** (§1, §3, §4): credentials arrive per-task on the `ai_tasks` message, sourced from `contracts/guild_config.md`. Plaintext-in-transit/at-rest remains an accepted risk (§1), not solved by this fix.
- Metrics without a v1 transport/consumer are explicitly deferred rather than left as an open contract (§8).
- ~~The Celery-level task/graph dispatch mechanism (one generic task vs. one task per graph) was undecided~~ — **resolved P0.4** (§6): one generic `ai_worker.tasks.run_graph` task, branching internally on `envelope.graph`.
- ~~Celery app import path / obsolete `ai_worker_tasks` name~~ — **resolved Phase 2:** `ai_worker.celery_app:app` + task name `ai_worker.tasks.run_graph`.
- ~~Phase 2 transport stub discriminator~~ — **resolved:** reuse `graph="environment"` + `AI_WORKER_TRANSPORT_SHELL`; no `stub` graph (`contracts/ai_task.md` §11).
- ~~RabbitMQ ack semantics (manual vs. automatic)~~ — **resolved**, `contracts/ai_task.md` §2.
- ~~RabbitMQ broker credentials (username/password/vhost)~~ — **resolved, P0.4**: `rabbitmq.md` §3/§13.
- Heartbeat schema, dependency scope, and staleness are resolved in `contracts/telemetry.md` §2 (§11).
- ~~Standalone RabbitMQ outage behavior~~ — **resolved (P1.5):** `rabbitmq.md` §8a/§9, this doc §9, S05.
- Mosquitto control loss remains fail-closed via Head/Bot leadership contracts; AI Worker pause/resume simply cannot be delivered while the broker is down (accepted).
- **Progress and graph timing are confirmed:** heartbeat 30s; environment deadline 600s; battle deadline 840s; Bot stall/overall 120s/900s (`ai_task.md`, both graph docs).
- ~~Whether `Bot` publishes `ai_tasks` via a real Celery client or raw AMQP~~ — **resolved P0.4 / Phase 2**: Bot uses `send_task("ai_worker.tasks.run_graph", ...)`.
