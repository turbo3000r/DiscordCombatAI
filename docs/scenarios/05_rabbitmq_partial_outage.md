# S05 — RabbitMQ partial outage

**Id:** `S05`  
**Canonical:** `contracts/ai_task.md`; `containers/rabbitmq.md` §8a/§9; `contracts/leadership_control.md` (Bot may stay Gateway-active); `contracts/drain_status.md`

## Preconditions

- Leader Bot is Gateway-active with a valid grant.
- Mosquitto and Azure coordination are healthy.
- RabbitMQ on the node becomes unavailable or publishes/consumes fail (broker down, auth, network partition to broker).

## Ordered steps

1. Bot fails to `apply_async` / publisher-confirm for new `ai_tasks` (or AI Worker fails to consume). **User-visible:** the AI-backed command step that attempted the publish returns a localized ephemeral/error response; **no** `TaskRecord` is created for that attempt (`rabbitmq.md` §9).
2. Leadership fencing is **unchanged**: Bot does not hard-stop solely because RabbitMQ is down — RabbitMQ is node-local work transport, not the activation authority (`architecture.md` / `ai_task.md` / `rabbitmq.md` §9).
3. Bot and AI Worker AMQP clients reconnect with exponential backoff (`rabbitmq.md` §8a: 1s → ×2 → cap 60s + jitter).
4. In-flight tasks already claimed by AI Worker may complete or fail locally; results cannot reach Bot until `ai_tasks_results` recovers. Bot’s stall/overall timeouts still apply (`ai_task.md` §8) and produce user-visible failure if the result never arrives.
5. On broker recovery, at-least-once delivery resumes; unknown/`schema_version` rejects go to dead-letter per contract — no reinvented retry policy here.
6. Drain progress: workflows waiting only on RabbitMQ still count in `in_flight_workflows` until terminal resolution or hard-stop (`drain_status.md` §1).

## Durable writes

- No new durable task envelopes while publish fails.
- Broker volume may retain unacked messages when the broker is back — effectively-once outcome via discard-by-unknown-`task_id` on Bot (`ai_task.md`).

## Timeouts

- `BOT_AI_TASK_STALL_TIMEOUT_SEC` / `BOT_AI_TASK_TIMEOUT_SEC` govern user-visible task failure when results never arrive.
- Leadership grant renewals continue independently.
- Publisher confirm wait is `5s` (`rabbitmq.md` §8a).

## User-visible result

- Discord: AI-backed flows fail at publish time (immediate) or time out (already in-flight); Gateway may remain connected.
- Non-AI commands (e.g. `/suggest` Cosmos write) can still succeed if Azure is up.

## Invariant checked

**RabbitMQ loss does not demote leadership or activate another Bot.** Work fails closed for AI tasks; fencing stays on lease + grant (`leadership_control.md`).
