# S09 — Planned update rollback

**Id:** `S09`  
**Canonical:** `contracts/launcher_ipc.md`; `containers/Launcher.md`; `architecture.md` Scenario 5 (manual CLI); `contracts/drain_status.md` §4

## Preconditions

- An update was admitted (automatic `/v1/update` or `launcher update --version`).
- Either the new version fails exact-version liveness verification, or an operator separately decides to revert a verified/current deployment.
- Launcher has a verified `current_version`; automatic update admission captures it as this operation's `rollback_version`. Manual rollback additionally requires version history's `previous_version`.

## Ordered steps

### A — Automatic rollback after failed verification

1. After recreate, Launcher polls authenticated Head `/v1/health`. Success requires schema-valid `200`, `status: "alive"`, and exact `version == target_version`; dependency readiness is not checked (`launcher_ipc.md` §5).
2. If the verification window expires, Launcher automatically attempts rollback to the operation's captured pre-update `rollback_version` **once**. It pulls the fixed local image set and recreates it through the same coordinator/lock and controlled Compose path.
3. Launcher verifies authenticated liveness at that exact rollback version. Success records the rollback result, leaves the pre-operation current/previous history unchanged, and returns to `IDLE`. Failure stops automatic recovery and remains operator-visible; it does not recursively rollback or flap.

### B — Separate manual rollback

4. An operator may run `launcher rollback` to target version history's `previous_version`, either to revert a currently deployed/verified version or recover after a stopped/interrupted operation. This is a new manual admission, not the automatic attempt from A and not a continuation of an old operation.
5. The manual command bypasses GitHub poll / `update_available` initiation but shares the same coordinator, persisted state, and cross-process operation lock as HTTP. Busy/conflicting work is rejected; no concurrent recreate is started.

### Common completion/restart behavior

6. No requirement exists for every peer node to rollback simultaneously — each host's Launcher is independent.
7. On boot, Heads publish retained `inactive` before election (S01); a leader re-acquires the lease (S02).
8. If Launcher itself restarts while pull/recreate/verify/rollback was active, it marks that operation `INTERRUPTED`, exposes the last durable phase, and performs no automatic resume. Explicit reconciliation or a new update/rollback admission is required.
9. If a different `target_version` broadcast arrives mid-cycle, Head queues it until the current cycle completes (`drain_status.md` §4); manual rollback remains a separate Launcher admission.

## Durable writes

- Launcher version history records current + previous tags.
- IPC idempotency, interrupted-operation status, and busy behavior apply if automatic/manual initiators race (`launcher_ipc.md`).

## Timeouts

- Health verification uses the documented 2s connect / 5s response polls within `LAUNCHER_HEALTHCHECK_TIMEOUT_SEC`.
- One automatic rollback attempt is the complete automatic recovery budget.
- Grant/lease rules unchanged after restart.

## User-visible result

- Discord: Bot offline during rollback window; returns on previous version after election.
- Users may need to retry interrupted battles (in-memory state lost — accepted).

## Invariant checked

**Rollback is host-local Launcher authority + normal leadership fencing on restart** — no special “rollback grant.” Cluster does not skip Blob Lease election after version change.
