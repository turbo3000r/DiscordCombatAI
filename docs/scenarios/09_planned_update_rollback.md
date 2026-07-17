# S09 — Planned update rollback

**Id:** `S09`  
**Canonical:** `contracts/launcher_ipc.md`; `containers/Launcher.md`; `architecture.md` Scenario 5 (manual CLI); `contracts/drain_status.md` §4

## Preconditions

- An update was attempted (automatic `/v1/update` or `launcher update --version`).
- New version fails verification / health, or operator decides to revert.
- Launcher retains previous version tag in version history (Launcher tree: `version_history.go` / rollback CLI per `architecture.md`).

## Ordered steps

1. Automatic path: after compose recreate, Launcher verification polls Head health (`launcher_ipc.md` `/v1/health` is authenticated liveness-only). Failure is operator-visible via Launcher status/logs — exact readiness depth remains P1.8.
2. Operator runs `launcher rollback` (or equivalent documented CLI) on the affected host — **bypasses** GitHub poll / `update_available` broadcast initiation (`architecture.md` Scenario 5 step 9 pattern for manual control).
3. Rollback applies the previous version tag; containers recreate again. No requirement that every peer node rollback simultaneously — each host’s Launcher is independent (followers update independently in S07).
4. On boot, Heads publish retained `inactive` before election (S01); a leader re-acquires the lease (S02).
5. If a different `target_version` broadcast arrives mid-cycle, it is queued until the current cycle completes (`drain_status.md` §4) — rollback CLI is unaffected by broadcast dedup (manual path already idempotent via IPC).

## Durable writes

- Launcher version history records current + previous tags.
- IPC idempotency / busy behavior for `/v1/update` still applies if a concurrent automatic update races — reject or admit per `launcher_ipc.md`.

## Timeouts

- Health verify polling bounds are Launcher implementation / P1.8 — scenario requires that a failed verify does not leave Bot active under a dead/unverified Head without S01 fencing.
- Grant/lease rules unchanged after restart.

## User-visible result

- Discord: Bot offline during rollback window; returns on previous version after election.
- Users may need to retry interrupted battles (in-memory state lost — accepted).

## Invariant checked

**Rollback is host-local Launcher authority + normal leadership fencing on restart** — no special “rollback grant.” Cluster does not skip Blob Lease election after version change.
