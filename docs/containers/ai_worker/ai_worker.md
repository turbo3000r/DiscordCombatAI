# Service: AI Worker

> **Scope note:** this doc covers the `AI Worker` **container** itself — the Celery process, task routing, retry policy, health, and scaling. It deliberately does **not** re-describe LangGraph internals (nodes, state schemas, control-flow diagrams) — those live in `ai_worker/nodes.md` (shared nodes), `ai_worker/prompts.md` (prompt system), and each graph's own doc (`graphs/environment.md`, `graphs/battle.md`). This doc is what those three point back to for anything container-level (env vars, Celery mechanics, RabbitMQ/Mosquitto wiring) instead of each redefining it.

---

## 1. Responsibility

`AI Worker` is the Generation Engine (per `architecture.md`'s Container Breakdown): a **Celery** worker running an infinite consume loop against `RabbitMQ`'s `ai_tasks` queue. For each task it claims, it initializes the correct **LangGraph** state machine (`environment` or `battle`, selected by a field on the task payload — see §6), drives it to completion against the **Google Gemini API**, and publishes the finished result onto `ai_tasks_results`.

It is stateless between tasks, has no direct Discord-facing or Azure-facing responsibility (confirmed, `azure.md` §4 — "No direct Azure dependency today"), and can be scaled to multiple instances per PC and across PCs. Unlike `Bot`, it stays active even when the local `Head` is not the cluster leader — task processing is not gated by leader election, only by the `Head`-driven pause/resume signal (§6, node-local). This does **not** mean it's idle only for cluster reasons: since `RabbitMQ` is strictly node-local (`rabbitmq.md` §1, `architecture.md`'s corrected `RabbitMQ` note), a non-leader node's `AI Worker` simply has nothing in its own local `ai_tasks` queue to pull — there is no cross-node task routing anywhere in this system.

**Credential model — gap closed this revision:** `AI Worker` holds **no Gemini credential of its own**. Each `ai_tasks` message carries the requesting guild's own `api_key` and `model` (§3, §4), sourced from `contracts/guild_config.md`'s per-guild fields, staged via `/config`. This was a real contradiction in the previous revision — this doc previously described a single required global `GEMINI_API_KEY`, which is incompatible with `/config`'s per-guild model/key selection actually reaching the worker at all. **Accepted risk, not solved here:** the key travels in plaintext on the RabbitMQ message and is held in plaintext in Cosmos DB — a Key Vault indirection is impractical for this project's cheap, self-hosted, user-supplied-API-key model; revisit only if that cost model changes.

---

## 2. File Structure

```
ai_worker/
├── Dockerfile
├── entrypoint.sh
├── main.py              # Entry point: starts the Celery worker, registers tasks
├── tasks.py             # Celery task definitions — routes each ai_tasks message to the correct graph (§6)
├── nodes/               # Shared LangGraph nodes — see ai_worker/nodes.md
│   ├── validation.py    # Validator (nodes.md §2)
│   └── decider.py       # Decider (nodes.md §3)
└── graphs/              # LangGraph graphs — see graphs/environment.md, graphs/battle.md
    ├── environment/
    │   ├── graph.py
    │   └── nodes/        # graph-specific: RouteInput, Generator, Normalise, Enhancer
    └── battle/
        ├── graph.py
        └── nodes/        # graph-specific: Predefine, CreateSkeleton, Implement*Episode, Modifier, ResolveWinners
```

> **Correction applied in this revision:** `architecture.md`'s Project File Structure previously listed only `validation.py` under `nodes/` — `decider.py` was missing despite `ai_worker/nodes.md` already documenting it as shared code (promoted from graph-specific, see `nodes.md` §3). Both this doc and `architecture.md` now show `decider.py`.

`prompts/` (repo root, **not** under `ai_worker/`) is mounted into this container at runtime — its structure, injection pattern, and per-node mapping are fully owned by `ai_worker/prompts.md`, not duplicated here.

---

## 3. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AI_WORKER_RABBITMQ_HOST` | No | `rabbitmq` | Hostname of the local RabbitMQ broker. Formalizes the variable `rabbitmq.md` §3 previously flagged as expected-but-open. |
| `AI_WORKER_RABBITMQ_PORT` | No | `5672` | RabbitMQ broker port. |
| `AI_WORKER_MOSQUITTO_HOST` | No | `mosquitto` | Hostname of the local Mosquitto broker. Formalizes the variable `mosquitto.md` §3 previously flagged as expected-but-open. |
| `AI_WORKER_MOSQUITTO_PORT` | No | `1883` | Mosquitto broker port. |
| `AI_WORKER_LLM_MAX_RETRIES` | No | `2` | **Canonical home for this variable** (confirmed decision, originally introduced in `graphs/environment.md` §8/§9 and `graphs/battle.md` §8/§9, both of which deferred to this doc once written). Shared retry budget for transient Gemini API failures **and** malformed/unparseable structured LLM output — one unified wrapper around every LLM-backed node call, across every graph. Not graph-specific — see `ai_worker/nodes.md` §5. |
| `AI_WORKER_CELERY_CONCURRENCY` | No | `1` | Number of tasks a single `AI Worker` instance processes concurrently. Default of `1` is a conservative starting point (each task is already a multi-call LLM pipeline); horizontal scaling (§6, per `architecture.md`) is the primary scaling lever, not per-instance concurrency. |

> **`GEMINI_API_KEY` is deliberately NOT listed here (corrected this revision).** A previous revision of this doc listed it as a single required global environment variable — that directly contradicted `/config`'s per-guild key/model selection (`bot/commands/config.md`, `contracts/guild_config.md` §3) ever reaching this container. `AI Worker` is credential-stateless: the key and model it uses for a given task arrive **on that task's own `ai_tasks` message** (§4) — see the Credential model note in §1. Never logged, from any task, at any level (§7).

> **RabbitMQ broker credentials (username/password/vhost) remain an open item inherited from `rabbitmq.md` §3** — not resolved here. This doc only formalizes the host/port variables that follow the existing `HEAD_*`-style per-consumer prefix convention (`head.md` §3); actual authentication config for `AI Worker`'s RabbitMQ connection is still undecided project-wide, not an `ai_worker`-specific gap.
>
> **Graph-specific tunables are NOT listed here, by design** — `ENVIRONMENT_MAX_ENHANCER_RETRIES` (`graphs/environment.md` §8), `BATTLE_MIN_EPISODES`, and `BATTLE_MAX_MODIFIER_RETRIES` (`graphs/battle.md` §8) belong permanently in their own graph docs, per the same "don't duplicate a variable defined elsewhere" convention `azure.md` §3 established. Only `AI_WORKER_LLM_MAX_RETRIES` lives here, because it's graph-agnostic.

---

## 4. Inbound Communication

| Source | Channel | Format | Trigger |
|---|---|---|---|
| `RabbitMQ` | `ai_tasks` queue (consume) | AI task payload — exact JSON schema not yet formally defined in `contracts/` (still unwritten), but must include at minimum: a `graph: "environment" \| "battle"` discriminator (implied by `docs/contracts/task_progress.md` §4's message schema); each graph's own input contract (`graphs/environment.md` §2 / `graphs/battle.md` §2); and — **added this revision, closing the credential gap in §1** — `api_key: str` and `model: str`, the requesting guild's own Gemini credential/model pair (`contracts/guild_config.md` §3), which `Bot` must read and attach when it publishes the message, since `AI Worker` itself has no other way to obtain them. | Continuous consumption loop, while in `RUNNING` state (§6) |
| `Mosquitto` | `control/ai_worker/pause` \| `control/ai_worker/resume` | Plain signal | `Head` publishes during update/maintenance windows (`architecture.md`, `mosquitto.md` §4) |
| Google Gemini API | HTTPS response | Generated text / structured output | Synchronous response to each LLM-backed node's call |

---

## 5. Outbound Communication

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| `RabbitMQ` | `ai_tasks_results` queue (publish, correlated via `reply_to`) | Generated result (finished `Environment` or battle `story`+`winners`) — exact JSON schema not yet formally defined in `contracts/` | On graph completion — success (§6) or synthetic error (§9) |
| `Mosquitto` | `progress/ai_worker/<task_id>` | Phase-update message, per `docs/contracts/task_progress.md` §4 | On each phase transition within a running graph — see §6 and each graph's own §11 phase mapping |
| `Mosquitto` | `logs/<level>/ai_worker` | Structured log string, per §7 | On every log emission |
| `Mosquitto` | `status/ai_worker/heartbeat` | Liveness ping | Periodic |
| Google Gemini API | HTTPS request | Assembled prompt (system prompt + injected elements, per `ai_worker/prompts.md` §2) | On every LLM-backed node's call |

---

## 6. Internal Logic / State Machine

**Consumption state (control loop, driven by `Head` via Mosquitto):**

```
        ┌───────────┐   control/ai_worker/pause    ┌───────────┐
   ┌───►│  RUNNING  │ ─────────────────────────────►│  PAUSED   │────┐
   │    │(consuming)│ ◄─────────────────────────────│(idle, no  │    │
   │    └───────────┘   control/ai_worker/resume    │new claims)│    │
   └─────────────────────────────────────────────────────────────────┘
```

- **`RUNNING`** (default) — actively consuming from `ai_tasks`.
- **`PAUSED`** — stops pulling *new* messages from `ai_tasks`. **Does not abort in-flight tasks already claimed** — a graph execution already underway runs to completion regardless of pause state. This is what makes `Head`'s `DRAINING` state (`head.md` §6, waiting up to `HEAD_DRAIN_TIMEOUT_SEC` for in-flight `ai_tasks` to resolve) actually work: `Head` pauses `AI Worker` at the start of an update sequence (`head.md` §5) to stop new work from starting, while whatever's already running is allowed to finish naturally within the drain window.

**Per-task flow (while `RUNNING`):**

1. Claim one message from `ai_tasks`.
2. Publish a `launching` phase update (§5; `docs/contracts/task_progress.md` §4).
3. Read the message's `graph` discriminator field; initialize the corresponding compiled LangGraph (`environment` or `battle`) with the message body as initial state (per that graph's own Input State contract, `graphs/environment.md` §2 / `graphs/battle.md` §2).
4. Invoke the graph to completion. As execution crosses each graph's own internal phase boundaries, publish `composing` / `refining` / `finishing` phase updates — the mapping from internal nodes to these phases is defined once per graph in `docs/contracts/task_progress.md` §6.1, not re-derived here.
5. On success, publish the graph's output state as the `ai_tasks_results` message (correlated via the original message's `reply_to`) and acknowledge the `ai_tasks` message.
6. On unrecoverable failure (§9), publish a synthetic error result to `ai_tasks_results` instead, following the same "light crash" shape already established in `rabbitmq.md` §6/§9.

**Graph selection mechanism — undecided (flagged, §12):** whether `tasks.py` defines one generic Celery task that branches on the `graph` field, or one Celery task per graph bound to its own routing/queue, is not confirmed anywhere. `rabbitmq.md` only documents a single `ai_tasks` queue (not per-graph queues), which is the working assumption this doc uses, but the exact Celery-level dispatch mechanism is an implementation detail not yet decided.

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

- **Candidate `AI Worker`-owned metrics** (not yet formally wired to any storage): `tasks_processed_total`, `tasks_failed_total`, per-graph execution duration, and each graph's own quality signals (`attempts_used`, `forced_selection` — already proposed in `graphs/environment.md` §11 / `graphs/battle.md` §11 as candidates, never assigned a storage destination).
- **`tasks_in_queue`** (`template.md`'s own illustrative example for this service) is actually a RabbitMQ queue-depth metric, not something `AI Worker` measures about itself — ownership is already flagged as undecided in `rabbitmq.md` §8 (`Head` polling the management API? `AI Worker` self-reporting?). Not resolved here either.
- **No transport path exists today.** Unlike `Head` (which writes directly to Azure Table Storage, per `head.md` §8), `AI Worker` has no Azure dependency (§10) and no `metrics/ai_worker/*` Mosquitto topic is defined anywhere in `mosquitto.md`'s topic table. Concretely: even if the candidate metrics above were implemented, **there is currently no way for them to reach any persistent store.** This is a real gap, not just missing detail — flagged in §12.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Gemini API call fails/times out, or any LLM node returns structurally invalid output | Exception / schema validation error inside a node | Shared retry-with-backoff wrapper, budget `AI_WORKER_LLM_MAX_RETRIES` (§3, default `2`) — one unified budget for both failure shapes, per `ai_worker/nodes.md` §5. If exhausted, the task fails — see next row. |
| A graph's LLM retry budget is exhausted, or any other unhandled exception occurs during graph execution | Exception propagates out of the graph invocation (§6 step 4) | Synthetic error result published to `ai_tasks_results` (§6 step 6), the "light crash" pattern already established in `rabbitmq.md` §6/§9. The original `ai_tasks` message is still acknowledged — see next row for the open question this raises. |
| `AI Worker` process crashes mid-task (before ack) | Depends entirely on whether `ai_tasks` consumption uses manual or automatic RabbitMQ acknowledgment | **Not specified anywhere** — inherited open item from `rabbitmq.md` §9: whether the message gets redelivered to another `AI Worker` instance (manual ack + requeue) or is lost (auto-ack) is undecided. `architecture.md`'s stated intent ("ensures no tasks are lost if an AI Worker crashes mid-generation") implies manual ack, but this is not confirmed as an actual implementation decision. |
| `RabbitMQ` itself unreachable | Connection failure on consume or publish | **Not specified** — same standalone-outage gap already flagged in `rabbitmq.md` §9. Whether `AI Worker` retries its connection, crashes, or idles indefinitely is undecided. |
| `Mosquitto` unreachable | Publish failure on `progress/*`, `logs/*`, `status/*`, or subscribe failure on `control/*` | **Must never block or fail the task itself** (confirmed, per `docs/contracts/task_progress.md` §7) — log-and-continue for progress/log/heartbeat publishing. For `control/ai_worker/pause`/`resume` specifically: if `Mosquitto` is down exactly when `Head` needs to pause `AI Worker` for an update, there's no fallback — same unresolved gap `mosquitto.md` §9 already flags for `Bot`. |
| `AI Worker` container itself restarted (e.g. Docker restart policy) | N/A | No persistent state to recover (§6, stateless-between-tasks) — resumes consuming from `ai_tasks` immediately once reconnected. Any task it had claimed but not finished falls under the "process crashes mid-task" row above. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| `RabbitMQ` (local) | Claiming `ai_tasks`, publishing `ai_tasks_results` | Core dependency — see §9's open items on standalone RabbitMQ outages and crash-recovery semantics. |
| `Mosquitto` (local) | `control/ai_worker/*` signals, `progress/ai_worker/*` updates, logs, heartbeat | Best-effort only — task processing itself never blocks on Mosquitto (§9). |
| Google Gemini API | Every LLM-backed node, across both graphs | Required — failures handled via `AI_WORKER_LLM_MAX_RETRIES` (§3, §9); exhaustion fails the task. |
| `ai_worker/nodes.md`, `ai_worker/prompts.md` | Shared `Validator`/`Decider` code and every node's prompt content | Internal documentation dependency, not a runtime one — listed for completeness per this project's "link, don't duplicate" convention. |
| `graphs/environment.md`, `graphs/battle.md` | The two LangGraph graphs this container actually runs | Same as above. |
| `docs/contracts/task_progress.md`, `docs/contracts/localization.md` | Phase-update contract; `language_locale`'s meaning (arrives pre-resolved in task input, `AI Worker` never sources it itself) | Same as above. |
| **Not a dependency (explicit):** Azure (any resource) | — | Confirmed, `azure.md` §4 — all cloud I/O for AI tasks flows through `Bot`/`Head`, not `AI Worker` directly. |

---

## 11. Health Check

**Not formally specified beyond the existing Mosquitto heartbeat.** `AI Worker` publishes `status/ai_worker/heartbeat` (per `mosquitto.md` §4/§5), consumed by `Head` for basic liveness tracking — the same mechanism every service uses, not `AI Worker`-specific.

Unlike `Head`, `AI Worker` exposes no HTTP endpoint of its own (no `Launcher`-equivalent needs to poll it directly), so there is no `/health`-style check beyond that heartbeat. Whether a more detailed self-check (RabbitMQ connection state, Gemini API reachability) should be folded into the heartbeat payload, or exposed some other way, is undecided — flagged as an open item (§12).

---

## 12. Versioning & Update Behavior

- `AI Worker` shares the coordinated version tag with `Bot`, `Head`, and `Web` (per `Launcher.md` §12) — it does not version independently.
- **Update sequence participation:** during `Head`'s `DRAINING` → `UPDATING` transition (`head.md` §6), `Head` publishes `control/ai_worker/pause` at update-sequence start (`head.md` §5) — `AI Worker` stops claiming new `ai_tasks` messages immediately (§6, `PAUSED` state) while any task already in flight continues to completion, within `Head`'s own `HEAD_DRAIN_TIMEOUT_SEC` window. `Head` publishes `control/ai_worker/resume` once the update sequence concludes (or is abandoned).
- After `Launcher` recreates the container, a fresh `AI Worker` instance starts directly in `RUNNING` and resumes consuming — no state to restore (§6, §11).

---

## 13. Open Items / Future Work

*(Deviation from `template.md`'s 12-section schema, noted explicitly: this section is additive, mirroring the "Open Items" section already used in `graphs/*.md` and `nodes.md`, since this doc surfaced enough new gaps to warrant collecting them in one place rather than scattering them across §6/§8/§9/§11 only.)*

- ~~`GEMINI_API_KEY` as a single global required env var contradicted `/config`'s per-guild key/model selection~~ — **resolved this revision** (§1, §3, §4): credentials arrive per-task on the `ai_tasks` message, sourced from `contracts/guild_config.md`. Plaintext-in-transit/at-rest remains an accepted risk (§1), not solved by this fix.
- **No metrics transport path exists** (§8) — the single most concrete gap: even the metrics both graph docs already floated (`attempts_used`, `forced_selection`) have nowhere to actually go.
- The Celery-level task/graph dispatch mechanism (one generic task vs. one task per graph) is undecided (§6).
- RabbitMQ ack semantics (manual vs. automatic) — and therefore whether a mid-task crash loses or redelivers work — remain unresolved, inherited from `rabbitmq.md` §9 (§9).
- RabbitMQ broker credentials (username/password/vhost) remain undecided project-wide, not just for this service (§3).
- Whether a more detailed health signal (beyond the existing heartbeat) is worth adding is undecided (§11).
- Standalone RabbitMQ or Mosquitto outages (independent of any `Head`-internet-loss scenario) have no `AI Worker`-specific documented behavior beyond "the affected channel stops working" — same underlying gap already flagged in both `rabbitmq.md` §9 and `mosquitto.md` §9.
