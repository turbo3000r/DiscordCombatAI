# S07 — Planned update happy path

**Id:** `S07`  
**Canonical:** `contracts/drain_status.md`; `contracts/leadership_control.md` §6; `contracts/launcher_ipc.md`; `architecture.md` Scenario 5

## Preconditions

- Leader Head detects a newer GitHub release (or receives `update_available` for a new `target_version`).
- Bot has zero or quickly finishing `in_flight_workflows`.
- Launcher is reachable via authenticated IPC (`launcher_ipc.md`).

## Ordered steps

1. Leader Head publishes `update_available` into the cluster PubSub group; every Head receives it (`architecture.md` Scenario 5). Dedup by `target_version` (`drain_status.md` §4).
2. **Leader path:** publish retained `draining`; issue only bounded `draining` grants; Bot sets `bot.draining = True`, rejects new drain-gated work, publishes `status/bot/drain_progress` while `in_flight_workflows > 0`.
3. Head watches drain progress; transitions `DRAINING` → `UPDATING` when `in_flight_workflows == 0` (before `HEAD_DRAIN_TIMEOUT_SEC`).
4. Head publishes retained `stopped`, stops grants, Bot hard-stops, Head waits boundedly for `gateway_connected: false` ack (`leadership_control.md` §6).
5. Head releases Blob Lease, then `POST /v1/update` to local Launcher (HMAC, idempotent by version, async admission per `launcher_ipc.md`).
6. **Follower path:** each follower Head signals its own Launcher (no Bot drain required when inactive).
7. Containers restart on the new version; cold boot / election (S01/S02) restores a leader.

## Durable writes

- Launcher version history / update admission dedup (`launcher_ipc.md`).
- Lease released before update proceeds past demotion step 5 (`leadership_control.md` §6 — releasing before hard-stop command is forbidden).

## Timeouts

- Drain completes on zero workflows (happy path) — `HEAD_DRAIN_TIMEOUT_SEC` not hit.
- IPC: 2s/5s timeouts and bounded jittered retries per `launcher_ipc.md`.
- Missing Bot ack: logged; update may continue after bounded wait (accepted overlap limitation).

## User-visible result

- Discord: brief Bot offline window during update; open lobbies should already be zero on happy path.
- Users retry commands after the new Bot activates.

## Invariant checked

**Drain means full user-workflow (`in_flight_workflows`), not empty RabbitMQ alone.** Lease is released only after commanding hard-stop; followers also update via the same broadcast.
