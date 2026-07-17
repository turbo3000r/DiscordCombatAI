# Service: Launcher

## 1. Responsibility

`Launcher` is a standalone host process (not a Docker container) responsible for pulling new Docker images and recreating the local `docker-compose` stack during version updates. It is the only component in the system capable of restarting the Compose network, which is why it must live entirely outside it.

`Launcher` is the foundation of the system's "always running" guarantee: from the outside, the node behaves like a single persistent process — but it is not one process, it is an entire cluster of containers that `Launcher` brings up, monitors at the surface level (via `Head`'s health signal), and recreates on update. `Launcher` itself has no cluster-awareness, no Azure access, and no knowledge of leadership state — it only knows how to pull images, recreate containers, and check whether the resulting `Head` is responding.

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
│   ├── ipc/
│   │   └── listener.go            # Authenticated host TCP listener reachable from Head container
│   ├── docker/
│   │   ├── ping.go                 # Checks Docker daemon availability before any operation
│   │   ├── pull.go                 # Docker Engine API: image pull
│   │   └── recreate.go             # Docker Engine API: container recreate
│   ├── healthcheck/
│   │   └── verify.go               # Polls Head's health endpoint post-update
│   └── state/
│       └── version_history.go      # Local-only: tracks current + previous version tags for rollback
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
| `LAUNCHER_GHCR_NAMESPACE` | Yes | — | GitHub Container Registry namespace (e.g. `ghcr.io/youruser/discord-combat-ai`). |
| `LAUNCHER_GHCR_TOKEN` | Yes | — | Dedicated read-only token for pulling images from GHCR. Used directly via the Docker Engine API's registry auth, independent of any host-level `docker login` session. Required because the host machine running `Launcher` may not have an authenticated Docker session of its own. |
| `LAUNCHER_DOCKER_RETRY_INTERVAL_SEC` | No | `15` | How often to re-check Docker daemon availability while waiting for it to come online. |
| `LAUNCHER_DOCKER_RETRY_MAX_SEC` | No | `0` (infinite) | Maximum time to keep retrying daemon availability before giving up on a triggered update. `0` means retry indefinitely. |
| `LAUNCHER_HEALTHCHECK_TIMEOUT_SEC` | No | `300` | How long to wait for the new `Head` to respond before triggering rollback. |
| `LAUNCHER_HEALTHCHECK_INTERVAL_SEC` | No | `10` | Polling interval during the verification window. |
| `LAUNCHER_VERSION_HISTORY_PATH` | No | `./launcher_state.json` | Local file tracking current and previous deployed version tags. Local-only, never synced to Azure. |
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
| Docker Engine (local) | Docker Engine API (Unix socket / named pipe) | API calls: daemon ping, image pull, container recreate | Continuously (ping) and on update/rollback execution |
| GHCR | HTTPS, authenticated with `LAUNCHER_GHCR_TOKEN` | Docker image pull | On update or rollback execution |
| `Head` (new container) | Authenticated HTTP, `GET http://127.0.0.1:${HEAD_IPC_HOST_PORT}/v1/health` | Liveness schema in `contracts/launcher_ipc.md` §5 | During verification window |
| Local log file | Filesystem | Plain text (see Section 7) | Continuous |

`Launcher` has no outbound communication to Azure, RabbitMQ, or Mosquitto, by design. If centralized visibility of node versions is ever needed, that responsibility belongs to `Head` — `Head` can read `Launcher`'s local `/status` endpoint and publish version info to Azure itself, keeping `Launcher` fully isolated from cloud dependencies.

---

## 6. Internal Logic / State Machine

```
                    ┌─────────────┐
                    │    IDLE     │◄────────────────────────┐
                    └──────┬──────┘                         │
                           │ update signal (HTTP or CLI)    │
                           ▼                                │
                    ┌─────────────┐                         │
                    │ AWAIT_DOCKER│  (retry/wait loop)      │
                    └──────┬──────┘                         │
                           │ daemon responds                │
                           ▼                                │
                    ┌─────────────┐  pull failure           │
                    │  PULLING    │─────────────────────────┤
                    └──────┬──────┘                         │
                           │ pull success                   │
                           ▼                                │
                    ┌─────────────┐                         |
                    │ RECREATING  │                         │
                    └──────┬──────┘                         │
                           │ containers up                  │
                           ▼                                │
                    ┌─────────────┐                         │
                    │ VERIFYING   │                         │
                    └──────┬──────┘                         │
              health OK    │    no response / timeout       │
                    ▼                          ▼            │
            ┌─────────────┐          ┌─────────────┐        │
            │  VERIFIED   │          │ ROLLING_BACK │───────┘
            └──────┬──────┘          └─────────────┘
                   │
                   ▼
              back to IDLE
```

**State descriptions:**

- **IDLE** — default state, listening for signals.
- **Request admission/idempotency:** before leaving `IDLE`, persist the request ID, operation ID, target, and accepted response. Replaying the same request ID returns its recorded response. A request for the same target while busy returns `202` with the current operation; a conflicting target returns `409`. Recent request/operation records are retained at least 24 hours across process restart (`contracts/launcher_ipc.md` §4).
- **AWAIT_DOCKER** — before touching anything, `Launcher` pings the Docker Engine API. If unavailable, it waits and retries every `LAUNCHER_DOCKER_RETRY_INTERVAL_SEC`, up to `LAUNCHER_DOCKER_RETRY_MAX_SEC` (or indefinitely by default). This guards against the case where `Launcher`'s host service starts before the Docker daemon itself has finished starting (a common boot-order race condition).
- **PULLING** — `docker pull` in progress for all service images at the target tag, authenticated via `LAUNCHER_GHCR_TOKEN`. If pull fails for any image, no containers are touched; state returns to `IDLE` and the failure is logged.
- **RECREATING** — `docker compose up -d --force-recreate` executed against the existing Compose file with new image tags.
- **VERIFYING** — polls authenticated `/v1/health` every `LAUNCHER_HEALTHCHECK_INTERVAL_SEC`, up to `LAUNCHER_HEALTHCHECK_TIMEOUT_SEC`, with 2-second connect and 5-second response timeout per poll. Only valid `200 status: alive` succeeds. This is liveness, not leadership/readiness.
- **VERIFIED** — health check passed; `version_history.json` updated, previous tag retained as rollback target.
- **ROLLING_BACK** — re-pulls and recreates using the previous known-good tag from `version_history.json`. If rollback itself fails, `Launcher` halts and requires manual intervention — it does not attempt further automatic recovery, to avoid flapping between broken states.

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
| Container recreate fails (e.g. port conflict, volume issue) | Non-zero exit from `docker compose up` | Attempt rollback to previous tag immediately. |
| New `Head` never responds within timeout | Health polling exhausts `LAUNCHER_HEALTHCHECK_TIMEOUT_SEC` | Trigger `ROLLING_BACK`. |
| Rollback itself fails | Recreate with previous tag also fails | Halt. Do not retry automatically. Requires manual `launcher status` / `launcher update` intervention — a deliberate stop to avoid infinite flap loops. |
| `Launcher` process itself crashes | Process supervisor (systemd `Restart=always` / Windows Service auto-restart) | Supervisor restarts the process. On restart, `Launcher` reads `version_history.json` and resumes from `IDLE` rather than assuming any in-progress state — if a crash happened mid-update, the administrator will see the stack in whatever state the last successful step left it in, and can re-trigger `launcher update` manually if needed. |
| Local IPC port already in use | Listener fails to bind on startup | `Launcher` fails to start, logs `ERROR` locally, process exits non-zero so the supervisor retries per its own restart policy. |
| Duplicate update request | Existing request ID | Return the persisted prior response; never start another operation. |
| Busy with same target | Active operation target matches | Return `202` and the current operation ID. |
| Busy with conflicting target | Active target differs | Return authenticated `409 update_busy`; caller must not retry automatically. |
| Invalid authentication/replay-integrity failure | HMAC/skew/cache validation | Return generic `401`, log redacted metadata, perform no operation. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| Docker Engine (local socket/pipe) | Pulling and recreating containers | `Launcher` waits in `AWAIT_DOCKER`, retrying — does not fail outright unless `LAUNCHER_DOCKER_RETRY_MAX_SEC` is exceeded. |
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
  "state": "IDLE" | "AWAIT_DOCKER" | "PULLING" | "RECREATING" | "VERIFYING" | "ROLLING_BACK",
  "current_version": "v1.4.0",
  "previous_version": "v1.3.2",
  "operation_id": null,
  "target_version": null
}
```

`/v1/status` is HMAC-authenticated and private to the documented Docker/host boundary. Liveness for the host service supervisor remains a process-exists check.

---

## 12. Versioning & Update Behavior

This section is somewhat circular since `Launcher` *is* the update mechanism — included here for completeness per the standard template:

- `Launcher`'s own binary version is tracked separately from the application's service versions (`bot`, `head`, `ai_worker`, `web` all version together; `Launcher` versions independently since it's a different artifact entirely, distributed as a binary release rather than a Docker image).
- `Launcher` does **not** update itself automatically. Self-update would require the process to replace its own running binary, which is high-risk for the one component that must always remain available. Self-updates, if ever needed, are a manual operation (replace binary, restart the systemd/Windows Service).