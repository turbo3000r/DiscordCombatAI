# S03 — Leader Head crash while Bot remains alive

**Id:** `S03`  
**Canonical:** `contracts/leadership_control.md` §5.3–§5.4; `containers/bot/discord_bot.md` §6.5

## Preconditions

- Leader Head holds the Blob Lease and has been renewing `active` grants to the local Bot.
- Bot is Gateway-connected with an accepted grant deadline.
- Head process crashes or disappears without publishing `stopped` / releasing the lease cleanly.

## Ordered steps

1. Grant renewals stop. Bot’s local monotonic grant deadline continues to count down (`leadership_control.md` §3.2).
2. Optional: Bot also detects loss of control connection / Head watchdog — soft-stop paths may begin earlier for Mosquitto loss (`§5.4` table); Head process disappearance is grant/watchdog driven.
3. At grant expiry (or explicit watchdog), Bot autonomously runs the hard-stop sequence (`discord_bot.md` §6.5): purge queued AI work, terminate in-flight worker execution as specified, notify affected Discord surfaces, disconnect Gateway.
4. Followers eventually miss heartbeats, race for the Blob Lease after it becomes available (Azure lease expiry), and a new leader activates per S02.

## Durable writes

- No new grants after Head death.
- Cosmos / Queue / RabbitMQ state for in-flight work is handled by hard-stop + `contracts/ai_task.md` cancellation matrix (Bot is sole `revoke` actor).
- Lease eventually expires at Azure; winner acquires it (S02).

## Timeouts

- Grant TTL default 45s (and aligned failure-drain bound) — hard-stop backstop.
- Planned-update 120s drain does **not** continue without a living authoritative Head issuing `draining` grants (`§3.2`).

## User-visible result

- Discord: Bot disconnects after autonomous hard-stop; in-flight workflows get termination notices where the hard-stop sequence requires them.
- After failover, a different node’s Bot may come online.

## Invariant checked

**Bot never stays Gateway-active on a dead Head:** grant/watchdog expiry forces hard-stop without waiting for a follower to steal the lease first. Retained MQTT alone cannot keep Bot active.

## Phase 2 acceptance ownership

| Step | Phase 2 status | Notes |
|---|---|---|
| 1. Grant renewals stop; monotonic deadline continues | **complete** | Bot grant watchdog |
| 2. Optional early soft-stop on control loss | **complete** | Overlaps S04; Bot-owned |
| 3. Autonomous hard-stop: purge/revoke tracked AI tasks, Gateway disconnect | **complete** | Discord thread notices only when a harness registered a delivery ref; no slash-command recovery claimed |
| 3a. Notify affected Discord command surfaces | **deferred** → `/quick-battle` / command phases | No user-facing commands in Phase 2 |
| 4. Followers race for lease (S02) | **integration-only** | Head/lease already Phase 1; Phase 2 may simulate Head disappearance |
