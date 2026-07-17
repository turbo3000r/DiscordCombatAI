# Service: Head

## 1. Responsibility

`Head` is the session coordinator and watchdog of the local node. It is the only container responsible for leader election across the distributed cluster, for authorizing the local `Bot` to connect to Discord, and for gathering and routing telemetry (logs and metrics) both locally and to Azure.

`Head` is what gives the system its "single persistent process" illusion: it decides, cluster-wide, which physical node's `Bot` is allowed to be the one actually talking to Discord at any given moment. It also owns the relationship with `Launcher` — it is the only component that knows when an update should happen and is the only one permitted to trigger `Launcher` into action.

`Head` itself does not generate battles, does not talk to Discord, and does not run AI logic. It is purely a coordination and observability layer sitting above the rest of the local stack.

---

## 2. File Structure

```
head/
├── Dockerfile
├── entrypoint.sh
├── main.py                        # Entry point: starts election loop, telemetry loop, IPC server
├── modules/
│   ├── election/
│   │   ├── pubsub_client.py        # Azure Web PubSub connection management — cluster broadcast group only (§3/§6); NOT the mutex
│   │   ├── lease_client.py         # Azure Blob Lease acquire/renew/release — the actual mutual-exclusion primitive (§3/§6, corrected this revision)
│   │   ├── heartbeat.py            # Publishes/listens for the leader heartbeat message inside the broadcast group (§6)
│   │   └── state_machine.py        # LEADER / FOLLOWER / CLAIMING / DRAINING / UPDATING transitions
│   ├── telemetry/
│   │   ├── metrics_collector.py    # CPU/RAM sampling, in-memory buffering
│   │   ├── log_aggregator.py       # Mosquitto subscriber, log parsing, buffering
│   │   └── azure_uploader.py       # Batched writes to Table Storage / Blob Storage
│   ├── update/
│   │   ├── release_poller.py       # Periodic GitHub Releases API polling
│   │   └── launcher_client.py      # Authenticated host-reachable HTTP client to Launcher
│   ├── control/
│   │   └── mosquitto_publisher.py  # Publishes control/* signals to Bot, AI Worker
│   └── ipc/
│       └── http_server.py          # Exposes authenticated /v1/health and /v1/status
└── configs/
    └── logger_config.json
```

> **Note:** `modules/control/` is intentionally limited to Mosquitto publishing for now. Additional coordination channels (if any service ever needs something Mosquitto can't express well) are not ruled out, but none are planned at this time — see Section 4 note on `Bot` signaling.

---

## 3. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `HEAD_PUBSUB_CLUSTER_GROUP` | No | `cluster` | Every `Head` — leader or follower — joins this one Web PubSub group permanently on startup and never leaves. Broadcast channel for leader heartbeat + `update_available`. **Not** the mutex (§6). Canonical: `contracts/pubsub_live.md`. |
| `HEAD_PUBSUB_DASHBOARD_GROUP` | No | `dashboard-live` | **New (P0.6).** Leader-only send target for live telemetry/logs. Browsers join via negotiate tokens (join/leave only). Never give browsers `cluster` tokens. |
| `HEAD_ELECTION_HEARTBEAT_SEC` | No | `30` | How often the active leader (a) publishes its heartbeat message into the cluster broadcast group, and (b) renews its Azure Blob Lease. Kept on one shared cadence deliberately — see §6. |
| `HEAD_ELECTION_HEARTBEAT_TIMEOUT_SEC` | No | `90` | How long a follower waits without seeing a leader heartbeat message before treating the leader as gone and attempting to acquire the Blob Lease (§6). Should be a small multiple of `HEAD_ELECTION_HEARTBEAT_SEC`, not equal to it, to tolerate one missed tick without triggering a spurious re-election. |
| `HEAD_LEASE_BLOB_CONTAINER` | No | `coordination` | Blob container (within the shared storage account, `azure.md` §3) holding the leader lease blob. |
| `HEAD_LEASE_BLOB_NAME` | No | `leader.lock` | Name of the single blob whose lease is the cluster's actual mutex. One fixed blob, not per-node — created once if missing, never deleted. |
| `HEAD_LEASE_DURATION_SEC` | No | `60` | Azure Blob Lease duration. Must be comfortably longer than `HEAD_ELECTION_HEARTBEAT_SEC` so a single missed renewal doesn't cause the lease to expire mid-cycle. |
| `HEAD_LOSS_OF_INTERNET_GRACE_SEC` | No | `0` | Grace period before treating a connectivity drop as leadership loss (per node, tune to local network reliability). |
| `HEAD_RECONNECT_BACKOFF_MAX_SEC` | No | `1800` | Maximum exponential backoff delay (30 min) when retrying Azure connectivity after a disconnect, per existing design. |
| `HEAD_GITHUB_REPO` | Yes | — | `owner/repo` to poll for new releases. |
| `HEAD_RELEASE_POLL_INTERVAL_SEC` | No | `300` | How often `Head` checks the GitHub Releases API for a new version. |
| `HEAD_DRAIN_TIMEOUT_SEC` | No | `120` | **Resolved this revision (P0.3):** maximum time to wait for `Bot`'s `in_flight_workflows` (`contracts/drain_status.md` §1, not just RabbitMQ queue depth) to reach zero before escalating to the hard-stop sequence — see `bot/discord_bot.md` §6.5. Cross-reference: `Bot` publishes progress toward this every `BOT_DRAIN_PROGRESS_INTERVAL_SEC` (`bot/discord_bot.md` §3, default `5`). |
| `HEAD_BOT_STOP_ACK_TIMEOUT_SEC` | No | `15` | Best-effort wait for Bot's matching stopped acknowledgement before voluntary lease release. Timeout is logged; update may continue under the accepted overlap limitation. |
| `HEAD_BOT_GRANT_TTL_SEC` | No | `45` | Monotonic lifetime of each non-retained Bot grant. Must equal the Bot's configured control-drain bound or be lower only if earlier hard-stop is intentionally accepted. |
| `HEAD_BOT_GRANT_RENEW_SEC` | No | `15` | Grant-renew cadence while leadership is confidently current; must be comfortably below `HEAD_BOT_GRANT_TTL_SEC`. |
| `HEAD_MOSQUITTO_HOST` | No | `mosquitto` | Hostname of the local Mosquitto broker. |
| `HEAD_MOSQUITTO_PORT` | No | `1883` | Mosquitto broker port. |
| `HEAD_METRICS_INTERVAL_SEC` | No | `2` | Local CPU/RAM sampling interval (in-memory buffering). |
| `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` | No | `60` | How often buffered metrics/logs are flushed to Azure Table Storage / Blob Storage. |
| `HEAD_TELEMETRY_LIVE_INTERVAL_SEC` | No | `10` | How often the **leader** publishes live telemetry to `HEAD_PUBSUB_DASHBOARD_GROUP` — **always while leader**, no listener detection (`contracts/pubsub_live.md`, `contracts/telemetry.md`). |
| `HEAD_LAUNCHER_IPC_URL` | No | `http://host.docker.internal:9700` | Host-reachable Launcher base URL. Linux Compose requires the host-gateway mapping; this is not container loopback (`contracts/launcher_ipc.md`). |
| `HEAD_LAUNCHER_IPC_SECRET_FILE` | No | `/run/secrets/launcher_ipc_secret` | Read-only shared HMAC secret file, at least 32 random bytes (`contracts/launcher_ipc.md` §2/§3). |
| `HEAD_IPC_BIND` | No | `0.0.0.0` | Bind address inside the container for Launcher-facing health/status. |
| `HEAD_IPC_PORT` | No | `9800` | Container port. Compose publishes it as host loopback only via `127.0.0.1:${HEAD_IPC_HOST_PORT}:9800`. |

> **Azure configuration lives in `azure.md`, not here.** Per Section 10, `Head` depends on three Azure resources: **Web PubSub** (election + live telemetry), **Table Storage** (batched metrics), and **Blob Storage** (batched logs, and the leader lease blob — new dependency this revision, §3/§6 above). All Azure authentication and endpoint variables (`AZURE_TENANT_ID`, `HEAD_AZURE_CLIENT_ID`/`HEAD_AZURE_CLIENT_SECRET` — per-service Service Principal, corrected this revision, `azure.md` §3 — `AZURE_WEBPUBSUB_ENDPOINT`, `AZURE_STORAGE_ACCOUNT_NAME`, etc.) are defined exactly once in `azure.md` § 3 — see that file for the full list and descriptions. This table only lists variables owned by `Head` itself.
>
> **Correction from a previous revision of this doc:** this table previously also listed `HEAD_PUBSUB_CONNECTION_STRING` and `AZURE_COSMOS_CONNECTION_STRING` directly. Both have been removed: the former duplicated what is now `AZURE_WEBPUBSUB_ENDPOINT` in `azure.md`, and the latter was never actually consistent with the rest of this document — no section of `head.md` (Inbound, Outbound, or Dependencies) describes `Head` talking to Cosmos DB. If `Head` is ever given a real reason to read/write Cosmos DB (e.g. the "centralized version visibility" idea floated in Section 5), it should be re-added there deliberately, not left over from a stale draft.

---

## 4. Inbound Communication

| Source | Channel | Format | Trigger |
|---|---|---|---|
| Azure Web PubSub | WebSocket | Leader heartbeat message + `update_available` broadcast, in the permanent cluster group (`HEAD_PUBSUB_CLUSTER_GROUP`, §3 — corrected this revision, was presence-event-based) | Continuous, real-time push |
| All local services | Mosquitto, `logs/<level>/<service>` | Structured log string (see `mosquitto.md`) | Real-time, on every log emission |
| `Bot` | Mosquitto, `status/bot/heartbeat` | `{latency_ms, guild_count}` — confirmed extension beyond a bare ping, `bot/discord_bot.md` §6.3; leader Head samples these into Table rows (`contracts/telemetry.md` §1) | Periodic |
| `AI Worker` | Mosquitto, `status/ai_worker/heartbeat` | Lightweight liveness ping | Periodic |
| `Bot` | Mosquitto, `status/bot/drain_progress` (QoS 1, not retained) — **new this revision, P0.3** | `{"schema_version": 1, "node_id": ..., "leadership_term": ..., "in_flight_workflows": N, "observed_at": ISO8601}` (`contracts/drain_status.md` §1) | Every `BOT_DRAIN_PROGRESS_INTERVAL_SEC` while `Head` is in `DRAINING` — this is what `Head` actually watches for drain completion, replacing the previous "infer from empty RabbitMQ queues" approach. |
| `AI Worker` | Mosquitto, `status/ai_worker/pause_ack` (QoS 1, not retained) — **new this revision, P0.3** | `{"schema_version": 1, "node_id": ..., "paused_at": ISO8601}` | Informational/diagnostic only — does **not** gate the `DRAINING` → `UPDATING` transition (`contracts/drain_status.md` §3). |
| `Launcher` (local host) | Authenticated HTTP, `GET /v1/health`, through host-loopback port mapping | HMAC headers; liveness schema in `contracts/launcher_ipc.md` §5 | During post-update verification |
| GitHub Releases API | HTTPS, polled | JSON release metadata | Every `HEAD_RELEASE_POLL_INTERVAL_SEC` |

> **Open point, acknowledged as such rather than resolved:** `control/bot/*` over Mosquitto is the only defined channel for `Head` → `Bot` activation/drain signaling today. This is treated as the current answer, not a permanent guarantee — if a future scenario genuinely doesn't fit pub/sub semantics (e.g. something requiring a confirmed synchronous response with retry-on-failure semantics that Mosquitto's fire-and-forget model can't give), an additional channel may be introduced later. Nothing currently demands it.

---

## 5. Outbound Communication

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| Azure Blob Storage (lease) | HTTPS | Acquire/renew/release the leader lease blob (`HEAD_LEASE_BLOB_CONTAINER`/`HEAD_LEASE_BLOB_NAME`, §3) — **the actual mutex, corrected this revision** | Acquire attempt: on entering `CLAIMING`. Renew: every `HEAD_ELECTION_HEARTBEAT_SEC` while leader. |
| Azure Web PubSub | WebSocket | Join permanent cluster group; publish canonical `leader_heartbeat` (`contracts/leadership_control.md` §4); publish `update_available` | Heartbeat every `HEAD_ELECTION_HEARTBEAT_SEC` while lease ownership is confirmed. Heartbeat is informational, not authority. |
| Azure Web PubSub | Service SDK `send_to_group` | Live `telemetry_live` payload (`contracts/telemetry.md` §4) to `HEAD_PUBSUB_DASHBOARD_GROUP` | Every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC` **while leader** — always-stream, no listener detection (`contracts/pubsub_live.md`) |
| Azure Table Storage | HTTPS | Batched metrics rows (`contracts/telemetry.md` §2) | Every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` — **leader only** |
| Azure Blob Storage | HTTPS | Appended log batch (`contracts/log_archive.md`) | Every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` — **leader only** |
| `Bot` (local) | Mosquitto, `control/bot/desired_state` (QoS 1, retained) | Safe mode `inactive \| draining \| stopped`; exact schema in `contracts/leadership_control.md` §3.1 | Publish `inactive` before election on every Head start; publish demotion/failure/update modes. Never carries active authorization. |
| `Bot` (local) | Mosquitto, `control/bot/activation_grant` (QoS 1, not retained) | Time-bounded `active \| draining` grant with term UUID and per-term sequence (`contracts/leadership_control.md` §3.2) | Issued/renewed only from confidently held leadership; stopped on demotion or uncertain authority. |
| `AI Worker` (local) | Mosquitto, `control/ai_worker/desired_state` (**retained**, same redesign) | `{"state": "running" \| "paused"}` | On this node's own update sequence start/end — **node-local, not cluster-wide** (corrected, `architecture.md`'s `AI Worker` note) |
| `Launcher` (host) | Authenticated HTTP, `POST /v1/update` to `HEAD_LAUNCHER_IPC_URL` | Async idempotent request/response contract in `contracts/launcher_ipc.md` §4 | After Bot hard-stop/ack wait and voluntary lease release |
| `Launcher` (host) | Authenticated HTTP, `GET /v1/status` | Schema in `contracts/launcher_ipc.md` §6 | Optional version/operation reconciliation |

> **Centralized version visibility (resolved, per Launcher discussion):** `Launcher` itself never talks to Azure. If cluster-wide version visibility is ever wanted on the Web dashboard, `Head` is the one responsible for periodically reading `Launcher`'s local `/status` and writing that into Cosmos DB or Table Storage itself. Not yet implemented — flagged as a future addition, not a current requirement.
>
> **Resolved (P0.6):** dashboard group is `HEAD_PUBSUB_DASHBOARD_GROUP` / `dashboard-live`, distinct from `cluster`. See `contracts/pubsub_live.md`.

---

## 6. Internal Logic / State Machine

```
                         ┌──────────────┐
                  ┌─────►│   FOLLOWER   │◄────────────────────────┐
                  │      └──────┬───────┘                         │
                  │             │ no heartbeat within             │
                  │             │ HEAD_ELECTION_HEARTBEAT_TIMEOUT_SEC │
                  │             ▼                                 │
                  │      ┌──────────────┐                         │
   lease acquire  │      │   CLAIMING   │                         │
   attempt failed │      └──────┬───────┘                         │
   (409, lost race)             │ lease acquired                  │
                  │             ▼                                 │
                  │      ┌──────────────┐   internet lost /       │
                  └──────┤    LEADER    ├──exceeds backoff ───────┤
                         └──────┬───────┘                         │
                                │ new version detected            │
                                │ (release_poller, leader only)   │
                                ▼                                 │
                         ┌──────────────┐                         │
                         │   DRAINING   │                         │
                         └──────┬───────┘                         │
                                │ drain complete / timeout        │
                                ▼                                 │
                         ┌──────────────┐                         |
                         │   UPDATING   │─────────────────────────┘
                         └──────────────┘   (signals Launcher, releases lease)

   (separate, orthogonal to the above): any state, on receiving
   `update_available` while NOT the leader → signal own Launcher
   directly, no drain needed (§12/`architecture.md` Scenario 5)
```

**State descriptions — corrected this revision (Blob Lease is now the actual mutex, replacing the incorrect "Web PubSub group join is exclusive" assumption; see `architecture.md`'s High-Level Architecture note for why):**

- **FOLLOWER** — default standby state. On process startup, before election, publishes and confirms retained `inactive`; failure to establish Mosquitto control prevents activation. Stays joined to `HEAD_PUBSUB_CLUSTER_GROUP` and listens for canonical heartbeats. Local `Bot` remains inactive.
- **CLAIMING** — entered when no leader heartbeat has been observed for `HEAD_ELECTION_HEARTBEAT_TIMEOUT_SEC`. Attempts to **acquire the Azure Blob Lease** on the fixed lease blob (§3, `HEAD_LEASE_BLOB_NAME`) — this is the actual point of mutual exclusion. If multiple `Head` instances attempt simultaneously, exactly one lease-acquire call succeeds; the rest receive a conflict response and fall straight back to `FOLLOWER` without ever touching `Bot`.
- **LEADER** — creates a new opaque `leadership_term` UUID after successful lease acquisition. Every `HEAD_ELECTION_HEARTBEAT_SEC`, renews the Blob Lease and publishes the canonical heartbeat. It issues short-lived, non-retained activation grants more frequently than their expiry. A UUID term is identity only, never globally ordered. No current grant means no active Bot.
- **DRAINING** — publishes retained `draining` and only bounded draining grants. Bot rejects new AI work and remains Gateway-connected only to finish existing work. **Resolved this revision (P0.3):** `Head` subscribes to `status/bot/drain_progress` (§4) and transitions to `UPDATING` when the reported `in_flight_workflows == 0` **or** `HEAD_DRAIN_TIMEOUT_SEC` elapses, whichever comes first — replacing the previous "infer from empty RabbitMQ queues" approach, which never covered lobby/collector/vote workflow stages that have no `ai_tasks` entry at all (`contracts/drain_status.md` §1). `AI Worker`'s `status/ai_worker/pause_ack` is received for diagnostics only and never blocks this transition (`contracts/drain_status.md` §3) — `in_flight_workflows` already covers whether this node's in-flight `ai_tasks` claim has finished. Timeout always escalates to the hard-stop sequence (`bot/discord_bot.md` §6.5), including cancelling any open lobby/collector/vote views — never silent abandonment.
- **UPDATING** — commands retained `stopped`, ceases grants, waits up to `HEAD_BOT_STOP_ACK_TIMEOUT_SEC` for `gateway_connected: false`, then releases the lease and calls authenticated `POST /v1/update`. A missing acknowledgement is logged and does not block forever; bounded overlap is an accepted limitation. Releasing the lease before commanding hard-stop is forbidden.
- **Follower path on update broadcast (new this revision, `architecture.md` Scenario 5):** a follower receiving `update_available` over the cluster group has no local `Bot` to drain, so it skips `DRAINING` entirely and signals its own local `Launcher` directly.
- **Failure transitions:** PubSub-only failure, Blob lease-renew failure, and Mosquitto failure cause bounded soft-stop; simultaneous loss of both Azure coordination dependencies causes immediate hard-stop. If this Head learns another Head owns leadership, it immediately commands hard-stop and ceases grants. Head crash needs no final publish: Bot hard-stops autonomously when its live grant/watchdog expires. Full ordering and recovery conditions are canonical in `contracts/leadership_control.md` §5.

The architecture intentionally does not claim strict at-most-one Gateway-connected Bot under every delay/partition. Blob Lease, grant TTLs, fail-closed boot, and acknowledgement waits are best-effort safeguards; bounded overlap remains accepted (`contracts/leadership_control.md` §1).

---

## 7. Logging

`Head` aggregates structured logs from the **local node's** Mosquitto broker (not a cross-node cluster log bus unless routing is added later — see P1.8).

- **Own logs:** `Head` logs its own state transitions and operations the same way every other service does — published to Mosquitto on `logs/<level>/head`, then immediately consumed by its own aggregator like any other service's logs (no special-casing).
- **Aggregation:** Subscribes to `logs/#` (all levels, all services) on the local Mosquitto broker. Parses each incoming message using the shared structured format in `contracts/log_archive.md`.
- **Buffering and routing:** Parsed log entries are buffered in memory (caps in `log_archive.md`) and flushed to **Azure Blob Storage** every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` by the **leader only**.
- **RabbitMQ bridging:** A lightweight internal subscriber listens to RabbitMQ's `rabbitmq_event_exchange` plugin and re-publishes relevant broker-level events onto `logs/warning/rabbitmq` or `logs/errors/rabbitmq` in the standard format, so RabbitMQ's internal health is visible in the same aggregated stream without other services needing AMQP event awareness.
- **Sensitive data exclusion:** never emit tokens/secrets — policy in `contracts/log_archive.md` §2; originating services own redaction.

---

## 8. Metrics

Canonical field producers, Table schema, and live payload: **`contracts/telemetry.md`**. Summary:

- **Local hardware metrics:** leader `Head` samples CPU and memory every `HEAD_METRICS_INTERVAL_SEC`.
- **Bot-derived fields:** `latency_ms` / `guild_count` sampled from Mosquitto `status/bot/heartbeat` into the same Table rows (nullable if missing).
- **`errors_in_window`:** count of ERROR-level lines in the current batch window; reset each flush. **`uptime_sec`:** this leader Head process uptime.
- **Batched persistence:** every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` → Table Storage — **leader only**. Followers do not upload dashboard telemetry.
- **Live streaming:** every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC` → `dashboard-live` while leader — **always**, no listener detection (`contracts/pubsub_live.md`).
- **Election-related metrics (recommended):** `is_leader`, `time_since_last_leader_change`, `election_transitions_total`.
- **No metrics about `Launcher`** — local to Launcher.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Azure Web PubSub unreachable while Blob Lease renewal remains confirmed | Connection drop / send failure | Soft-stop immediately. Lease renewal may continue during bounded recovery, but Bot accepts no new AI work. Restore only in the same confirmed term with a fresh active grant; otherwise hard-stop at drain timeout. |
| Azure Web PubSub unreachable (as follower) | Connection drop / send failure | No `Bot` action needed (already inactive). Same backoff reconnect logic applies before resuming normal election participation. |
| Competing claim / delayed demotion | Lease conflict, heartbeat/ack timeout | Blob Lease and grants reduce overlap but do not prove universal at-most-one Gateway connection. A Head that learns it is not leader immediately hard-stops local Bot. Bounded overlap is accepted and logged. |
| Mosquitto unreachable | Publish/subscribe failures | No activation is permitted. An already active Bot detects control loss and soft-stops, then hard-stops by drain/grant deadline. |
| GitHub Releases API unreachable | Poll request fails | Skip this poll cycle, retry at next `HEAD_RELEASE_POLL_INTERVAL_SEC` interval. Not treated as a critical failure. |
| Drain timeout exceeded | `HEAD_DRAIN_TIMEOUT_SEC` elapses with `in_flight_workflows > 0` (`contracts/drain_status.md` §1/§2, **resolved this revision, P0.3**) | Escalate to the canonical hard-stop sequence (`bot/discord_bot.md` §6.5), which now explicitly includes cancelling any open lobby/collector/vote views with a localized retry notice, not just RabbitMQ purge/revoke. |
| Concurrent/repeated `update_available` broadcasts for the same or a different version (**new this revision, P0.3**) | Broadcast payload's `target_version` compared against the version `Head` last acted on | Idempotent by `target_version` — a repeat of the version already being drained/updated for is ignored. A broadcast for a *different* version while already `DRAINING`/`UPDATING` is queued as the pending next target and only acted on after the current cycle fully completes (`contracts/drain_status.md` §4). |
| `Launcher` request fails/transiently times out | Authenticated `POST /v1/update` failure | Retry only per `contracts/launcher_ipc.md` §4 with the same request ID. Do not reacquire/activate merely because delivery outcome is unknown; reconcile through idempotent response/status. |
| `Head` container itself crashes | Grant renewal and heartbeat stop | Bot hard-stops autonomously at grant/watchdog expiry. Blob Lease expires naturally; a fresh Head starts by publishing retained inactive before election. |
| Azure Blob Lease renewal fails while PubSub remains available | Exception from `lease_client.py` | Soft-stop immediately; hard-stop at bounded drain timeout unless same-term authority is safely restored. |
| Blob Lease and Web PubSub both unavailable | Both coordination-plane dependencies fail | Immediate hard-stop; no bounded drain allowance. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| Azure Blob Storage (lease) | Leader election — **the actual mutex**, corrected this revision | Election halts (no node can safely become or confirm leader); see §9's new Blob Lease failure row. |
| Azure Web PubSub | Leader heartbeat broadcast, update-available broadcast, live telemetry streaming | PubSub-only loss soft-stops active Bot; it does not by itself prove loss of Blob Lease. |
| Mosquitto (local) | Log aggregation, control signaling | Activation is prohibited; active Bot soft-stops on its control disconnect and later hard-stops. |
| Azure Table Storage | Metrics persistence | Batched writes fail and are logged; in-memory buffer keeps accumulating up to its retention window, then oldest samples are dropped (per existing compression/retention design). |
| Azure Blob Storage | Log archival | Same as above — buffered logs accumulate, then drop oldest if the outage persists beyond buffer capacity. |
| GitHub Releases API | Auto-update detection | Polling fails silently per cycle; manual `launcher update --version` remains available regardless. |
| `Launcher` (host-reachable authenticated HTTP) | Triggering the update sequence | See `contracts/launcher_ipc.md`; manual CLI remains a fallback. |
| RabbitMQ (local) | Bridging broker-level events into the log stream | If RabbitMQ itself is down, there's simply nothing to bridge — not a failure of `Head`. |

---

## 11. Health Check

`Head` listens on `0.0.0.0:HEAD_IPC_PORT` inside the container; Compose publishes it only on host loopback. `Launcher` polls the authenticated endpoint during post-update verification:

```
GET http://127.0.0.1:{HEAD_IPC_HOST_PORT}/v1/health
→ 200 OK
{
  "schema_version": 1,
  "status": "alive",
  "version": "v1.5.0",
  "instance_id": "6b44781e-40f8-4807-9b4b-9087430c14b6",
  "started_at": "2026-07-15T17:06:00Z"
}
```

It may return `503` with `status: "initializing"` until initialized. This remains liveness-only: it says nothing about leadership, Discord connectivity, or Bot readiness. HMAC headers, timeout/retry behavior, and the exact schema are canonical in `contracts/launcher_ipc.md` §3/§5.

A separate, more detailed `/status` endpoint may additionally expose internal state for administrator debugging:

```
GET http://127.0.0.1:{HEAD_IPC_HOST_PORT}/v1/status
→ 200 OK
{
  "election_state": "LEADER" | "FOLLOWER" | "CLAIMING" | "DRAINING" | "UPDATING",
  "is_leader": true,
  "lease_held": true,
  "mosquitto_connected": true,
  "pubsub_connected": true
}
```

`lease_held` (added this revision) reflects whether this node currently holds the Blob Lease — the authoritative leadership signal, distinct from `is_leader`, which reflects the state machine's own belief. The two should always agree; if they ever diverge, that itself is worth alerting on (e.g. a lease renewal failure the state machine hasn't reacted to yet).

---

## 12. Versioning & Update Behavior

- `Head` does not version independently — it ships as part of the same coordinated release as `Bot`, `AI Worker`, and `Web` (all four share a version tag per release).
- **Corrected this revision (`architecture.md` Scenario 5):** only the current *leader* `Head` polls GitHub Releases and detects a new version, but **every** `Head` — leader and follower alike — reacts to the resulting `update_available` broadcast independently, since they're all permanently joined to the same cluster group (§3/§6). The leader is the sole initiator of *detection*, not the sole executor of the *update sequence* — that was the previous revision's gap (followers never updating at all).
  - **Leader path:** `DRAINING` (watching `status/bot/drain_progress`'s `in_flight_workflows`, `contracts/drain_status.md` §1, resolved P0.3) → Bot hard-stop → bounded stopped-ack wait → lease release → authenticated/idempotent Launcher request.
  - **Follower path (new):** no `Bot` to drain, so it signals its own `Launcher` directly on receiving the broadcast.
  - Neither path executes the update itself — that responsibility belongs entirely to each node's own `Launcher`, independently.
  - **Release-broadcast dedup (resolved this revision, P0.3):** `Head` tracks the last `target_version` it has already started draining/updating for and ignores a repeated broadcast for that same version. A broadcast for a different version while a cycle is already in progress is queued and only acted on once the current cycle fully completes — see `contracts/drain_status.md` §4. Manual `launcher update --version` bypasses `Head` entirely and needs no new rule (goes straight to `Launcher`'s already-idempotent `POST /v1/update`).
- After `Launcher` completes a recreate, the new `Head` instance has no memory of the previous instance's state. It starts cold in `FOLLOWER` and participates in election like any node joining the cluster for the first time. This is intentional — `Head` carries no state that needs to survive its own restart.