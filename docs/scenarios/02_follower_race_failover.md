# S02 — Follower race and successful failover

**Id:** `S02`  
**Canonical:** `contracts/leadership_control.md` §2–§5; `contracts/pubsub_live.md` (cluster group); `architecture.md` Scenario 1

## Preconditions

- Previous leader released the lease or lease expired (voluntary demotion, crash past grant expiry, or planned update lease release).
- At least two follower Heads are alive and joined to the cluster PubSub group.
- Each follower’s Bot is inactive (no grant).

## Ordered steps

1. Followers detect leader heartbeat timeout via local monotonic elapsed time since last receipt (`leadership_control.md` §4). Heartbeat fields are observability only.
2. Followers race to acquire the **same named Blob Lease**. Exactly one acquisition succeeds; losers remain non-authoritative.
3. Winner creates a new `leadership_term` (opaque UUID), publishes retained safe mode as required, establishes local Bot control, and issues a fresh non-retained `activation_grant` with `mode: active`, new `grant_id`, and `command_seq` starting at 1 for that term (`§3.2`).
4. Winner Bot accepts the new term only from its local Head over a live control connection, starts Gateway, and begins accepting work.
5. Winner begins publishing `leader_heartbeat` on PubSub; losers do not issue grants.

## Durable writes

- Blob Lease held by the winning Head (lease id / term as documented for Head).
- Mosquitto retained desired mode on the winner’s node updated per demotion/activation sequencing.
- No durable “Bot is leader” flag elsewhere — lease + grant are the authority chain.

## Timeouts

- Follower heartbeat timeout (local monotonic) triggers lease race, not automatic Bot activation.
- Grant TTL (default 45s) begins only after the winner issues grants; renewals continue while confidently leader.

## User-visible result

- Discord: previous Bot (if any) already hard-stopped or was never active; new leader Bot comes online after grant accept.
- Bounded dual-active overlap remains an accepted limitation (`§1`) if demotion acknowledgement was late — not a success criterion for this scenario’s happy path.

## Invariant checked

**Blob Lease is the mutual-exclusion primitive:** only the lease holder may create a new term and issue `active` grants. PubSub membership alone never elects a leader or activates Bot.
