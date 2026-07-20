# S10 — Bot or AI Worker restart mid-task

**Id:** `S10`  
**Canonical:** `contracts/ai_task.md` §8–§9; `containers/bot/discord_bot.md` §6.3; `containers/ai_worker/ai_worker.md`; `contracts/task_progress.md`

## Preconditions

- Leader Bot has published an `ai_tasks` Celery task (`task_id` = Celery id = correlation id = domain idempotency key).
- AI Worker has claimed or is about to claim the task (prefetch = 1).
- Either Bot process restarts, or AI Worker process restarts, mid-flight.

## Ordered steps

### A — Bot restart mid-task

1. In-memory task map and `in_flight_workflows` are lost (`discord_bot.md` §6.3 / `drain_status.md` §7 — accepted limitation).
2. Bot cold-starts inactive until a fresh grant (S01 fencing on that node if Head still leader and re-grants).
3. Orphaned worker may still finish and publish `ai_tasks_results`. Bot **discards unknown `task_id`** (effectively-once outcome) — no Discord delivery for orphaned results unless a durable delivery reference exists (P1.1 open).
4. User-visible surface for the original lobby may be gone; user retries.

### B — AI Worker restart mid-task

1. Claimed Celery task is interrupted; after restart, worker does not resume mid-graph from memory.
2. At-least-once: broker may redeliver; worker may start a duplicate execution. Bot still correlates by `task_id` and applies cancel/timeout matrix if the original Bot session still tracks it.
3. Progress MQTT ticks may stop; Bot stall timeout can fire → Bot sole `revoke` actor (`ai_task.md` §8).
4. Malformed / unknown schema results → dead-letter, not infinite retry (`§9`).

## Durable writes

- RabbitMQ message durability per queue config; no graph checkpoint store in v1 docs.
- Battle archive / Discord messages only on Bot-owned success path (`contracts/battle_archive.md`) — not written on orphaned discard.

## Timeouts

- Stall / overall task timeouts on Bot while the task is still tracked.
- After Bot restart, those timers are gone with the map — user must retry (P1.1 persistence still open).

## User-visible result

- Discord: progress bar stops; timeout/error notice if Bot still owns the task; otherwise silent orphan + user retry.
- No double Discord final story for the same `task_id` when Bot still tracks it (effectively-once).

## Invariant checked

**At-least-once delivery, effectively-once outcome by `task_id`.** Restart never invents a second leadership story; Bot remains sole `revoke` author when cancellation is required.

## Phase 2 acceptance ownership

| Step | Phase 2 status | Notes |
|---|---|---|
| A1. Bot restart loses in-memory task map / timers | **complete** | |
| A2. Bot cold-starts inactive until fresh grant | **complete** | |
| A3. Orphaned `ai_tasks_results` discarded by unknown `task_id` | **complete** | **v1 limitation preserved** — no durable Discord delivery reconciliation |
| A4. User retries / lobby gone | **deferred** → command phases / P1.1 | Do not claim interaction recovery |
| B1–B2. AI Worker restart → redelivery / possible duplicate execution | **complete** | Transport shell |
| B3. Progress stops; stall timeout / revoke while Bot still tracks | **complete** | |
| B4. Malformed → DLQ | **complete** | |
