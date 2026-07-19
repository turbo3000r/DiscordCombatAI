# Service: Launcher

## 1. Responsibility

`Launcher` is a standalone host process (not a Docker container) responsible for pulling coordinated application images and recreating the local application containers during version updates. It is the only component in the system capable of recreating the Compose application set, which is why it must live entirely outside it.

`Launcher` is the foundation of the system's "always running" guarantee: from the outside, the node behaves like a single persistent process — but it is not one process, it is a coordinated set of containers that `Launcher` brings up, monitors at the surface level (via `Head`'s health signal), and recreates on update. `Launcher` itself has no cluster-awareness, no Azure access, and no knowledge of leadership state — it only knows the fixed local application image set (`head`, `bot`, `ai_worker`), how to recreate those services, and whether the resulting `Head` is alive at the exact requested version.

It also exposes a manual CLI for administrator-triggered updates, rollbacks, and status checks.

---

## 2. File Structure

```
launcher/
├── go.mod
├── go.sum
├── main.go                       # Entry point: parses CLI args, starts daemon mode if no subcommand given
├── cmd/
│   ├── update.go                  # `launcher update --version vX.Y.Z`
│   ├── rollback.go                # `launcher rollback`
│   └── status.go                  # `launcher status`
├── internal/
│   ├── coordinator/
│   │   └── coordinator.go         # One admission/state machine used by HTTP and every mutating CLI command
│   ├── ipc/
│   │   └── listener.go            # Authenticated host TCP listener reachable from Head container
│   ├── docker/
│   │   ├── ping.go                 # Checks Docker daemon availability before any operation
│   │   ├── pull.go                 # Docker Engine API: image pull
│   │   └── auth.go                 # GHCR registry auth for API pulls
│   ├── compose/
│   │   └── recreate.go             # Controlled docker compose CLI invocation; no shell interpolation
│   ├── healthcheck/
│   │   └── verify.go               # Polls Head's health endpoint post-update
│   └── state/
│       ├── version_history.go      # Local-only: tracks current + previous version tags for rollback
│       └── process_lock.go         # Host-wide cross-process lock shared by daemon and CLI
├── install/
│   ├── launcher.service            # systemd unit file (Linux) — includes Restart=always
│   └── launcher.exe.config         # Windows Service wrapper config — includes auto-restart on failure
└── Dockerfile.build                # Multi-stage build used only by CI to cross-compile the binary
```

---

## 3. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LAUNCHER_IPC_BIND` | No | `0.0.0.0` | Bind required for Head-container reachability. Host firewall must allow port 9700 only from Docker's bridge/private network. |
| `LAUNCHER_IPC_PORT` | No | `9700` | Host-reachable HTTP port used by `Head`; not loopback-only. |
| `LAUNCHER_IPC_SECRET_FILE` | Yes | — | Host path to shared random HMAC secret (32 bytes minimum), also mounted read-only into Head. |
| `HEAD_IPC_HOST_PORT` | No | `9800` | Host-loopback port mapped to Head container port 9800 for liveness polling. |
| `LAUNCHER_COMPOSE_FILE` | No | `docker-compose.yml` | Path to the Compose file this node manages. |
| `LAUNCHER_COMPOSE_PROJECT` | No | `discordcombatai` | Fixed Compose project name passed to the controlled CLI invocation. |
| `LAUNCHER_GHCR_NAMESPACE` | Yes | — | GitHub Container Registry namespace (e.g. `ghcr.io/youruser/discord-combat-ai`). |
| `LAUNCHER_GHCR_TOKEN` | Yes | — | Dedicated read-only token for pulling images from GHCR. Used directly via the Docker Engine API's registry auth, independent of any host-level `docker login` session. Required because the host machine running `Launcher` may not have an authenticated Docker session of its own. |
| `LAUNCHER_DOCKER_RETRY_INTERVAL_SEC` | No | `15` | How often to re-check Docker daemon availability while waiting for it to come online. |
| `LAUNCHER_DOCKER_RETRY_MAX_SEC` | No | `0` (infinite) | Maximum time to keep retrying daemon availability before giving up on a triggered update. `0` means retry indefinitely. |
| `LAUNCHER_HEALTHCHECK_TIMEOUT_SEC` | No | `300` | How long to wait for the new `Head` to respond before triggering rollback. |
| `LAUNCHER_HEALTHCHECK_INTERVAL_SEC` | No | `10` | Polling interval during the verification window. |
| `LAUNCHER_VERSION_HISTORY_PATH` | No | `./launcher_state.json` | Local file tracking current/previous tags and operations. Relative paths are resolved against the Launcher executable/install directory, not the caller's current directory. Local-only, never synced to Azure. |
| `LAUNCHER_LOCK_PATH` | No | `./launcher.lock` | Host-wide advisory lock file used by the daemon HTTP path and separate CLI processes. Relative paths use the same executable/install-directory base as state, ensuring service and CLI resolve one lock. |
| `LAUNCHER_LOG_PATH` | No | `./logs/launcher.log` | Local log file. `Launcher` writes here only — no centralized log collection. |

---

## 4. Inbound Communication

| Source | Channel | Format | Trigger |
|---|---|---|---|
| `Head` (local container) | Authenticated HTTP, `POST /v1/update` via `host.docker.internal:${LAUNCHER_IPC_PORT}` | Versioned idempotent request in `contracts/launcher_ipc.md` §4 | After Head has hard-stopped Bot, waited boundedly for ack, and released its lease |
| Administrator | CLI (`launcher update --version vX.Y.Z`) | CLI args | Manual trigger |
| Administrator | CLI (`launcher rollback`) | CLI args | Manual rollback request |
| Administrator | CLI (`launcher status`) | CLI args | Manual status query |

> **Transport boundary:** `Launcher` does not subscribe to Mosquitto. Head → Launcher is host-reachable TCP, not loopback: Windows Docker Desktop supplies `host.docker.internal`; Linux Compose adds `host.docker.internal:host-gateway`. HMAC authentication plus a host firewall restricted to Docker's private network protects the `0.0.0.0:9700` bind. Full configuration is canonical in `contracts/launcher_ipc.md` §1–§3.

---

## 5. Outbound Communication

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| Docker Engine (local) | Go Docker Engine API client (Unix socket / named pipe) | Daemon ping and authenticated image pulls only | Before and during update/rollback execution |
| GHCR | HTTPS, authenticated with `LAUNCHER_GHCR_TOKEN` | Docker image pull | On update or rollback execution |
| Docker Compose (local CLI) | Direct child process, fixed executable/argument vector (never through a shell) | Recreate only: `docker compose --project-name <configured> --file <configured> up -d --force-recreate --no-build --no-deps head bot ai_worker` with child-only `APPLICATION_VERSION=<target>` | After all fixed image pulls succeed |
| `Head` (new container) | Authenticated HTTP, `GET http://127.0.0.1:${HEAD_IPC_HOST_PORT}/v1/health` | Liveness schema in `contracts/launcher_ipc.md` §5 | During verification window |
| Local log file | Filesystem | Plain text (see Section 7) | Continuous |

`Launcher` has no outbound communication to Azure, RabbitMQ, or Mosquitto, by design. If centralized visibility of node versions is ever needed, that responsibility belongs to `Head` — `Head` can read `Launcher`'s local `/status` endpoint and publish version info to Azure itself, keeping `Launcher` fully isolated from cloud dependencies.

---

## 6. Internal Logic / State Machine

```
IDLE
  └─ accepted HTTP/CLI admission
       → AWAIT_DOCKER
       → PULLING
          ├─ pull failure → IDLE (failed; containers untouched)
          └─ pull success → RECREATING
               ├─ recreate failure ───────────────────────┐
               └─ containers up → VERIFYING target       │
                    ├─ exact-version liveness OK          │
                    │    → VERIFIED → IDLE                │
                    └─ verification window expires ───────┤
                                                         ▼
                                                   ROLLING_BACK
                                                         │ one attempt:
                                                         │ captured rollback_version
                                                         ▼
                                              VERIFYING rollback_version
                                                   ├─ success → IDLE (restored)
                                                   └─ failure → IDLE (failed;
                                                      explicit admission required)

Any non-terminal state + Launcher process restart
  → INTERRUPTED (no automatic resume)
  → explicit reconcile/update/rollback admission
```

**State descriptions:**

- **IDLE** — default state, listening for signals.
- **One coordinator and lock:** HTTP and mutating CLI commands call the same coordinator. Before admission, it takes `LAUNCHER_LOCK_PATH` using an OS-level cross-process lock; in-process mutexes alone are insufficient because `launcher update` may run beside the daemon. The exclusive lock is held from accepted admission through terminal operation-state persistence. The admitted operation captures `rollback_version = current_version` before any recreate. Replaying the same request ID returns its recorded response. A request for the same target while busy returns `202` with the current operation; a conflicting target or a separate process holding the lock returns `409`/non-zero CLI busy. Recent request/operation records are retained at least 24 hours (`contracts/launcher_ipc.md` §4).
- **AWAIT_DOCKER** — before touching anything, `Launcher` pings the Docker Engine API. If unavailable, it waits and retries every `LAUNCHER_DOCKER_RETRY_INTERVAL_SEC`, up to `LAUNCHER_DOCKER_RETRY_MAX_SEC` (or indefinitely by default). This guards against the case where `Launcher`'s host service starts before the Docker daemon itself has finished starting (a common boot-order race condition).
- **PULLING** — the Go Docker API pulls exactly `${LAUNCHER_GHCR_NAMESPACE}/head:${target_version}`, `/bot:${target_version}`, and `/ai_worker:${target_version}`, authenticated via `LAUNCHER_GHCR_TOKEN`. The target must match `contracts/launcher_ipc.md` §4's grammar. Arbitrary image names are never accepted. If any pull fails, no containers are touched; the operation fails and the error is logged.
- **RECREATING** — only the controlled Docker Compose CLI owns recreation. Launcher invokes the fixed service list without a shell and injects `APPLICATION_VERSION=<target_version>` into that child process, so Compose resolves all three application image tags to the admitted target. The Go Docker API is not used for container recreation. Broker pins are not changed or recreated by this operation.
- **VERIFYING** — polls authenticated `/v1/health` every `LAUNCHER_HEALTHCHECK_INTERVAL_SEC`, up to `LAUNCHER_HEALTHCHECK_TIMEOUT_SEC`, with 2-second connect and 5-second response timeout per poll. Success requires a valid authenticated `200`, `status: "alive"`, and `version == target_version`. This is exact-version liveness, not leadership, Bot readiness, or dependency readiness.
- **VERIFIED** — target health check passed; only now does version history shift `previous_version = old current_version`, `current_version = target_version`. An automatic rollback that restores the captured `rollback_version` leaves the pre-operation history unchanged. A successful manual rollback swaps current/previous only after verification.
- **ROLLING_BACK** — a recreate or post-recreate verification failure makes exactly **one** automatic rollback attempt using the operation's captured `rollback_version` (the verified current version before this update), followed by exact-version liveness verification of that tag. A pull failure before containers are touched simply fails the update and does not need rollback. If the rollback attempt fails, `Launcher` stops automatic recovery and requires a new operator/Head admission. `launcher rollback` is a separate manual operation targeting version history's `previous_version`; it never counts as, or recursively triggers, the automatic attempt.
- **INTERRUPTED** — on process startup, any persisted operation that was active is marked interrupted with its last durable phase. Launcher does not infer whether a pull/recreate finished and never resumes it automatically. `/v1/status` remains available; an explicit new update, rollback, or `reason: "reconcile"` admission is required.

---

## 7. Logging

`Launcher` does not participate in the centralized logging pipeline. There is no log collection, no forwarding, and no Mosquitto integration for `Launcher` — by design, to keep it fully independent of the stack it manages.

- Logs are written **locally to disk only**, in the same structured format used elsewhere for consistency if anyone needs to read them manually:
  ```
  [%time%][%level%][launcher][%file/module%]<tags>: [%message%]
  ```
- Log levels: `INFO` (state transitions, successful pulls/recreates), `WARNING` (rollback triggered, Docker daemon unavailable and retrying), `ERROR` (pull failure, rollback failure, Docker Engine API errors).
- **Sensitive data exclusion:** `LAUNCHER_GHCR_TOKEN` and any other credentials must never appear in log output, at any log level.
- The log file rotates locally (size or time-based, implementation detail) but is never shipped anywhere automatically. If an administrator needs to inspect it during troubleshooting, it's read directly from disk on the host.

---

## 8. Metrics

`Launcher` does not collect or report metrics to anywhere. There is no metrics pipeline for `Launcher`, no Azure Table Storage entry, and no Cosmos DB record — consistent with the principle that `Launcher` itself has zero cloud dependencies.

The only state exposed at all is via the local `/status` endpoint (Section 11), intended for direct administrator inspection on that specific node — not for centralized aggregation. If cluster-wide version visibility is ever desired, `Head` is responsible for reading this endpoint and reporting it onward; `Launcher` itself never reaches outward.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Docker daemon not yet available | Daemon ping fails | Enter `AWAIT_DOCKER`, retry every `LAUNCHER_DOCKER_RETRY_INTERVAL_SEC` until available or `LAUNCHER_DOCKER_RETRY_MAX_SEC` elapses. |
| GHCR unreachable during pull | `docker pull` returns non-zero exit / API error | Abort update, remain `IDLE`, no containers touched. Logged as `ERROR`. |
| GHCR authentication failure | Pull rejected with auth error | Abort update, remain `IDLE`. Logged as `ERROR` (token itself never logged). |
| Partial image pull (some services succeed, some fail) | Per-image pull result tracked individually | Abort entire update — never recreate with a mixed-version set. All-or-nothing. |
| Container recreate fails (e.g. port conflict, volume issue) | Controlled Compose child exits non-zero | Attempt the one automatic rollback to the captured pre-operation `current_version`. |
| New `Head` never returns authenticated alive at the exact target version | Verification window expires (including persistent version mismatch) | Attempt the one automatic rollback to the captured pre-operation `current_version`. |
| No verified current tag exists when automatic rollback is needed | First-install/invalid state has no captured `rollback_version` | Record rollback-unavailable and stop automatic recovery; require a new explicit admission. Never guess a tag. |
| Automatic rollback itself fails verification/recreate | Previous-tag attempt fails | Halt automatic recovery and expose the failure. Requires a new manual/Head admission; never flap or recurse. |
| `Launcher` process itself crashes during an operation | Process supervisor (systemd `Restart=always` / Windows Service auto-restart) | Supervisor restarts the process. Persisted active operation becomes `INTERRUPTED`; expose last durable phase/status, perform no pull/recreate automatically, and require explicit reconciliation/new admission. |
| Local IPC port already in use | Listener fails to bind on startup | `Launcher` fails to start, logs `ERROR` locally, process exits non-zero so the supervisor retries per its own restart policy. |
| Duplicate update request | Existing request ID | Return the persisted prior response; never start another operation. |
| Busy with same target | Active operation target matches | Return `202` and the current operation ID. |
| Busy with conflicting target | Active target differs | Return authenticated `409 update_busy`; caller must not retry automatically. |
| Separate Launcher daemon/CLI process owns operation lock | OS lock acquisition fails | Return/print busy with current persisted operation; perform no Docker or Compose action. |
| Invalid authentication/replay-integrity failure | HMAC/skew/cache validation | Return generic `401`, log redacted metadata, perform no operation. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| Docker Engine (local socket/pipe) | Daemon ping and image pulls through the Go API | `Launcher` waits in `AWAIT_DOCKER`, retrying — does not fail outright unless `LAUNCHER_DOCKER_RETRY_MAX_SEC` is exceeded. |
| Docker Compose CLI | Controlled application-container recreation | Non-zero exit triggers the one automatic rollback path; no shell fallback or raw Docker-API recreation is permitted. |
| GHCR (network) | Pulling new images | Update aborts cleanly, no partial state. |
| `Head` (container via authenticated host TCP / host-loopback published health) | Receiving auto-update signals, health verification | If unreachable, no automatic request arrives or verification eventually rolls back; manual CLI remains available. |

`Launcher` has **no dependency on Azure, RabbitMQ, or Mosquitto** — this is intentional and absolute, reinforcing its role as the one component that must survive total Compose-stack failure and remain operable even if every cloud-facing piece of the system is down.

---

## 11. Health Check

`Launcher` itself exposes a minimal status endpoint for administrator tooling (not polled by any other service, and not part of any monitoring pipeline):

```
GET http://127.0.0.1:{LAUNCHER_IPC_PORT}/v1/status
→ 200 OK
{
  "schema_version": 1,
  "state": "IDLE" | "AWAIT_DOCKER" | "PULLING" | "RECREATING" | "VERIFYING" | "ROLLING_BACK" | "INTERRUPTED",
  "current_version": "v1.4.0",
  "previous_version": "v1.3.2",
  "operation_id": null,
  "target_version": null,
  "rollback_version": null,
  "interrupted_operation": null
}
```

`/v1/status` is HMAC-authenticated and private to the documented Docker/host boundary. Liveness for the host service supervisor remains a process-exists check.

---

## 12. Versioning & Update Behavior

This section is somewhat circular since `Launcher` *is* the update mechanism — included here for completeness per the standard template:

- `Launcher`'s own binary version is tracked separately from the application's service versions (`bot`, `head`, `ai_worker`, `web` all version together; `Launcher` versions independently since it's a different artifact entirely, distributed as a binary release rather than a Docker image).
- Application release tags use the exact Docker-safe SemVer-compatible grammar in `contracts/launcher_ipc.md` §4. Compose requires `APPLICATION_VERSION`; Launcher supplies the admitted target to the controlled Compose child.
- The local coordinated image map is fixed: `head`, `bot`, and `ai_worker` under `LAUNCHER_GHCR_NAMESPACE`, all at the same `APPLICATION_VERSION`. Mosquitto/RabbitMQ use independent pinned images and are outside automatic application release mapping. `Web` is deployed independently and is not recreated by this host Launcher.
- `Launcher` does **not** update itself automatically. Self-update would require the process to replace its own running binary, which is high-risk for the one component that must always remain available. Self-updates, if ever needed, are a manual operation (replace binary, restart the systemd/Windows Service).