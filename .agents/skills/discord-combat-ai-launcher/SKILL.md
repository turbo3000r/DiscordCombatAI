---
name: discord-combat-ai-launcher
description: Implements and changes the DiscordCombatAI Go Launcher host service, including authenticated Head↔Launcher IPC, durable update admission, Docker/Compose image pull and recreate, verification, rollback, CLI, and host-service packaging. Use when working on launcher/, launcher IPC endpoints, version history, GHCR pulls, Compose recreation, or Launcher CLI/daemon behavior.
disable-model-invocation: true
---

# DiscordCombatAI Launcher

Use together with `discord-combat-ai-implementation`. This skill specializes the host-side Go Launcher; it does not replace the umbrella workflow.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read `docs/containers/Launcher.md`.
3. Read `docs/contracts/launcher_ipc.md` as the canonical IPC, auth, idempotency, and health contract.
4. Read only the Head/scenario docs needed for the slice (`docs/containers/head.md`, S07–S09).
5. Check `docs/to_resolve.md` for Phase 1 gates that affect tag format, Compose tag injection, crash recovery, CLI/daemon concurrency, or verification readiness.
6. Inspect existing `launcher/` code, Phase 0 Python HMAC helpers (`src/shared/security/ipc_auth.py`), and golden fixtures before adding parallel crypto or schemas.

Do not invent release-tag grammar, Compose image-selection mechanics, crash-recovery semantics, or CLI locking when those decisions are unresolved. Stop and report the missing decision.

## Hard requirements

Require:

- Go host process outside Docker Compose;
- HMAC-SHA256 authentication on every IPC request in both directions;
- signatures over exact received body bytes;
- durable request/operation/version persistence before successful admission responses;
- idempotent `/v1/update` admission;
- all-or-nothing image pull before recreate;
- authenticated Head liveness verification after recreate;
- automatic rollback exactly once on verification exhaustion;
- halt without flapping if rollback fails;
- no Azure, RabbitMQ, or Mosquitto dependencies in Launcher;
- secret redaction in logs and error bodies;
- CLI and daemon share one admission coordinator.

## IPC authentication

- Match `contracts/launcher_ipc.md` and the Phase 0 Python golden vectors byte-for-byte.
- Canonical bytes: uppercase method, path-with-query, timestamp, request ID, lowercase hex SHA-256 of exact body bytes.
- Reject clock skew outside ±30 seconds.
- Retain accepted `(request_id, signature)` replay entries for the documented window.
- Cap request bodies at 16 KiB.
- Never parse then reserialize JSON before hashing or verifying.
- Return generic `401` on auth/skew/replay-integrity failure; never log the secret or full signature.

## Admission and durable state

- Implement `POST /v1/update` and `GET /v1/status` exactly as documented.
- Same `request_id` returns the stored response and never starts a second operation.
- Same target while busy returns `202` with the current operation ID.
- Conflicting target returns non-retryable `409 update_busy`.
- Persist accepted/completed request IDs and the active operation before returning success.
- Retain dedup records at least 24 hours across process restart.
- Use serialized writes and atomic replace for the state file; handle truncation/corruption without silent success.
- Apply the canonical crash-recovery decision once documented; do not invent resume-vs-idle behavior.

## Docker and Compose update engine

- Wait for Docker daemon availability with documented retry bounds before pull/recreate.
- Construct a coordinated local application image set from one validated `target_version`.
- Authenticate to GHCR with the dedicated token; never log the token.
- Pull every required image before any recreate; abort on partial pull without touching containers.
- Recreate through the documented Compose/Engine mechanism using argument arrays, controlled environment, and fixed working directory. No shell string expansion.
- Reject invalid tags and shell fragments at admission/validation time.
- Never recreate a mixed-version application set.

## Verification and rollback

- Poll authenticated Head `GET /v1/health` with 2-second connect and 5-second response timeouts.
- Accept only a valid `200` with `status: alive` for the target version within the verification window.
- Treat `401`, malformed payload, `503 initializing`, timeout, and connection failure as unsuccessful polls.
- Commit version history only after verification success.
- On verification exhaustion, roll back to the previous known-good tag exactly once.
- If rollback fails, halt and require manual intervention; do not flap between broken versions.
- Keep verification at the readiness depth documented for P1.8; do not invent deeper readiness checks.

## CLI and host packaging

- Provide daemon mode plus `update --version`, `rollback`, and `status`.
- CLI must use the same admission/locking rules as HTTP; never run a second independent update engine.
- Package systemd and Windows service wrappers with auto-restart as documented.
- Document and respect host firewall restrictions for the Docker-network-only bind on port 9700.
- Rotate local logs only; Launcher does not ship logs to Azure or Mosquitto.

## Testing

Include focused tests for:

- cross-language HMAC golden vectors;
- `200/202/400/401/409/422` admission matrix;
- duplicate request and restart dedup;
- concurrent same-target and conflicting-target admission;
- corrupt/truncated state files;
- unavailable Docker, auth failure, and partial image pull;
- mixed-tag prevention and argument safety;
- health verification success, timeout, and rollback halt;
- CLI/daemon concurrency and secret-safe output.

Prefer hermetic fakes for Docker/Compose/Head where practical. Do not require live Azure.

## Change boundaries

- Do not implement Head election, Bot fencing, telemetry, or broker clients in Launcher.
- Do not put Launcher business logic into shared Python packages beyond IPC models and HMAC fixtures.
- Do not weaken authentication, persistence, or all-or-nothing pull barriers for convenience.
- Do not auto-update the Launcher binary itself.

## Completion criteria

A slice is complete only when:

- applicable Phase 1 documentation gates for that slice are closed or explicitly out of scope;
- HMAC, admission, persistence, pull/recreate, verification, or packaging behavior matches the owning docs;
- focused Go tests pass, including race-sensitive paths where concurrency is involved;
- secrets remain redacted;
- no undocumented public behavior was introduced;
- handoff names the next Launcher or Head integration slice.
