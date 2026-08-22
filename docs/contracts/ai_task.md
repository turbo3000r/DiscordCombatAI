# Contract: AI Task Request / Result (RabbitMQ)

> **New this revision — closes a real gap, not a formality.** Before this file existed, the exact JSON shape of `ai_tasks` and `ai_tasks_results` was scattered and incomplete: each graph doc defined its own Input/Output State (`graphs/environment.md` §2, `graphs/battle.md` §2), but the shared envelope every message needs regardless of graph — correlation, error shape, durability, ack semantics — was never written down in one place. `Bot` and `AI Worker` are implemented by reading different docs; without this contract they would each have to guess the wire format independently, and a single field-name mismatch (e.g. `error` vs `error_message`) breaks the pipeline at runtime with no schema to catch it first. Per the same "define once, link elsewhere" convention as `task_progress.md`, `guild_config.md`, and `localization.md`.

---

## 1. Purpose

Defines the complete wire contract for the two RabbitMQ queues that connect `Bot` and `AI Worker` (`architecture.md`, `rabbitmq.md`): `ai_tasks` (request) and `ai_tasks_results` (response). Both `Bot`'s publisher/consumer code and `AI Worker`'s consumer/publisher code must conform to this exactly — this doc is the single source of truth, not either service's own doc.

---

## 2. Transport & Broker Semantics

**Wire design resolved this revision (P0.4, owner-approved): native Celery task protocol, domain envelope nested as a single kwarg.** `Bot` is a real Celery client and `AI Worker` is a real Celery worker (`celery -A ai_worker.celery_app worker -Q ai_tasks`), not a bare Kombu/AMQP JSON consumer — this was the previously-open choice between that option and a raw-AMQP-with-custom-consumer design.

| | |
|---|---|
| **Dispatch call** | `Bot` calls `celery_app.send_task("ai_worker.tasks.run_graph", kwargs={"envelope": envelope}, task_id=task_id, queue="ai_tasks")` (or the equivalent shared task-name constant). `envelope` is exactly §3's `AiTaskEnvelope`. Celery headers supply task id / name / retries; the envelope carries domain data only. |
| **Celery app / task identity** | Worker app import path: `ai_worker.celery_app:app`. Registered task name: `ai_worker.tasks.run_graph` (module `src/ai_worker/tasks.py`). Obsolete names such as `ai_worker_tasks.run_graph` or `celery -A ai_worker` without `celery_app` are non-canonical. |
| **Queues** | `ai_tasks` (Bot → AI Worker, a real Celery queue), `ai_tasks_results` (AI Worker → Bot, a plain AMQP/Kombu queue — **not** Celery's result backend) — both **durable**, with `x-dead-letter-exchange` → shared `dlx` (§9). **Topology owner:** `infra/rabbitmq/definitions.json`. Clients may verify/passive-declare; they must not create conflicting topology. |
| **Results transport** | `ai_tasks_results` remains the custom queue. **Celery is used for task dispatch/cancellation only, not for results.** `AI Worker` publishes `AiTaskResultSuccess`/`AiTaskResultFailed` (§4) manually with `correlation_id = task_id`. Never use `AsyncResult` / Celery result backends. |
| **Broker URL** | `amqp://{user}:{password}@{host}:{port}/{quote(RABBITMQ_DEFAULT_VHOST, safe="")}` — Compose injects the vhost into Bot and AI Worker. Default vhost `/discordcombatai` → path `/%2Fdiscordcombatai`. |
| **Serialization** | JSON only on Celery and on manual result publishes (`application/json`, UTF-8). |
| **Message persistence** | `delivery_mode=2` (persistent) on every publish to either queue. |
| **Identifier unification** | `task_id` (Bot-generated UUID, §3) = Celery task id = AMQP `correlation_id` = domain idempotency key. |
| **`reply_to`** | Not used. |
| **Publisher confirms** | Enabled on both publish paths; confirm wait 5s (`rabbitmq.md` §8a). Unconfirmed = not sent. |
| **Prefetch** | `AI Worker` prefetch = 1 (`worker_prefetch_multiplier=1`, concurrency 1). Bot’s `ai_tasks_results` prefetch stays default/unbounded. |
| **Late ack on `ai_tasks`** | `task_acks_late=True`. Ack only after confirmed `ai_tasks_results` publish. Crash before ack → redelivery. |
| **Ack on `ai_tasks_results`** | Bot **auto-ack**. Unknown/`task_id`-absent results are discarded (§6). |
| **Delivery guarantee** | **At-least-once delivery, effectively-once outcome** — not exactly-once. |
| **Revoke / purge** | Bot sole actor (§8). |
| **Redelivery cost, accepted** | Redelivered task re-runs from scratch (transport shell or real graph). |
| **Phase 2 transport shell** | Uses `graph="environment"` only — see §11. No `stub` graph discriminator. |

---

## 3. `ai_tasks` Message (Bot → AI Worker)

**Dispatch call (§2):**

```python
celery_app.send_task(
    "ai_worker.tasks.run_graph",
    kwargs={"envelope": envelope},
    task_id=task_id,      # also the AMQP correlation_id and the domain idempotency key, §2
    queue="ai_tasks",
)
```

`envelope`'s shape:

```python
class AiTaskEnvelope(TypedDict):
    schema_version: int   # = 1. A receiver rejects (nacks-to-DLQ, §9) an unknown schema_version
                           # rather than guessing at an unrecognized shape.
    task_id: str          # UUID, generated by Bot when it publishes — the same value used as
                           # correlation_id (§2), the Celery task id (§2), and the same task_id
                           # task_progress.md's progress ticks carry
    graph: Literal["environment", "battle"]
    created_at: str       # ISO 8601 UTC — when Bot published this message
    # --- everything below is the flattened Input State of whichever graph `graph` selects ---
    # graphs/environment.md §2/§5's EnvironmentGraphState input fields, OR
    # graphs/battle.md §2/§5's BattleGraphState input fields — both already include
    # guild_id, trace_id, api_key, model (per this revision's earlier credential-gap fix)
```

**Not a nested `payload` object inside `AiTaskEnvelope` itself, by design:** the graph-specific input fields are flattened directly alongside `task_id`/`graph`/`created_at`/`schema_version`, matching exactly how each graph doc's own `Input State` table (§2 in both `graphs/environment.md` and `graphs/battle.md`) is already written. The only nesting this revision introduces is the envelope's own position as a single `send_task`/`apply_async`-compatible kwarg (§2) — there is no second nesting level inside the envelope, and no second schema to keep in sync.

---

## 4. `ai_tasks_results` Message Schema (AI Worker → Bot)

A discriminated union on `status`:

```python
class AiTaskResultSuccess(TypedDict):
    schema_version: int    # = 1. Same reject-unknown-version rule as §3/§9.
    task_id: str
    graph: Literal["environment", "battle"]
    status: Literal["success"]
    result: dict           # the graph's own Output State (graphs/environment.md §2's
                            # final_environment/attempts_used/forced_selection, OR
                            # graphs/battle.md §2's story/winners/attempts_used/forced_selection)
    completed_at: str      # ISO 8601 UTC

class AiTaskResultFailed(TypedDict):
    """Renamed from AiTaskResultError this revision (project owner) — status value renamed
    "error" -> "failed" to match, and the nested AiTaskError/code enum is replaced by a flat
    node + reason shape. Rationale: a `code` enum trying to enumerate every possible failure
    (llm_retry_exhausted, invalid_input, ...) was mostly just re-deriving "which node failed" in
    disguise — attributing the failure to the actual LangGraph node that was executing is more
    precise and self-documenting than a growing catch-all enum, and needs no separate "unknown"
    escape hatch for ordinary graph failures."""
    schema_version: int    # = 1. Same reject-unknown-version rule as §3/§9.
    task_id: str
    graph: Literal["environment", "battle"]
    status: Literal["failed"]
    node: str        # The specific LangGraph node executing when the failure occurred, using the
                      # SAME names each graph's own §6 node table and §11 logging tag already use
                      # (e.g. "Enhancer", "Validator", "ImplementLastEpisode") — AI Worker is always
                      # the one who knows this and sets it directly from its own execution context.
                      #
                      # For failures NOT attributable to a single node (the graph never ran, or was
                      # killed from outside), use one of these reserved pseudo-node values instead —
                      # deliberately reusing this same field rather than adding a parallel `code`:
                      #   "invalid_input"      — pre-graph validation failure (e.g. environment.md §9's
                      #                          revision/initial input-shape mismatch), AI Worker-set
                      #   "worker_terminated"  — hard-stop kill (rabbitmq.md §6) — Bot-set, resolved
                      #                          §8's cancellation matrix (Bot is the sole actor)
                      #   "bot_stall_timeout"  — Bot gave up locally, no progress tick within
                      #                          BOT_AI_TASK_STALL_TIMEOUT_SEC (§5) — Bot-set
                      #   "bot_task_timeout"   — Bot gave up locally, overall BOT_AI_TASK_TIMEOUT_SEC
                      #                          exceeded regardless of ticks (§5) — Bot-set
    reason: str      # Human-readable, safe to show/log — MUST NOT include api_key or any other
                      # credential (ai_worker.md §7's logging rule applies here too, since this field
                      # can end up rendered directly in a Discord error message).
    completed_at: str # ISO 8601 UTC
```

**Every synthetic failure result uses `AiTaskResultFailed`** — this is the exact shape `rabbitmq.md` §6's hard-stop behavior and every graph's own §9 Failure Modes were previously describing only in prose ("a synthetic error result is added") without ever pinning down its fields. `worker_terminated`/`bot_stall_timeout`/`bot_task_timeout` are the three pseudo-node values `Bot` can use to render a distinct message (e.g. "generation was interrupted" vs. a generic failure) instead of a one-size-fits-all error.

---

## 5. Timeouts (Bot side)

**Redesigned this revision (project owner) into two independent timers, not one flat timeout.** A single `BOT_AI_TASK_TIMEOUT_SEC=300` couldn't distinguish "the task is dead/stuck" from "the task is alive and just legitimately slow" — a `battle` task with several episodes and a full `refiner` retry budget can genuinely run past 5 minutes while still being completely healthy. Two timers, checked independently:

| Timer | Env var | Default | Resets on | Fires when |
|---|---|---|---|---|
| **Stall timer** | `BOT_AI_TASK_STALL_TIMEOUT_SEC` | `120` | Every `progress/ai_worker/<task_id>` message received for this task — **including the new periodic heartbeat tick**, not only phase-change ticks (`contracts/task_progress.md` §3/§4, redesigned in this same revision specifically so this timer has something to reset against even mid-phase) | No progress message of any kind (phase-change or heartbeat) received within the window — the strongest available signal that the specific `AI Worker` instance handling this task has died or hung. |
| **Overall timer** | `BOT_AI_TASK_TIMEOUT_SEC` | `900` (confirmed) | Never — absolute cap from the moment `Bot` publishes the `ai_tasks` message | Total task duration exceeds the cap, regardless of how healthy the progress ticks looked — a hard ceiling against a task that's alive, ticking normally, but never converging (e.g. `refiner` loop pathologically re-triggering, or a graph bug). |

Whichever timer fires first, `Bot` treats the task as failed **locally**: synthesizes its own `AiTaskResultFailed` (§4) — `node: "bot_stall_timeout"` or `node: "bot_task_timeout"` respectively, with a `reason` describing which — removes the entry from its task map (`discord_bot.md` §6.3), and notifies the user, without waiting for RabbitMQ to ever deliver anything. The other timer is cancelled at the same time (the task is being given up on entirely, not per-timer).

This closes a gap no other doc addressed: without a client-side timeout, a task that's stuck because no `AI Worker` is running at all (not crashed — simply never started, so no heartbeat *or* phase tick ever arrives) would wait forever, since nothing on the broker side would ever produce a result to time out against. The stall timer additionally catches the case where a worker claimed the task and then died mid-execution — the overall timer alone (previous design) would still have waited the full 900s even though the specific failure was detectable within `BOT_AI_TASK_STALL_TIMEOUT_SEC`.

### 5a. Admission bound required by the timers

`Bot` permits **one outstanding AI task per Bot node** (queued or running). A `/quick-battle` workflow waits at most `QUICKBATTLE_AI_ADMISSION_TIMEOUT_SEC=60` for that slot; if it cannot acquire the slot, it terminates with a localized busy/timeout message and publishes nothing. The slot is released on accepted terminal result, local stall/overall timeout, user abort, publish failure, or hard-stop.

This serialization is load-bearing: the stall timer starts at publish time, while an unclaimed RabbitMQ task emits no worker heartbeat. Allowing multiple queued tasks could therefore trigger a false 120-second stall before a worker claimed the later task. Human-only lobby/collection/ballot phases do not hold the slot.

---

## 6. Idempotency & Duplicate Results

Given §2's accepted redelivery-can-duplicate-generation trade-off, `Bot` must not show the user two results for one `task_id`:

- If an `ai_tasks_results` message arrives for a `task_id` no longer in `Bot`'s local task map (already resolved, already timed out per §5 — by **either** timer, or from a `Bot` instance that has since restarted), `Bot` **silently discards it** — same convention `task_progress.md` §7 already uses for progress ticks with an unrecognized `task_id`, extended here to the terminal result.
- This is a discard, not an error — a duplicate arriving after the user already got their answer is an expected consequence of the redelivery model (§2), not a bug to alert on.
- **This is also the correctness backstop for a stall-timer false positive** (§5): if `Bot` gives up on `bot_stall_timeout` but the `AI Worker` handling that task was actually still alive and just failed to publish a heartbeat (e.g. a transient Mosquitto hiccup on the worker's side only), the eventual real `ai_tasks_results` message simply lands here and is discarded — the user already got a (premature) failure message, but nothing crashes or double-delivers. Accepted cost of the stall timer's existence, not a new failure mode to solve separately.

For a multi-task workflow such as `/quick-battle`, the owning session additionally records exactly one `expected_task_id`, expected `graph`, and environment revision number. A result is accepted only when all three match the session's current state. Replaced/superseded task IDs are removed from the task map before the next task becomes expected; their late results and progress are discarded by this section's normal unknown-ID rule.

The Bot-side task record stores a stable Discord delivery reference as IDs — `guild_id`, `channel_id`, optional `thread_id`, active `message_id`, and phase kind. An interaction token is never a delivery/recovery address. These IDs allow Bot-authenticated edits while the in-memory record exists; v1 does not persist task/session records across Bot restart (`bot/discord_bot.md` §6.3).

---

## 7. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `ai_tasks` publish fails (`Bot` side) | Exception from the AMQP client | Inherited, still-open gap from `rabbitmq.md` §9 (standalone RabbitMQ outage) — not resolved by this contract, which only defines message shape, not connection-failure behavior. |
| `AI Worker` crashes after ack but before actually finishing (impossible by construction) | N/A | Not reachable — §2's manual-ack ordering means ack only ever happens *after* a result is already published, so there is no window where an acked message has no corresponding result. |
| `AI Worker` crashes before ack | RabbitMQ consumer channel closes | Message redelivered per §2. Accepted duplicate-generation cost; duplicate result handled per §6. |
| Malformed message (missing required envelope field, or unknown `schema_version`) | Schema validation, on whichever side receives it | **Resolved this revision, see §9:** nack-without-requeue (dead-letter it). Never retry-forever against a malformed message. |

---

## 8. Cancellation Semantics Matrix

**Resolves this contract's former open item on cancellation for user abort/stall/overall/drain/hard-stop, and confirms hard-stop authorship** (previously the last item in §11): because `task_id` is now a real Celery task id (§2/§3), `Bot` — which already holds the Celery/AMQP client connection used to publish `ai_tasks` and consume `ai_tasks_results` — is the actor for every row below that calls `revoke`. No third actor is introduced.

| Cause | Bot action | `revoke`? | Synthetic result? |
|---|---|---|---|
| User-initiated abort | `revoke(task_id, terminate=True)`; remove the `task_id` from the local task map (`discord_bot.md` §6.3); acknowledge the abort directly on the owning Discord surface | Yes | No — direct Bot UI acknowledgement is authoritative; no synthetic worker result is needed. |
| Stall timeout (`BOT_AI_TASK_STALL_TIMEOUT_SEC`, §5) | Give up locally | **No** — the worker may legitimately still be alive; letting it finish is harmless since the eventual real result is discarded per §6 regardless | `AiTaskResultFailed`, `node: "bot_stall_timeout"` |
| Overall timeout (`BOT_AI_TASK_TIMEOUT_SEC`, §5) | Give up locally | **No**, same reasoning | `AiTaskResultFailed`, `node: "bot_task_timeout"` |
| Drain timeout (`contracts/drain_status.md`, P0.3) | Abandon and let `AI Worker`'s own pause/finish happen naturally | **No**, same reasoning — escalates into the hard-stop row below once drain timeout itself elapses | None at drain-timeout itself — see hard-stop row |
| Hard-stop (`control/bot/desired_state = stopped`, or autonomous grant/watchdog expiry, `contracts/leadership_control.md`) | Purge queued `ai_tasks`; `revoke(task_id, terminate=True)` for every in-flight claimed task | Yes | `AiTaskResultFailed`, `node: "worker_terminated"` — one per purged/terminated task |

**Deliberate choice, not an oversight:** only user-abort and hard-stop call `revoke`. All three timeout rows are "`Bot` gives up locally without touching the worker" — once `Bot` has already discarded the task, whether the worker finishes is irrelevant, and calling `revoke` there would be extra Celery traffic for no behavioral benefit.

---

## 9. Malformed/Poison Message Handling & Dead-Letter Queue

**Resolves this contract's former open items on malformed-message policy and the missing DLQ** (previously §7's last row and two bullets in §11):

- Both `ai_tasks` and `ai_tasks_results` are declared with `x-dead-letter-exchange` pointing at one shared `dlx` exchange, bound to a single `dead_letter` queue (declaration lives in `rabbitmq.md` §2/§6; this is the canonical policy).
- On schema validation failure — an `AiTaskEnvelope` (§3) or `ai_tasks_results` message (§4) that fails to parse, or carries an unknown `schema_version` — the receiving side **nacks without requeue**, dead-lettering the message. Never retry-forever against a malformed message.
- `Head`'s existing RabbitMQ event-exchange log bridge (`rabbitmq.md` §7, `head.md` §7) additionally surfaces a `logs/errors/rabbitmq` line whenever anything lands in `dead_letter`. No dedicated dead-letter consumer is required for v1 — queue depth (management UI/API) is the operational signal.

---

## 10. Schema Versioning

Every `AiTaskEnvelope` (§3) and every `ai_tasks_results` message (§4) carries `schema_version` (currently `1`). A receiver rejects/ignores an unknown `schema_version` — nack-without-requeue to the dead-letter queue per §9 — rather than guessing at a shape it doesn't recognize. This is the same uniform rule stated in `contracts/leadership_control.md` §7 and `contracts/launcher_ipc.md`, cross-referenced from `architecture.md`'s overview (P0.3).

---

## 11. Phase 2 transport shell (resolved)

Phase 2 proves the wire path without LangGraph/Gemini. Rules:

1. Keep `graph: "environment"` — **never** add a `stub` graph literal.
2. When `AI_WORKER_TRANSPORT_SHELL=true`, `ai_worker.tasks.run_graph` validates the envelope, publishes the mandatory progress sequence (`queued` is Bot-local; worker emits `launching` → `composing` → `refining` → `finishing`), then publishes this success result:

```json
{
  "schema_version": 1,
  "task_id": "<same as envelope>",
  "graph": "environment",
  "status": "success",
  "result": {
    "final_environment": {
      "description": "Phase 2 transport-shell canned environment.",
      "tags": ["phase2", "transport-shell"],
      "setting": "realistic"
    },
    "attempts_used": 0,
    "forced_selection": false
  },
  "completed_at": "<ISO 8601 UTC>"
}
```

3. The `result` object must validate against `graphs/environment.md` §2 Output State / shared `EnvironmentState` models. `attempts_used=0` is the transport-shell exception because no generated candidate or `AttemptRecord` exists; real graph runs define it as `len(attempts)` and therefore return `1..4`.
4. Trigger only from tests/acceptance harnesses that exercise Bot’s Celery dispatch + task map. No temporary Discord command or production endpoint.
5. Production default is `AI_WORKER_TRANSPORT_SHELL=false`. Real `environment`/`battle` graphs remain Phase 4 (P1.2).

## 12. Open Items

- ~~Malformed-message handling policy~~ — **resolved**, §9.
- ~~No dead-letter queue~~ — **resolved**, §9.
- ~~`AiTaskError.code` enum~~ — **resolved**, §4 `node` field.
- ~~Hard-stop authorship~~ — **resolved**, §2/§8.
- ~~Celery app import path / task name drift (`ai_worker_tasks` vs `ai_worker.tasks`)~~ — **resolved**, §2 / Phase 2.
- ~~Topology declare-on-connect ownership~~ — **resolved**: definitions.json is canonical (§2).
- **Timing defaults are confirmed:** worker graph deadlines are 600s (`environment`) / 840s (`battle`), progress heartbeat 30s, Bot stall timeout 120s, Bot overall timeout 900s, with one outstanding AI task per Bot node (§5/§5a; graph cost math in `graphs/battle.md` §8).
- Broker-side secret exposure accepted in `rabbitmq.md` §3/§6/§13.
