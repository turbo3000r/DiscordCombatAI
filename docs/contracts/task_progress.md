# Contract: AI Task Progress Updates

> **Why this lives in `contracts/`, not a single service doc:** both `AI Worker` (publisher, any graph) and `Bot` (consumer) must agree on the exact phase vocabulary and message shape below — per `docs/Readme.md`'s stated purpose for this folder, that agreement belongs here, not duplicated inside `ai_worker.md` and `discord_bot.md` separately.

## 1. Purpose

Defines a generic, graph-agnostic way for any `AI Worker` LangGraph graph to report best-effort, in-flight progress on an `ai_tasks` job, so `Bot` can render a live status bar in Discord while a `/quick-battle` (or any future AI-backed command) is generating.

This is **not** a replacement for the actual task result. It is supplementary telemetry only — see §5.

---

## 2. Why This Is Not On RabbitMQ

`architecture.md`'s Container Breakdown draws an explicit, load-bearing boundary:

> `RabbitMQ` — at-least-once, effectively-once task delegation requiring acknowledgment, retry, and single-consumer guarantees (corrected, P0.4 — see `contracts/ai_task.md` §2).
> `Mosquitto` — best-effort broadcast for logs, metrics signals, and coordination commands where occasional message loss is acceptable.

A progress tick is squarely the second category: if one is dropped, nothing breaks — the next tick, or the terminal `ai_tasks_results` message (still delivered reliably, per `rabbitmq.md`), makes up for it. Routing this over `ai_tasks_results` instead (e.g. via a `type: "progress" | "result"` discriminator) was considered and rejected — it would mean every consumer of what should be a clean, single-terminal-message-per-task RPC channel now has to filter out non-terminal noise.

---

## 3. Transport

| | |
|---|---|
| **Broker** | `Mosquitto` |
| **Topic pattern** | `progress/ai_worker/<task_id>` |
| **Publisher** | `AI Worker` for every phase except `queued`. **`Bot` creates the local `queued` phase** in its own task map / Discord status bar when it publishes to `ai_tasks` — it does **not** wait for Mosquitto for that first phase (an AI Worker cannot report a task still waiting unclaimed in RabbitMQ). |
| **Subscriber** | `Bot` — one static wildcard subscription, `progress/ai_worker/#` (same pattern `Head` already uses for `logs/#`, per `mosquitto.md` §6), filtering by `task_id` against the task map `Bot` already keeps locally (it created the task, so it already knows which Discord message/embed that `task_id` belongs to). Local `queued` updates do not require a Mosquitto publish. |
| **Routing to `Head`** | None — this topic bypasses `Head` entirely; `Head` has no reason to know about individual task phases. Deliberate exception to `Head`'s usual "sole aggregator" role, see `mosquitto.md` §6. |
| **QoS / retained messages** | **QoS 0**, not retained — resolved with P1.5 (`mosquitto.md` §6). Progress is best-effort; a stale tick has no value to a late subscriber. |
| **Publish cadence — expanded this revision (project owner)** | Two triggers, not one: (1) on every phase transition (original design, §4/§6); **and (2) periodically every `AI_WORKER_PROGRESS_HEARTBEAT_SEC` (default `30`, `ai_worker.md` §3) for the entire duration of the task, even while the phase hasn't changed.** This closes a real gap the phase-only design left open: `contracts/ai_task.md` §5 needs a **stall timer** on `Bot`'s side (has this specific task's `AI Worker` gone silent?) that resets on every progress message — but a phase can legitimately run for minutes with zero internal ticks under the original design (e.g. `battle`'s `refiner` loop retrying `Modifier` several times, or `storyteller` writing several episodes, neither of which changes the coarse phase). Without a heartbeat, `Bot`'s stall timer would trigger constantly on perfectly healthy tasks. The heartbeat tick is identical in shape to a phase-change tick (§4) — same `phase` value, just a fresh `timestamp` — so `Bot` doesn't need to distinguish "why" a tick arrived, only that one did. |

---

## 4. Message Schema

```json
{
  "schema_version": 1,
  "task_id": "string — the same task_id / correlation_id used for ai_tasks / ai_tasks_results, contracts/ai_task.md §2/§3",
  "graph": "environment | battle | ...",
  "phase": "queued | launching | composing | refining | finishing",
  "timestamp": "ISO 8601 UTC (required; naive/local times are rejected)",
  "attempt": "int | null — optional extra detail (e.g. current retry number while refining)"
}
```

**`schema_version` added this revision (P0.3):** same uniform rule as every other contract in the project — a receiver rejects/ignores an unknown `schema_version` rather than guessing at an unrecognized shape (`architecture.md`'s overview, `contracts/leadership_control.md` §7).

**Timestamps are UTC.** Every `timestamp` value is ISO 8601 UTC. Receivers reject naive or ambiguous local times.

See `contracts/ai_task.md` for the full `ai_tasks`/`ai_tasks_results` envelope this `task_id` is shared with — this doc only defines the progress-tick shape, not the request/result contract.

### Phase vocabulary (generic, graph-agnostic)

| Phase | Meaning | Typically emitted by |
|---|---|---|
| `queued` | Task sitting in `ai_tasks`, no worker has claimed it yet | **`Bot` locally** when it publishes the Celery task — not Mosquitto, not AI Worker |
| `launching` | A worker claimed the task; graph state is being initialized | AI Worker Celery task wrapper / graph entry, before the first node runs |
| `composing` | Producing the first candidate/draft | Graph-specific — see §6 |
| `refining` | Iteratively validating/fixing towards an acceptable result | Graph-specific — see §6 |
| `finishing` | Final selection/formatting before the terminal `ai_tasks_results` message is published | Graph-specific — includes any "pick the best of imperfect attempts" fallback step |

There is intentionally **no** `completed`/`failed` phase in this vocabulary — that distinction is what the real `ai_tasks_results` message is for (§5). This channel only ever describes "still working, here's roughly where."

---

## 5. Relationship to `ai_tasks_results`

This channel is purely supplementary. It never replaces, races with, or delays the terminal `ai_tasks_results` message — that message remains the sole source of truth for "the task is done," per `rabbitmq.md`. If every progress message for a task were lost, the only user-visible impact is a status bar that doesn't visibly tick — the final result is entirely unaffected.

---

## 6. Translation Layer Principle (why phases, not raw node names)

Internally, every graph node can still log at full granularity (e.g. `environment`'s `RouteInput`/`Generator`/`Validator`/`Enhancer`/`Decider`, per `environment.md` §11) — but only **phase changes** get published on this topic, mapped down to the fixed 5-value vocabulary above. Two deliberate reasons:

- **Decoupling:** the Discord-facing contract stays stable even as internal graph nodes are added, renamed, or reordered — only the graph's own phase-mapping table (§6.1 below) needs updating, never `Bot`.
- **Discord rate limits:** embed edits are rate-limited; publishing on every internal node transition (especially across up to 3 `refiner` retries) would multiply edits for no real UX benefit. Publishing only on phase change caps this at roughly 4–5 edits per task, trivially safe.

### 6.1 Per-graph phase mapping

| Graph | `composing` | `refining` | `finishing` |
|---|---|---|---|
| `environment` | `RouteInput`, `Generator`, `Normalise`, and the revision path's first `Enhancer` pass — i.e. the whole `composer` phase (`graphs/environment.md` §3) | The `Validator` ↔ `Enhancer` retry loop — the `refiner` phase's main loop (`graphs/environment.md` §3) | `Decider` fallback (only if reached) plus final output formatting (`graphs/environment.md` §7) |
| `battle` | The entire `planner` + `storyteller` phases combined — `Predefine` through `ImplementLastEpisode` (`graphs/battle.md` §3, §11) | The `Validator` ↔ `Modifier` retry loop (`graphs/battle.md` §3) | `Decider` fallback (only if reached) plus `ResolveWinners` (`graphs/battle.md` §3, §6) |

---

## 7. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `Mosquitto` unreachable when `AI Worker` tries to publish a phase update **or a heartbeat tick** | Publish failure | Log and continue — by design, this must **never** fail or block the task itself (per `mosquitto.md`'s general design boundary). The task proceeds normally; the Discord status bar simply stops updating until connectivity resumes. **New consequence of the heartbeat design (§3):** if this outage lasts longer than `BOT_AI_TASK_STALL_TIMEOUT_SEC` (`contracts/ai_task.md` §5), `Bot` will locally give up on every task this specific `AI Worker` is running, even though the worker itself is healthy and still generating — accepted cost, see `ai_task.md` §6's note on stall-timer false positives being safely discarded if a real result eventually arrives. |
| `Bot` misses a phase update or heartbeat tick (was offline, or the specific tick was dropped) | Not detectable — `Mosquitto` is fire-and-forget with no persistent sessions (per `mosquitto.md` §6) | Not corrected retroactively for the UI. For the stall timer specifically (`ai_task.md` §5), a single missed tick is tolerated by design — `AI_WORKER_PROGRESS_HEARTBEAT_SEC` is sized so several heartbeats fit inside one `BOT_AI_TASK_STALL_TIMEOUT_SEC` window, so one drop doesn't falsely trigger a stall. The next phase update, heartbeat, or the terminal `ai_tasks_results` message, naturally supersedes any stale UI state regardless. |
| A progress message's `task_id` doesn't match any task `Bot` is currently tracking (e.g. `Bot` restarted mid-task) | Local lookup miss on `Bot`'s side | Message is silently discarded — not an error. `Bot` has no state left to update it against. |

---

## 8. Open Items

- ~~Exact ISO 8601 timestamp/timezone convention~~ — **resolved:** UTC required (§4).
- ~~QoS / retention for progress ticks~~ — **resolved (P1.5):** QoS 0, not retained (`mosquitto.md` §6).
- ~~`queued` publisher~~ — **resolved:** Bot creates `queued` locally; AI Worker publishes from `launching` onward (§3/§4).
- ~~`battle` graph's phase mapping (§6.1) is undefined until `ai_worker/graphs/quick-battle.md` is written~~ — **stale, already resolved**: §6.1 above already defines `battle`'s mapping (`composing`/`refining`/`finishing`), matching `graphs/battle.md` §11. This bullet was left over from an earlier draft of this doc.
- Whether the optional `attempt` field (or any further per-phase detail) is actually needed by the Discord UI, or whether the phase name alone is sufficient, is undecided — included now as optional so adding/dropping it later isn't a breaking schema change.
- **`AI_WORKER_PROGRESS_HEARTBEAT_SEC=30`'s default is sized by inference (roughly 1:4 against `BOT_AI_TASK_STALL_TIMEOUT_SEC=120`), not measurement** — see `contracts/ai_task.md` §8 for the full reasoning and the explicit flag that this pair of defaults needs confirmation once real timing data exists.
