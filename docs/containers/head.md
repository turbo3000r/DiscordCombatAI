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
│   │   └── launcher_client.py      # Loopback HTTP client to local Launcher
│   ├── control/
│   │   └── mosquitto_publisher.py  # Publishes control/* signals to Bot, AI Worker
│   └── ipc/
│       └── http_server.py          # Exposes /health, /status, /update endpoints
└── configs/
    └── logger_config.json
```

> **Note:** `modules/control/` is intentionally limited to Mosquitto publishing for now. Additional coordination channels (if any service ever needs something Mosquitto can't express well) are not ruled out, but none are planned at this time — see Section 4 note on `Bot` signaling.

---

## 3. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `HEAD_PUBSUB_CLUSTER_GROUP` | No | `cluster` | **Renamed and redefined this revision** (was `HEAD_PUBSUB_LEADER_GROUP`, presence-based). Every `Head` — leader or follower — joins this one Web PubSub group permanently on startup and never leaves. It is a cheap, real-time broadcast channel carrying two message types: the leader's heartbeat, and `update_available` events. It is **not** the mutual-exclusion mechanism (§6) — see the corrected `architecture.md` note on why Web PubSub group membership alone can't provide that. |
| `HEAD_ELECTION_HEARTBEAT_SEC` | No | `30` | How often the active leader (a) publishes its heartbeat message into the cluster broadcast group, and (b) renews its Azure Blob Lease. Kept on one shared cadence deliberately — see §6. |
| `HEAD_ELECTION_HEARTBEAT_TIMEOUT_SEC` | No | `90` | How long a follower waits without seeing a leader heartbeat message before treating the leader as gone and attempting to acquire the Blob Lease (§6). Should be a small multiple of `HEAD_ELECTION_HEARTBEAT_SEC`, not equal to it, to tolerate one missed tick without triggering a spurious re-election. |
| `HEAD_LEASE_BLOB_CONTAINER` | No | `coordination` | Blob container (within the shared storage account, `azure.md` §3) holding the leader lease blob. |
| `HEAD_LEASE_BLOB_NAME` | No | `leader.lock` | Name of the single blob whose lease is the cluster's actual mutex. One fixed blob, not per-node — created once if missing, never deleted. |
| `HEAD_LEASE_DURATION_SEC` | No | `60` | Azure Blob Lease duration. Must be comfortably longer than `HEAD_ELECTION_HEARTBEAT_SEC` so a single missed renewal doesn't cause the lease to expire mid-cycle. |
| `HEAD_LOSS_OF_INTERNET_GRACE_SEC` | No | `0` | Grace period before treating a connectivity drop as leadership loss (per node, tune to local network reliability). |
| `HEAD_RECONNECT_BACKOFF_MAX_SEC` | No | `1800` | Maximum exponential backoff delay (30 min) when retrying Azure connectivity after a disconnect, per existing design. |
| `HEAD_GITHUB_REPO` | Yes | — | `owner/repo` to poll for new releases. |
| `HEAD_RELEASE_POLL_INTERVAL_SEC` | No | `300` | How often `Head` checks the GitHub Releases API for a new version. |
| `HEAD_DRAIN_TIMEOUT_SEC` | No | `120` | Maximum time to wait for in-flight `ai_tasks` to resolve before forcing the update forward regardless. |
| `HEAD_MOSQUITTO_HOST` | No | `mosquitto` | Hostname of the local Mosquitto broker. |
| `HEAD_MOSQUITTO_PORT` | No | `1883` | Mosquitto broker port. |
| `HEAD_METRICS_INTERVAL_SEC` | No | `2` | Local CPU/RAM sampling interval (in-memory buffering). |
| `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` | No | `60` | How often buffered metrics/logs are flushed to Azure Table Storage / Blob Storage. |
| `HEAD_TELEMETRY_LIVE_INTERVAL_SEC` | No | `10` | Streaming interval to Web PubSub when an active dashboard listener is detected. |
| `HEAD_LAUNCHER_IPC_PORT` | No | `9700` | Loopback HTTP port to reach the local `Launcher`. Must match `LAUNCHER_IPC_PORT`. |
| `HEAD_IPC_PORT` | No | `9800` | Loopback HTTP port `Launcher` uses to reach `Head`'s `/health` endpoint. |

> **Azure configuration lives in `azure.md`, not here.** Per Section 10, `Head` depends on three Azure resources: **Web PubSub** (election + live telemetry), **Table Storage** (batched metrics), and **Blob Storage** (batched logs, and the leader lease blob — new dependency this revision, §3/§6 above). All Azure authentication and endpoint variables (`AZURE_TENANT_ID`, `HEAD_AZURE_CLIENT_ID`/`HEAD_AZURE_CLIENT_SECRET` — per-service Service Principal, corrected this revision, `azure.md` §3 — `AZURE_WEBPUBSUB_ENDPOINT`, `AZURE_STORAGE_ACCOUNT_NAME`, etc.) are defined exactly once in `azure.md` § 3 — see that file for the full list and descriptions. This table only lists variables owned by `Head` itself.
>
> **Correction from a previous revision of this doc:** this table previously also listed `HEAD_PUBSUB_CONNECTION_STRING` and `AZURE_COSMOS_CONNECTION_STRING` directly. Both have been removed: the former duplicated what is now `AZURE_WEBPUBSUB_ENDPOINT` in `azure.md`, and the latter was never actually consistent with the rest of this document — no section of `head.md` (Inbound, Outbound, or Dependencies) describes `Head` talking to Cosmos DB. If `Head` is ever given a real reason to read/write Cosmos DB (e.g. the "centralized version visibility" idea floated in Section 5), it should be re-added there deliberately, not left over from a stale draft.

---

## 4. Inbound Communication

| Source | Channel | Format | Trigger |
|---|---|---|---|
| Azure Web PubSub | WebSocket | Leader heartbeat message + `update_available` broadcast, in the permanent cluster group (`HEAD_PUBSUB_CLUSTER_GROUP`, §3 — corrected this revision, was presence-event-based) | Continuous, real-time push |
| All local services | Mosquitto, `logs/<level>/<service>` | Structured log string (see `mosquitto.md`) | Real-time, on every log emission |
| `Bot` | Mosquitto, `status/bot/heartbeat` | `{latency_ms, guild_count}` — confirmed extension beyond a bare ping, `bot/discord_bot.md` §6.3 | Periodic |
| `AI Worker` | Mosquitto, `status/ai_worker/heartbeat` | Lightweight liveness ping | Periodic |
| `Launcher` (local) | Loopback HTTP, `GET /health` | — | During `Launcher`'s post-update verification window |
| Web Dashboard (remote) | Azure Web PubSub | Subscribe event to telemetry stream group | When a dashboard client opens the live view |
| GitHub Releases API | HTTPS, polled | JSON release metadata | Every `HEAD_RELEASE_POLL_INTERVAL_SEC` |

> **Open point, acknowledged as such rather than resolved:** `control/bot/*` over Mosquitto is the only defined channel for `Head` → `Bot` activation/drain signaling today. This is treated as the current answer, not a permanent guarantee — if a future scenario genuinely doesn't fit pub/sub semantics (e.g. something requiring a confirmed synchronous response with retry-on-failure semantics that Mosquitto's fire-and-forget model can't give), an additional channel may be introduced later. Nothing currently demands it.

---

## 5. Outbound Communication

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| Azure Blob Storage (lease) | HTTPS | Acquire/renew/release the leader lease blob (`HEAD_LEASE_BLOB_CONTAINER`/`HEAD_LEASE_BLOB_NAME`, §3) — **the actual mutex, corrected this revision** | Acquire attempt: on entering `CLAIMING`. Renew: every `HEAD_ELECTION_HEARTBEAT_SEC` while leader. |
| Azure Web PubSub | WebSocket | Join the permanent cluster group (once, on startup, regardless of leader/follower state); publish leader heartbeat message; publish `update_available` broadcast | Join: once, on startup. Heartbeat: every `HEAD_ELECTION_HEARTBEAT_SEC` while leader. `update_available`: on detecting a new GitHub release, by whichever node is currently leader. |
| Azure Web PubSub | WebSocket | Live telemetry payload | Every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC`, only while a dashboard listener is connected |
| Azure Table Storage | HTTPS | Batched metrics rows | Every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` |
| Azure Blob Storage | HTTPS | Appended log batch | Every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` |
| `Bot` (local) | Mosquitto, `control/bot/activate` \| `control/bot/drain` \| `control/bot/stop` | Plain signal | `activate`: on winning leader election. `drain`: on a planned update sequence starting (§6 `DRAINING`). `stop`: on loss-of-internet-while-leader (§6/§9) — **hard stop**: purges the local `ai_tasks` queue and terminates any in-flight `AI Worker` execution (Celery `revoke(terminate=True)`) before `Bot` disconnects, see `bot/discord_bot.md` §6.5. |
| `AI Worker` (local) | Mosquitto, `control/ai_worker/pause` \| `control/ai_worker/resume` | Plain signal | On this node's own update sequence start/end — **node-local, not cluster-wide** (corrected, `architecture.md`'s `AI Worker` note) |
| `Launcher` (local) | Loopback HTTP, `POST /update` | JSON: `{"version": "vX.Y.Z", "reason": "auto_detected"}` | After successful drain, once a new version has been confirmed available |
| `Launcher` (local) | Loopback HTTP, `GET /status` | — | Optional, for `Head` to know current/previous version when deciding whether an update is actually needed |

> **Centralized version visibility (resolved, per Launcher discussion):** `Launcher` itself never talks to Azure. If cluster-wide version visibility is ever wanted on the Web dashboard, `Head` is the one responsible for periodically reading `Launcher`'s local `/status` and writing that into Cosmos DB or Table Storage itself. Not yet implemented — flagged as a future addition, not a current requirement.
>
> **Open item, raised from the `Web` side (`azure.md` §5, `web.md` §12) — narrowed, not fully resolved by this revision:** the internal cluster-coordination group now has a fixed, documented name (`HEAD_PUBSUB_CLUSTER_GROUP`, §3), but that group is Head-to-Head only (leader heartbeat + update broadcast) and should **not** be the same group a browser is handed a client access token for — mixing internal coordination traffic with a channel external dashboard clients can join is a scope-creep risk, not a simplification. The live telemetry/log broadcast group `Web`'s dashboard actually subscribes to is a **separate, still-unnamed** group — this part of the open item is not resolved here.

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

- **FOLLOWER** — default standby state. Stays joined to the permanent `HEAD_PUBSUB_CLUSTER_GROUP` (§3) and listens for the leader's heartbeat message — this is a cheap, real-time push, not a periodic poll. Local `Bot` remains inactive.
- **CLAIMING** — entered when no leader heartbeat has been observed for `HEAD_ELECTION_HEARTBEAT_TIMEOUT_SEC`. Attempts to **acquire the Azure Blob Lease** on the fixed lease blob (§3, `HEAD_LEASE_BLOB_NAME`) — this is the actual point of mutual exclusion. If multiple `Head` instances attempt simultaneously, exactly one lease-acquire call succeeds; the rest receive a conflict response and fall straight back to `FOLLOWER` without ever touching `Bot`.
- **LEADER** — every `HEAD_ELECTION_HEARTBEAT_SEC`: renews the Blob Lease **and** publishes its heartbeat message into the cluster group. Has signaled local `Bot` to activate via `control/bot/activate`. Begins full telemetry collection and routing duties. Polls GitHub Releases per `HEAD_RELEASE_POLL_INTERVAL_SEC`.
- **DRAINING** — entered only by the current leader, upon detecting a new version. Signals `Bot` via `control/bot/drain` to stop accepting new `/quick-battle` requests, and waits (up to `HEAD_DRAIN_TIMEOUT_SEC`) for in-flight `ai_tasks` to resolve through RabbitMQ.
- **UPDATING** — releases the Blob Lease and stops publishing a heartbeat (so the group naturally goes quiet and followers race for the lease), signals local `Launcher` via `POST /update`, and returns to `FOLLOWER` once the signal is sent. From this point, the actual update execution is entirely `Launcher`'s responsibility — `Head`'s job in this flow ends here, and a new `Head` instance (post-recreate) will re-enter election fresh.
- **Follower path on update broadcast (new this revision, `architecture.md` Scenario 5):** a follower receiving `update_available` over the cluster group has no local `Bot` to drain, so it skips `DRAINING` entirely and signals its own local `Launcher` directly.
- **Loss of internet (any state → FOLLOWER's local `Bot` stop):** If a leader `Head` loses connectivity to Azure, per existing design it immediately signals its local `Bot` to stop (`control/bot/stop` — the hard-stop action, distinct from `drain`'s soft gate, per §5's corrected three-action model), then attempts exponential backoff reconnection up to `HEAD_RECONNECT_BACKOFF_MAX_SEC`. If reconnection succeeds within that window, it re-attempts to resume leadership normally (re-checking whether a heartbeat is present, since another node may have claimed the lease in the meantime). If the backoff window is exceeded, the node is considered a fresh follower and re-enters the standard `FOLLOWER` cycle.

---

## 7. Logging

`Head` is the **sole aggregation point** for the entire cluster's structured logs.

- **Own logs:** `Head` logs its own state transitions and operations the same way every other service does — published to Mosquitto on `logs/<level>/head`, then immediately consumed by its own aggregator like any other service's logs (no special-casing).
- **Aggregation:** Subscribes to `logs/#` (all levels, all services) on the local Mosquitto broker. Parses each incoming message using the shared structured format:
  ```
  [%time%][%level%][%service%][%file/module%]<tags>: [%message%]
  ```
- **Buffering and routing:** Parsed log entries are buffered in memory and flushed to **Azure Blob Storage** every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC`, appended to the running log archive for that node.
- **RabbitMQ bridging:** A lightweight internal subscriber listens to RabbitMQ's `rabbitmq_event_exchange` plugin and re-publishes relevant broker-level events onto `logs/warning/rabbitmq` or `logs/errors/rabbitmq` in the standard format, so RabbitMQ's internal health is visible in the same aggregated stream without other services needing AMQP event awareness.
- **Sensitive data exclusion:** `Head` does not inspect or rewrite log content beyond parsing the structured fields — responsibility for never emitting credentials into a log line rests with each originating service.

---

## 8. Metrics

- **Local hardware metrics:** `Head` samples CPU and memory usage on its own host every `HEAD_METRICS_INTERVAL_SEC`, buffering in memory.
- **Batched persistence:** Every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC`, buffered metrics are written to **Azure Table Storage**.
- **Live streaming:** If a Web Dashboard client is connected (detected via Web PubSub group membership), `Head` additionally streams live metric/log payloads every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC`, on top of — not instead of — the batched writes.
- **Election-related metrics (recommended addition):** `Head` should track and expose, at minimum, `is_leader` (bool), `time_since_last_leader_change`, and `election_transitions_total` — useful for spotting a node that flaps between leader/follower abnormally.
- **No metrics are collected about `Launcher` or other host-level processes** — those remain fully local to `Launcher` itself, per its own documentation.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Azure Web PubSub unreachable (as leader) | Connection drop / send failure | Immediately signal local `Bot` to stop (`control/bot/stop` — hard stop, §5's corrected three-action model, was previously mis-described here as `drain` "with immediate effect"). Begin exponential backoff reconnect up to `HEAD_RECONNECT_BACKOFF_MAX_SEC`. **The Blob Lease is untouched by this failure** (§6) — a leader that loses PubSub but keeps Azure Blob access could in principle keep renewing its lease while unable to broadcast a heartbeat; this doc's existing decision to treat any Azure-connectivity loss as an immediate stop (not just PubSub specifically) already avoids that edge case in practice, since `Bot` is stopped regardless of which Azure dependency failed. |
| Azure Web PubSub unreachable (as follower) | Connection drop / send failure | No `Bot` action needed (already inactive). Same backoff reconnect logic applies before resuming normal election participation. |
| Split-brain risk: two nodes claim leadership near-simultaneously | Both stop observing a leader heartbeat and enter `CLAIMING` | **Corrected this revision:** Web PubSub group join semantics do **not** resolve this on their own — Microsoft's own documentation confirms group membership is additive, not exclusive (`architecture.md`'s High-Level Architecture note). The Azure Blob Lease acquire call is the actual tie-breaker: exactly one `Head` succeeds, the rest get a conflict response and fall back to `FOLLOWER` immediately, **before** ever signaling `Bot` to activate. This closes the dual-active window that the previous revision of this doc accepted as an unavoidable edge case — it is not merely "less likely" now, the failure mode itself is removed by construction, assuming the Blob Lease service itself is available (see the Web PubSub unreachable rows above for what happens when it isn't). |
| Mosquitto unreachable | Publish/subscribe failures | Log aggregation and control signaling halt locally. `Head` continues its own election logic (independent of Mosquitto) but cannot signal `Bot`/`AI Worker` or collect logs until Mosquitto recovers. Logged as `ERROR` once Mosquitto itself reconnects (can't log the outage in real time without the broker). |
| GitHub Releases API unreachable | Poll request fails | Skip this poll cycle, retry at next `HEAD_RELEASE_POLL_INTERVAL_SEC` interval. Not treated as a critical failure. |
| Drain timeout exceeded mid-update | `HEAD_DRAIN_TIMEOUT_SEC` elapses with tasks still in-flight | Proceed to `UPDATING` regardless — in-flight tasks are abandoned per the existing RabbitMQ "light crash" behavior (cleared, error results synthesized). |
| `Launcher` unreachable when signaling update | `POST /update` connection refused/timeout | Log `ERROR`, remain `LEADER`, retry on next detection cycle. Update does not proceed silently lost — it will be re-attempted since the version check will still report "newer version available." |
| `Head` container itself crashes | Docker restart policy / `Launcher`'s next health poll cycle (if mid-verification) | No persistent state to recover — a fresh `Head` instance starts in `FOLLOWER` and re-enters election normally. If it crashed while holding the lease, the lease simply expires after `HEAD_LEASE_DURATION_SEC` with no renewal, and any follower's `CLAIMING` attempt after that point succeeds. Leadership loss is detected by peers via heartbeat timeout (`HEAD_ELECTION_HEARTBEAT_TIMEOUT_SEC`), not presence. |
| Azure Blob Lease acquire/renew call fails (leader) | Exception from `lease_client.py` | **New failure mode, this revision.** A failed renewal means the lease will expire at `HEAD_LEASE_DURATION_SEC` regardless of whether the leader is otherwise healthy — treat identically to the "Web PubSub unreachable (as leader)" row: immediately signal local `Bot` to stop rather than risk running past lease expiry with another node believing it's safe to claim. |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| Azure Blob Storage (lease) | Leader election — **the actual mutex**, corrected this revision | Election halts (no node can safely become or confirm leader); see §9's new Blob Lease failure row. |
| Azure Web PubSub | Leader heartbeat broadcast, update-available broadcast, live telemetry streaming | Broadcast halts; existing leader keeps operating locally until its own heartbeat fails, then triggers the loss-of-internet behavior described in Section 6/9. |
| Mosquitto (local) | Log aggregation, control signaling | `Head` keeps running its election loop independently, but loses cluster-wide log visibility and the ability to activate/drain `Bot`/`AI Worker`. |
| Azure Table Storage | Metrics persistence | Batched writes fail and are logged; in-memory buffer keeps accumulating up to its retention window, then oldest samples are dropped (per existing compression/retention design). |
| Azure Blob Storage | Log archival | Same as above — buffered logs accumulate, then drop oldest if the outage persists beyond buffer capacity. |
| GitHub Releases API | Auto-update detection | Polling fails silently per cycle; manual `launcher update --version` remains available regardless. |
| `Launcher` (local, loopback HTTP) | Triggering the update sequence | If unreachable, `Head` cannot hand off an update — see Failure Modes. Manual administrator intervention via `launcher update` CLI remains a valid fallback. |
| RabbitMQ (local) | Bridging broker-level events into the log stream | If RabbitMQ itself is down, there's simply nothing to bridge — not a failure of `Head`. |

---

## 11. Health Check

`Head` exposes a minimal health endpoint, polled exclusively by the local `Launcher` during post-update verification:

```
GET http://localhost:{HEAD_IPC_PORT}/health
→ 200 OK
{
  "status": "responding"
}
```

Per the agreed contract with `Launcher.md`: this endpoint answering at all is the entire definition of "healthy" from `Launcher`'s perspective. It carries no information about leadership state, Discord connectivity, or `Bot` activity — those are operational concerns visible elsewhere (e.g. the Web dashboard), not part of the update-verification contract.

A separate, more detailed `/status` endpoint may additionally expose internal state for administrator debugging:

```
GET http://localhost:{HEAD_IPC_PORT}/status
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
  - **Leader path:** `DRAINING` → `UPDATING` (Section 6) — drains `Bot` first, then signals its own `Launcher`.
  - **Follower path (new):** no `Bot` to drain, so it signals its own `Launcher` directly on receiving the broadcast.
  - Neither path executes the update itself — that responsibility belongs entirely to each node's own `Launcher`, independently.
- After `Launcher` completes a recreate, the new `Head` instance has no memory of the previous instance's state. It starts cold in `FOLLOWER` and participates in election like any node joining the cluster for the first time. This is intentional — `Head` carries no state that needs to survive its own restart.