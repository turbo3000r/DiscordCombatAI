# Contract: AI Task Request / Result (RabbitMQ)

> **New this revision — closes a real gap, not a formality.** Before this file existed, the exact JSON shape of `ai_tasks` and `ai_tasks_results` was scattered and incomplete: each graph doc defined its own Input/Output State (`graphs/environment.md` §2, `graphs/battle.md` §2), but the shared envelope every message needs regardless of graph — correlation, error shape, durability, ack semantics — was never written down in one place. `Bot` and `AI Worker` are implemented by reading different docs; without this contract they would each have to guess the wire format independently, and a single field-name mismatch (e.g. `error` vs `error_message`) breaks the pipeline at runtime with no schema to catch it first. Per the same "define once, link elsewhere" convention as `task_progress.md`, `guild_config.md`, and `localization.md`.

---

## 1. Purpose

Defines the complete wire contract for the two RabbitMQ queues that connect `Bot` and `AI Worker` (`architecture.md`, `rabbitmq.md`): `ai_tasks` (request) and `ai_tasks_results` (response). Both `Bot`'s publisher/consumer code and `AI Worker`'s consumer/publisher code must conform to this exactly — this doc is the single source of truth, not either service's own doc.

---

## 2. Transport & Broker Semantics

**Wire design resolved this revision (P0.4, owner-approved): native Celery task protocol, domain envelope nested as a single kwarg.** `Bot` is a real Celery client and `AI Worker` is a real Celery worker (`celery -A ai_worker worker -Q ai_tasks`), not a bare Kombu/AMQP JSON consumer — this was the previously-open choice between that option and a raw-AMQP-with-custom-consumer design.

| | |
|---|---|
| **Dispatch call** | `Bot` calls `ai_worker_tasks.run_graph.apply_async(kwargs={"envelope": envelope}, task_id=task_id, queue="ai_tasks")`. `envelope` is exactly §3's `AiTaskEnvelope` — today's flattened body — now nested one level under the single `envelope` kwarg instead of being the top-level Celery message body. Celery's own protocol supplies task headers (task id, task name, retries, etc.); this envelope carries domain data only, never duplicating what Celery's headers already provide. |
| **Queues** | `ai_tasks` (Bot → AI Worker, a real Celery queue), `ai_tasks_results` (AI Worker → Bot, a plain AMQP/Kombu queue — **not** Celery's result backend, see next row) — both **durable**, declared once at startup by whichever service connects first, both with a `x-dead-letter-exchange` pointing at the shared `dlx` (§9). |
| **Results transport — resolved this revision** | `ai_tasks_results` remains the existing custom queue. **Celery is used for task dispatch/cancellation only, not for results.** `AI Worker` explicitly publishes `AiTaskResultSuccess`/`AiTaskResultFailed` (§4, shape unchanged) to `ai_tasks_results` via a manual publish inside the task body, setting `correlation_id = task_id` manually. `AsyncResult.get()`/`.result`/any Celery result-backend read must never be used or assumed anywhere in this pipeline. |
| **Message persistence** | `delivery_mode=2` (persistent) on every publish to either queue — messages survive a RabbitMQ container restart. |
| **Content type** | `application/json`, UTF-8, for both the Celery-dispatched `ai_tasks` message and the manually-published `ai_tasks_results` message. |
| **Identifier unification — resolves former open item #7** | `task_id` (Bot-generated UUID, §3) = the Celery task id passed to `apply_async(task_id=...)` = the AMQP `correlation_id` on both publishes = the domain idempotency key §6's discard logic keys on. One identifier, four names, generated exactly once per task by `Bot`. |
| **Correlation** | `correlation_id` is set to `task_id` on both the `ai_tasks` Celery dispatch and `AI Worker`'s manual `ai_tasks_results` publish. |
| **`reply_to`** | Not used — resolves the P0.4 cleanup flag on whether it should remain. `ai_tasks_results` is one shared, node-local durable queue, not a per-Bot temporary queue; `Bot` is its consumer and filters by `correlation_id`. |
| **Publisher confirms — new this revision** | `confirm_delivery` (publisher confirms) is enabled on both publish paths: `Bot`'s `ai_tasks` dispatch and `AI Worker`'s `ai_tasks_results` publish. A nacked/unconfirmed publish is treated as **not sent**: on `Bot`'s side this triggers its existing retry/error-surfacing logic (same path as any other `ai_tasks` publish failure, `rabbitmq.md` §9); on `AI Worker`'s side the inbound `ai_tasks` message is deliberately **not acked yet**, so it naturally redelivers per the manual-ack ordering below — not a second, separate retry loop. |
| **Prefetch — new this revision, makes an existing implication explicit** | `AI Worker` runs with **prefetch = 1** on `ai_tasks` — one task at a time per worker process, matching the LangGraph single-execution model (`ai_worker.md` §6); previously only implied by `AI_WORKER_CELERY_CONCURRENCY=1`, now a stated wire-level guarantee. `Bot`'s prefetch on `ai_tasks_results` stays default/unbounded — handling a result is an O(1) task-map lookup (§6). |
| **Ack semantics on `ai_tasks` (Bot→Worker)** | `AI Worker` uses **manual ack**, acknowledging a message only *after* successfully publishing its corresponding `ai_tasks_results` message (success or synthetic error, §4) and receiving that publish's confirm. If `AI Worker` crashes after claiming a message but before ack, RabbitMQ redelivers that message to another consumer once the channel closes. |
| **Ack semantics on `ai_tasks_results` (Worker→Bot) — resolved this revision, was previously undefined** | `Bot` uses **auto-ack**. A discarded/duplicate/unmapped result is intentionally droppable per §6 — there is nothing worth protecting by delaying the ack. |
| **Delivery guarantee — corrected this revision** | **At-least-once delivery, effectively-once outcome** — not "exactly-once." Manual ack after result-publish (above) bounds duplicate *generation* to the crash window between claim and ack; §6's discard-by-task_id-absent-from-map logic is the dedup mechanism that makes the user-visible *outcome* effectively-once even though the transport itself is only at-least-once. Every "exactly-once" claim elsewhere in the docs (`architecture.md`, `rabbitmq.md` §1, and any other occurrence) is stale and has been corrected alongside this revision. |
| **Redelivery cost, accepted** | A redelivered task re-runs the full graph from scratch (no partial-state resume) — a crash mid-task can cost a duplicate LLM generation. Accepted for v1 given how rare a mid-task crash actually is; see §6 for how `Bot` avoids showing the user a duplicate result even though generation itself may run twice. |

---

## 3. `ai_tasks` Message (Bot → AI Worker)

**Dispatch call (§2):**

```python
ai_worker_tasks.run_graph.apply_async(
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

**Not a nested `payload` object inside `AiTaskEnvelope` itself, by design:** the graph-specific input fields are flattened directly alongside `task_id`/`graph`/`created_at`/`schema_version`, matching exactly how each graph doc's own `Input State` table (§2 in both `graphs/environment.md` and `graphs/battle.md`) is already written. The only nesting this revision introduces is the envelope's own position as a single `apply_async` kwarg (§2) — there is no second nesting level inside the envelope, and no second schema to keep in sync.

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
| **Overall timer** | `BOT_AI_TASK_TIMEOUT_SEC` | `900` (raised from the previous flat `300` — **default proposed, needs confirmation**, see §8) | Never — absolute cap from the moment `Bot` publishes the `ai_tasks` message | Total task duration exceeds the cap, regardless of how healthy the progress ticks looked — a hard ceiling against a task that's alive, ticking normally, but never converging (e.g. `refiner` loop pathologically re-triggering, or a graph bug). |

Whichever timer fires first, `Bot` treats the task as failed **locally**: synthesizes its own `AiTaskResultFailed` (§4) — `node: "bot_stall_timeout"` or `node: "bot_task_timeout"` respectively, with a `reason` describing which — removes the entry from its task map (`discord_bot.md` §6.3), and notifies the user, without waiting for RabbitMQ to ever deliver anything. The other timer is cancelled at the same time (the task is being given up on entirely, not per-timer).

This closes a gap no other doc addressed: without a client-side timeout, a task that's stuck because no `AI Worker` is running at all (not crashed — simply never started, so no heartbeat *or* phase tick ever arrives) would wait forever, since nothing on the broker side would ever produce a result to time out against. The stall timer additionally catches the case where a worker claimed the task and then died mid-execution — the overall timer alone (previous design) would still have waited the full 900s even though the specific failure was detectable within `BOT_AI_TASK_STALL_TIMEOUT_SEC`.

---

## 6. Idempotency & Duplicate Results

Given §2's accepted redelivery-can-duplicate-generation trade-off, `Bot` must not show the user two results for one `task_id`:

- If an `ai_tasks_results` message arrives for a `task_id` no longer in `Bot`'s local task map (already resolved, already timed out per §5 — by **either** timer, or from a `Bot` instance that has since restarted), `Bot` **silently discards it** — same convention `task_progress.md` §7 already uses for progress ticks with an unrecognized `task_id`, extended here to the terminal result.
- This is a discard, not an error — a duplicate arriving after the user already got their answer is an expected consequence of the redelivery model (§2), not a bug to alert on.
- **This is also the correctness backstop for a stall-timer false positive** (§5): if `Bot` gives up on `bot_stall_timeout` but the `AI Worker` handling that task was actually still alive and just failed to publish a heartbeat (e.g. a transient Mosquitto hiccup on the worker's side only), the eventual real `ai_tasks_results` message simply lands here and is discarded — the user already got a (premature) failure message, but nothing crashes or double-delivers. Accepted cost of the stall timer's existence, not a new failure mode to solve separately.

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
| User-initiated abort (future feature — mechanism documented now even though no command wires it to a UI action yet) | `revoke(task_id, terminate=True)`; remove the `task_id` from the local task map (`discord_bot.md` §6.3) | Yes | No — `Bot` already knows to stop caring about this `task_id`; there is no user surface left to notify. |
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

## 11. Open Items

- ~~Malformed-message handling policy~~ — **resolved this revision**, §9: nack-without-requeue to a shared `dead_letter` queue.
- ~~No dead-letter queue was configured~~ — **resolved this revision**, §9.
- ~~`AiTaskError.code`'s enum is not guaranteed exhaustive~~ — **resolved this revision**: replaced with the `node` field (§4), which doesn't need an exhaustive enum since it's just naming whichever LangGraph node actually ran (or a fixed, small set of pseudo-node values for non-node failures).
- ~~Hard-stop authorship (which service purges/terminates/synthesizes) was unclear~~ — **resolved this revision**, §2/§8: `Bot` publishes via `apply_async(task_id=task_id, queue="ai_tasks")`, making `task_id` a revocable Celery task id, and `Bot` itself is the actor for every cancellation-matrix row that calls `revoke` (§8) — no third actor is introduced.
- **`BOT_AI_TASK_TIMEOUT_SEC=900` is a proposed default, not yet confirmed** — raised from the previous `300` on the reasoning in §5 (multi-episode `battle` + a full `refiner` retry budget can legitimately run several minutes), but the actual worst-case latency depends on real Gemini response times this project hasn't measured yet. Revisit once real timing data exists; the two-timer split (§5) means this default being "a bit too generous" mainly costs user-visible latency on a truly-stuck task, not correctness, so erring high is the safer direction if unsure.
- **`AI_WORKER_PROGRESS_HEARTBEAT_SEC=30` (`contracts/task_progress.md` §3) is sized off `BOT_AI_TASK_STALL_TIMEOUT_SEC=120` at roughly a 1:4 ratio** — chosen to mirror the same margin pattern `head.md` §3 already uses between `HEAD_ELECTION_HEARTBEAT_SEC`/`HEAD_ELECTION_HEARTBEAT_TIMEOUT_SEC` (1:3), rounded slightly more generous here since a false-positive stall here throws away real Gemini spend, not just a cheap re-election. Not independently confirmed — flagged alongside the timeout default above since both were sized by inference, not measurement.
- Broker-side secret exposure (guild `api_key` traveling plaintext on `ai_tasks`) and RabbitMQ broker credentials are accepted/defined in `rabbitmq.md` §3/§6/§13 — cross-referenced here, not duplicated.
