# S01 — Cold boot with no leader

**Id:** `S01`  
**Canonical:** `contracts/leadership_control.md` §3–§5; `architecture.md` Scenario 1; `containers/head.md`

## Preconditions

- Cluster nodes start (or restart) with no current Blob Lease holder.
- Local Mosquitto is reachable enough for Head to publish retained desired mode.
- Bot process may be up; it has no accepted activation grant.

## Ordered steps

1. Each Head, **before** election/lease acquisition, publishes retained `control/bot/desired_state = inactive` and confirms the MQTT publish (`leadership_control.md` §3.1). If it cannot confirm, it must not activate Bot.
2. Bot starts (or remains) Gateway-disconnected: no control connection and/or no current grant ⇒ inactive (`§5.1`).
3. Heads attempt to acquire the named Azure Blob Lease. Until one succeeds, no Head issues an `active` activation grant.
4. PubSub `leader_heartbeat` is absent or not authoritative; followers do not treat heartbeat as lease authority (`§4`).

## Durable writes

- Blob Lease: none held (or prior lease expired/released).
- Retained Mosquitto desired mode: `inactive` on each node that successfully published.

## Timeouts

- No grant TTL applies while inactive.
- Heartbeat timeout on followers is irrelevant until a leader exists.

## User-visible result

- Discord: Bot is offline / not connected on this node.
- No slash commands or AI work from this Bot instance.

## Invariant checked

**At-most-one Gateway activation without a live local grant:** Bot never connects solely from retained MQTT or PubSub heartbeat. Absent lease → no `active` grant → Bot stays inactive.
