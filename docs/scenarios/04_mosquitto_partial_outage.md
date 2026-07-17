# S04 — Mosquitto partial outage

**Id:** `S04`  
**Canonical:** `contracts/leadership_control.md` §5.4; `containers/mosquitto.md`; `contracts/drain_status.md` (progress is best-effort)

## Preconditions

- Leader Head holds the lease; Bot is active with a current grant.
- Mosquitto becomes unavailable or the Bot↔Head control connection drops, while Azure Blob Lease renewal and (optionally) PubSub may still work.

## Ordered steps

1. Bot detects loss of its control connection and **soft-stops immediately**: reject new AI work / drain-gated commands per existing drain gating; begin bounded failure drain (`leadership_control.md` §5.2 / §5.4).
2. Head, if it can still observe Bot, may publish retained `draining` and a bounded `draining` grant whose TTL does not exceed the remaining drain window — only when possible.
3. Grant expiry remains the hard-stop backstop if Mosquitto does not recover and fresh same-term `active` grants cannot be delivered.
4. Best-effort topics (`status/bot/drain_progress`, `progress/ai_worker/...`, logs) may be lost; they must not be treated as authority for leadership.

## Durable writes

- Leadership authority remains the Blob Lease (if Head can renew).
- No activation from stale retained messages after reconnect without a fresh non-retained grant (`§5.4` “Missing, malformed, stale…”).

## Timeouts

- Soft-stop → hard-stop bound converges with grant TTL / configured failure-drain timeout (defaults documented in `leadership_control.md` §3.2).
- Restoration requires renewed confidence in the same lease term **and** a fresh `active` grant; retained message alone cannot restore activity.

## User-visible result

- Discord: new slash / AI work rejected or soft-stopped; existing in-flight work may finish within the bound, then Gateway disconnect if unrecovered.
- Progress bars may stall (Mosquitto best-effort) without implying task success/failure by themselves — final outcomes use RabbitMQ results (`contracts/ai_task.md`).

## Invariant checked

**Control-plane loss soft-stops; grant expiry hard-stops.** Mosquitto outage never leaves Bot indefinitely active without renewals, and never invents leadership from PubSub alone.
