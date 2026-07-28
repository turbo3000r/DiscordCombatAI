---
name: discord-combat-ai-head
description: Implements and changes the DiscordCombatAI Python Head coordinator, including fail-closed Mosquitto control, Blob Lease election, cluster Web PubSub, Bot fencing grants, drain/update orchestration, Launcher IPC client, release polling, log aggregation, and telemetry. Use when working under src/head/, Head Docker/Compose wiring, leadership state machines, or Head-side scenario coverage for S01–S09.
disable-model-invocation: true
---

# DiscordCombatAI Head

Use together with `discord-combat-ai-implementation`. This skill specializes the Head container; it does not replace the umbrella workflow.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read `docs/containers/head.md`.
3. Read the owning contracts for the slice: `leadership_control`, `drain_status`, `launcher_ipc`, `pubsub_live`, `telemetry`, and `log_archive` as applicable.
4. Read only the scenarios that the slice claims to cover (typically S01–S09).
5. Check `docs/to_resolve.md` for Phase 1 gates that affect source-tree location, S06 lease-renew policy, release selection, or P1.8 observability/readiness.
6. Inspect existing `src/head/`, Phase 0 shared models/clients, MQTT topic policies, and Compose Head wiring before adding parallel modules.

Do not invent grant TTLs, lease-failure outcomes, heartbeat schemas, buffer limits, or readiness depth when those decisions are unresolved. Stop and report the missing decision.

## Hard requirements

Require:

- fail-closed Mosquitto startup with confirmed retained `inactive` before election;
- Azure Blob Lease as the only leadership mutex;
- Web PubSub heartbeats as informational only;
- non-retained short-lived Bot activation grants;
- monotonic timers for grant and heartbeat expiry;
- no active grant without current lease authority and live local MQTT control;
- lease release only after commanding hard-stop during voluntary demotion/update;
- authenticated Launcher IPC with identical request-ID retries on unknown outcomes;
- leader-only telemetry upload and always-stream live dashboard payload while leader;
- deterministic async resource closure;
- no claim of strict at-most-one Gateway-connected Bot.

## Lifecycle and configuration

- Keep Head under the project `src/head/` package layout unless docs explicitly change that.
- Validate `HEAD_*` settings, cadence/TTL relationships, node identity, and application version source before running loops.
- Own MQTT, Azure clients, PubSub, HTTP server, and background loops under one process lifecycle that closes exactly once.
- Expose authenticated `GET /v1/health` as liveness only; never assert leadership or Bot readiness there.
- Publish Head IPC only on host loopback via Compose; mount the shared Launcher HMAC secret read-only.

## Mosquitto control

- Confirm retained `control/bot/desired_state = inactive` before claiming or issuing grants.
- If Mosquitto publish confirmation fails, remain non-authoritative.
- On reconnect, republish current retained desired modes from in-memory authority; never replay grant history.
- Feed MQTT callbacks into one serialized state-machine queue.
- Consume only documented topics/policies from shared messaging constants and models.

## Election and fencing

- States: `FOLLOWER`, `CLAIMING`, `LEADER`, plus later `DRAINING` / `UPDATING` for update orchestration.
- Heartbeat timeout triggers a lease acquire attempt, not automatic Bot activation.
- On successful lease acquisition, create a new opaque `leadership_term` UUID and start per-term `command_seq` at 1.
- Issue/renew non-retained grants only while lease ownership is confidently current.
- Soft-stop and hard-stop outcomes must follow `contracts/leadership_control.md` §5.4 and the resolved S06 policy.
- When this Head learns it is not leader, immediately command hard-stop and cease grants.
- Tests must validate documented best-effort fencing only; do not assert universal at-most-one Gateway connectivity.

## PubSub coordination

- Keep every Head permanently joined to the `cluster` group.
- Treat `leader_heartbeat` as liveness/observability only.
- Publish `update_available` with the canonical `target_version` field; never revive stale `version` naming.
- Keep the long-lived cluster client separate from the Phase 0 service-SDK `send_to_group` wrapper used for dashboard live telemetry.
- Never mix `cluster` and `dashboard-live` tokens or memberships.

## Drain and update orchestration

- Leader path: broadcast → retained draining + bounded draining grants → watch `status/bot/drain_progress` → zero workflows or drain timeout → retained stopped → bounded matching ack wait → lease release → Launcher `POST /v1/update`.
- Follower path: remain inactive and call local Launcher directly; no Bot drain.
- Deduplicate by `target_version`; queue a different target until the current cycle completes.
- `status/ai_worker/pause_ack` is diagnostic only and must never gate drain completion.
- Missing Bot stop acknowledgement is logged and may continue after the bounded wait; releasing the lease before the stopped command is forbidden.
- Use protocol simulators for Bot/AI Worker peers until Phase 2 exists; do not claim full S03/S07/S08 Discord/task acceptance.

## Launcher client

- Sign requests with the shared secret and exact body bytes.
- Use 2-second connect and 5-second response timeouts, at most three attempts.
- On unknown outcomes, retry the same request ID and body; refresh only timestamp/signature when skew requires it.
- Never retry `400`, `401`, `409`, or `422`.
- Do not reacquire leadership merely because Launcher delivery outcome is uncertain.

## Observability

- Aggregate local `logs/#`, parse with shared log-archive helpers, redact secrets, and flush leader-only to Blob on the documented cadence.
- Enforce documented in-memory buffer caps and drop-oldest behavior once P1.8 fixes them; do not invent limits.
- Sample CPU/RAM locally; sample Bot heartbeat fields only through the resolved heartbeat contract.
- Persist Table metrics and stream `telemetry_live` only while leader.
- Always stream to `dashboard-live` while leader; no listener detection.
- RabbitMQ event bridging republishes broker events into the standard log topics without leaking message payloads.

## Testing

Include focused tests for:

- fail-closed MQTT startup and reconnect without grant replay;
- lease race, conflict, renew failure, and lost lease;
- heartbeat timeout → claim → grant issuance ordering;
- PubSub-only, lease-only, combined, and Mosquitto failure matrices;
- drain zero-path, timeout escalation, wrong-term ack, and lease-release ordering;
- Launcher client retry/idempotency matrix;
- leader-only telemetry/log upload and demotion races;
- secret redaction and deterministic shutdown.

Use injected monotonic clocks. Prefer fakes for Azure and protocol peers; live Azure only behind explicit opt-in.

## Change boundaries

- Do not implement Bot Gateway lifecycle, Celery revoke, Discord hard-stop UI, AI Worker graphs, or Web APIs in Head.
- Do not hide long-lived cluster WebSocket membership inside Phase 0 service-only PubSub wrappers.
- Do not widen Azure roles, expose Head IPC publicly, or retain activation grants.
- Do not encode unresolved P1.8 heartbeat/buffer policy as permanent defaults.

## Completion criteria

A slice is complete only when:

- applicable Phase 1 gates for that slice are closed or explicitly out of scope;
- no grant path exists without confirmed MQTT safe-state and lease authority;
- failure/update ordering matches the owning contracts;
- focused Python tests and static checks pass;
- secrets and credentials remain redacted;
- scenario claims distinguish complete Head-side steps from deferred Bot/AI Worker steps;
- handoff names the next Head, Launcher, or Phase 2 transport slice.
