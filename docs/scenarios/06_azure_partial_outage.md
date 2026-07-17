# S06 — Azure coordination-plane partial outage

**Id:** `S06`  
**Canonical:** `contracts/leadership_control.md` §5.4; `contracts/pubsub_live.md`; `containers/azure.md` (Blob + Web PubSub)

## Preconditions

- Leader Head previously held the Blob Lease and was publishing PubSub heartbeats.
- Partial Azure failure affects **coordination** resources (Blob Lease and/or Web PubSub), not necessarily Cosmos/Queue/Table used by suggestions/telemetry.

## Ordered steps

### A — Web PubSub-only failure (Blob Lease renewal still confirmed)

1. Soft-stop immediately: no new AI work; bounded recovery drain (`leadership_control.md` §5.4).
2. Lease renewals may continue during bounded recovery.
3. Restore only with same-term confidence and a fresh `active` grant; otherwise hard-stop at timeout.
4. Followers miss heartbeats but **must still acquire the Blob Lease** before creating a new term (`§4`) — they do not activate on silence alone.

### B — Simultaneous loss of Blob Lease coordination and Web PubSub

1. Immediate hard-stop; **no** drain allowance (`§5.4`).
2. Bot disconnects Gateway; no new grants.
3. When Azure recovers, cold-boot / failover rules (S01/S02) apply — no activation from retained MQTT alone.

### C — Blob Lease renewal failure while PubSub still works (coordination loss)

1. Head that cannot confirm lease ownership ceases to be authoritative: command hard-stop and cease grants (`§5.4` “Local Head learns it is not leader” / loss of Azure coordination).
2. Heartbeats without lease authority must not keep followers from racing once the lease is free — heartbeat never authorizes Bot.

## Durable writes

- Lease state in Azure Blob is authoritative when reachable.
- No durable “promoted via PubSub” marker.

## Timeouts

- Soft-stop cases: grant / failure-drain bound → hard-stop.
- Simultaneous Blob+PubSub loss: immediate hard-stop (no drain window).

## User-visible result

- Discord: soft-stop rejects new work then disconnect; or immediate disconnect on total coordination loss.
- Dashboard live stream may stall (`pubsub_live.md`) independently of Bot fencing.

## Invariant checked

**Azure Blob Lease (plus local grants) fences Bot; PubSub is liveness/broadcast only.** Partial Azure outages follow the §5.4 matrix — never invent a third election mechanism.
