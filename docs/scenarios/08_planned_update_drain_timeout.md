# S08 — Planned update drain timeout

**Id:** `S08`  
**Canonical:** `contracts/drain_status.md` §2; `contracts/leadership_control.md` §5.3 / §6; `contracts/ai_task.md` §8; `bot/discord_bot.md` §6.5

## Preconditions

- Planned update started as in S07.
- At least one workflow remains in `in_flight_workflows` (open `/quick-battle` lobby/collector/vote and/or open `ai_tasks` map entry) when `HEAD_DRAIN_TIMEOUT_SEC` elapses.

## Ordered steps

1. Head remains in `DRAINING`, receiving `status/bot/drain_progress` with `in_flight_workflows > 0` (or missing ticks — does not early-escalate; waits for timeout or explicit zero — `drain_status.md` §6).
2. `HEAD_DRAIN_TIMEOUT_SEC` elapses → Head escalates to the **hard-stop sequence**, not silent abandonment (`drain_status.md` §2).
3. Hard-stop: purge queued AI work, terminate in-flight worker execution, cancel open lobby/collector/vote views with localized “update in progress, please retry” notice, disconnect Gateway (`discord_bot.md` §6.5 / drain_status §2).
4. `revoke` occurs as part of hard-stop, not at drain-timeout itself (`ai_task.md` §8; `drain_status.md` §2).
5. Head continues demotion: `stopped`, bounded ack wait, lease release, Launcher `/v1/update` (S07 steps 4–5).

## Durable writes

- Same as S07 for lease release + Launcher admission.
- Task map / lobby state is in-memory and expires on restart; hard-stop actively cancels tracked surfaces before disconnect (`drain_status.md` §7, `quick-battle.md` §8.1).

## Timeouts

- `HEAD_DRAIN_TIMEOUT_SEC` is the escalation trigger.
- Grant TTL remains a Bot-side backstop if Head coordination fails mid-drain.

## User-visible result

- Discord: open Quick Battle UI cancelled with update/retry notice; AI tasks terminated with user-visible failure where hard-stop requires it.
- Bot goes offline; update proceeds.

## Invariant checked

**Drain timeout always escalates to hard-stop** — never “abandon work and leave Gateway up,” and never treats `pause_ack` as a second gate (`drain_status.md` §3).

## Phase 2 acceptance ownership

| Step | Phase 2 status | Notes |
|---|---|---|
| 1. Drain progress with `in_flight_workflows > 0` | **complete** for AI-task entries |
| 2. `HEAD_DRAIN_TIMEOUT_SEC` → hard-stop escalation | **integration-only** Head timer; Bot hard-stop execution **complete** |
| 3. Purge + revoke + Gateway disconnect | **complete** | |
| 3a. Cancel lobby/collector/vote with localized notice | **deferred** → Phase 5 `/quick-battle` | Behavior is specified; Phase 2 has no lobbies |
| 4. `revoke` only in hard-stop, not at drain-timeout itself | **complete** | |
| 5. Demotion / Launcher continue | **integration-only** | Phase 1 |
