# Contract: Telemetry Persistence and Live Stream

> **Closes P0.5.2** (persistence + live payload), supplies the live metric/log batch schema for **P0.6**, and closes **P1.8** heartbeat/staleness/buffer/cap semantics. Group topology, negotiate tokens, and reconnect behavior are canonical in `contracts/pubsub_live.md`; this file owns Bot/AI Worker heartbeats, Table Storage rows, field producers, buffering/retention, current-snapshot vs history APIs, and the `telemetry_live` payload shape.

---

## 1. Scope and Multi-Node Rule

**Owner decision (option A):** only the **leader** `Head` batches metrics to Table Storage, archives logs to Blob (`contracts/log_archive.md`), and publishes live telemetry to the dashboard PubSub group. Follower Heads sample locally if useful for diagnostics but **do not** upload dashboard telemetry.

Sources:

| Field | Producer | Notes |
|---|---|---|
| `cpu_percent`, `memory_mb`, `memory_percent` | Leader `Head` host sampling every `HEAD_METRICS_INTERVAL_SEC` | |
| `uptime_sec` | Leader `Head` process uptime (seconds since this Head process started) | Not cluster uptime |
| `errors_in_window` | Leader `Head` | Count of **ERROR**-level log lines ingested into the aggregator during the current batch window; **reset to 0 each flush** |
| `latency_ms`, `guild_count` | Sampled from the Bot heartbeat in §2 into the same Table rows | Nullable when the Bot heartbeat is stale |
| Live stream | Leader `Head` → `dashboard-live` group | Always, every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC` — see `pubsub_live.md` |

**Do not** use Cosmos `GuildConfigs` document count for Dashboard guilds — soft-deleted (`left_at != null`) pollution. Current guild count and latency for “now” come from Bot `status.py` (`contracts/status_document.md` §3 `status` section). History (cpu, memory, latency) comes from Table only.

---

## 2. Bot and AI Worker Heartbeats

Both heartbeats use Mosquitto QoS `0`, are never retained, and default to a 30-second publish interval. `Head` validates `schema_version`, `node_id`, `application_version`, enums, and field types; an invalid or unknown-version payload is ignored and does not refresh liveness.

### 2.1 `status/bot/heartbeat`

```json
{
  "schema_version": 1,
  "node_id": "node-a",
  "application_version": "v1.5.0",
  "observed_at": "2026-07-15T17:02:10Z",
  "gateway_connected": true,
  "latency_ms": 87,
  "guild_count": 12,
  "dependencies": {
    "rabbitmq_connected": true,
    "cosmos_ok": true,
    "azure_queue_ok": true,
    "status_blob_ok": true
  }
}
```

`latency_ms` is a non-negative integer or `null` while the Gateway is not connected; `guild_count` is a non-negative integer. Dependency booleans initialize `false`, become `true` only after a successful connection/real operation, and return to `false` on the documented failure threshold (for example Queue after three failed poll cycles). They are diagnostic and do not override leadership fencing. Mosquitto health is not duplicated in the payload: receipt of this non-retained heartbeat already proves that publish path worked at that instant.

### 2.2 `status/ai_worker/heartbeat`

```json
{
  "schema_version": 1,
  "node_id": "node-a",
  "application_version": "v1.5.0",
  "observed_at": "2026-07-15T17:02:10Z",
  "state": "running",
  "active_tasks": 1,
  "dependencies": {
    "rabbitmq_connected": true
  }
}
```

`state` is `running` or `paused`; `active_tasks` is a non-negative integer and is bounded by the configured worker concurrency. `rabbitmq_connected` initializes `false` and reflects the current Celery/Kombu broker connection. Gemini reachability is deliberately absent: credentials are per task/per guild, so a synthetic global Gemini probe would not represent the next task's dependency health.

### 2.3 Staleness

- `BOT_HEARTBEAT_INTERVAL_SEC` and `AI_WORKER_HEARTBEAT_INTERVAL_SEC` both default to `30`.
- `HEAD_SERVICE_HEARTBEAT_STALE_SEC` defaults to `90` and must be at least twice the largest configured service-heartbeat interval.
- `Head` uses local monotonic elapsed time since the last valid receipt, not `observed_at`, to determine freshness. `observed_at` is audit data.
- At `90` seconds without a valid Bot heartbeat, Bot liveness becomes `stale`; `latency_ms` and `guild_count` are written as `null` in subsequent Table rows. At `90` seconds without a valid AI Worker heartbeat, AI Worker liveness becomes `stale`.
- Staleness is operator-visible in Head status/logs but is not a leadership transition and does not itself stop Bot. Existing control/grant and dependency-specific failure rules remain authoritative.

These are the complete v1 Bot/AI Worker metric surfaces. Candidate business/quality counters without a transport (`commands_invoked_total`, task totals, graph duration, `attempts_used`, `forced_selection`, and broker queue depth) are explicitly deferred and must not be emitted as undocumented v1 telemetry.

---

## 3. Table Storage

| | |
|---|---|
| **Table** | `AZURE_METRICS_TABLE` (default `NodeMetrics`, `azure.md` §3) |
| **Partition key** | `node_id` — the leader node that wrote the row (e.g. `node-a`). Stick to this; do not prefix with `leader\|`. |
| **Row key** | `{yyyyMMddHHmmss}_{seq:04d}` UTC — sortable time range queries via row-key prefix/`ge`/`le`. `seq` disambiguates multiple flushes in the same second. |
| **Retention** | Keep **30 days**. Implementation may use Table TTL (if available on the account) or a periodic purge job later; the contract requires 30-day retention semantics. |

### Entity fields

```python
class NodeMetricsEntity(TypedDict):
    PartitionKey: str              # node_id
    RowKey: str                    # yyyyMMddHHmmss_seq
    schema_version: int            # current = 1
    node_id: str
    leadership_term: str           # opaque UUID from current lease term
    sampled_at: str                # ISO 8601 UTC — end of the batch window
    cpu_percent: float
    memory_mb: float
    memory_percent: float
    latency_ms: int | None         # null if Bot heartbeat missing
    guild_count: int | None        # null if Bot heartbeat missing
    errors_in_window: int          # ERROR log lines in this batch window; reset each flush
    uptime_sec: int                # leader Head process uptime
    batch_interval_sec: int        # HEAD_TELEMETRY_BATCH_INTERVAL_SEC used for this row
```

Flush cadence: every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` (default `60`). Writers reject unknown `schema_version`.

### Metrics buffering and drop policy

`Head` aggregates raw 2-second samples into one completed Table entity per 60-second batch window. Completed entities awaiting upload are held in an in-memory FIFO:

- `HEAD_METRICS_BUFFER_MAX_BATCHES` defaults to `60` (one hour at the default batch interval).
- A failed Table write leaves that completed entity queued. Recovery uploads queued entities oldest-first, using their original `PartitionKey`/`RowKey`.
- When enqueueing a completed entity would exceed the cap, drop the **oldest** queued entity, retain the newest data, increment a process-local dropped-window counter, and emit a rate-limited warning.
- The buffer is intentionally memory-only; a Head process restart loses queued entities. Live telemetry continues from current samples and does not backfill the dropped/pending windows.

Log archival has its own independent byte/record caps in `contracts/log_archive.md`; this metrics cap does not replace them.

---

## 4. Current Snapshot vs History (Web APIs)

| API | Source | Fields |
|---|---|---|
| `GET /api/metrics` (current) | Mix: latest Table row for CPU/memory/uptime/errors **plus** `status.py` `status` section for `latency_ms` / `guild_count` “now” | Do not invent Cosmos guild counts |
| `GET /api/metrics/history` | Table only | Time series for `cpu`, `memory`, `latency` (nullable points where `latency_ms` was null) |

When multiple historical partition keys exist, the Dashboard shows the **current** leader’s `node_id` when known (via latest status / negotiate context); otherwise it uses the most recently written partition. There is no multi-node picker in v1; adding one is optional future UI scope, not an observability-contract blocker.

---

## 5. Live PubSub Payload

Published by leader `Head` to `HEAD_PUBSUB_DASHBOARD_GROUP` (`dashboard-live`) every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC` (default `10`), **unconditionally while leader** — no listener detection (`contracts/pubsub_live.md`).

```json
{
  "schema_version": 1,
  "type": "telemetry_live",
  "seq": 1842,
  "node_id": "node-a",
  "leadership_term": "cd88086a-fd6d-48d4-8446-39523af2bf70",
  "sampled_at": "2026-07-15T17:02:10Z",
  "service_liveness": {
    "bot": "fresh",
    "ai_worker": "fresh"
  },
  "metrics": {
    "cpu_percent": 42.5,
    "memory_mb": 2048.0,
    "memory_percent": 51.2,
    "latency_ms": 87,
    "guild_count": 12,
    "errors_in_window": 0,
    "uptime_sec": 3600
  },
  "logs": [
    {
      "time": "2026-07-15T17:02:09.123Z",
      "level": "INFO",
      "service": "bot",
      "module": "heartbeat",
      "tags": "guild_id=1",
      "message": "heartbeat ok"
    }
  ],
  "logs_dropped": 0
}
```

| Rule | Detail |
|---|---|
| `seq` | Monotonic per Head **process instance**; browsers ignore older/duplicate `seq` after reconnect |
| Service liveness | `fresh` or `stale`, determined by §2.3. This is diagnostic; dependency details remain on the local heartbeat/Head status surface. |
| Log count cap | `HEAD_TELEMETRY_LIVE_MAX_LOGS`, default **50**. Select the newest 50 available lines for the tick. |
| Payload byte cap | `HEAD_TELEMETRY_LIVE_MAX_BYTES`, default **65,536 bytes**, measured over the final UTF-8 JSON payload. The fixed envelope and metrics are always retained; remove the oldest selected log lines until the payload fits. If one log line cannot fit, omit it. |
| Drop visibility | `logs_dropped` is the number of otherwise-eligible lines omitted by either cap for this tick. Live omissions are not retried/backfilled. |
| No backfill | Live channel carries only forward ticks; history via REST `/api/metrics/history` |
| Sensitive | Never include tokens, api_keys, webhook URLs, PubSub URLs, HMAC secrets (same policy as `contracts/log_archive.md`) |

---

## 6. Schema Evolution

`schema_version` on Table entities and live payloads; reject unknown on writers; Web ±1 additive tolerance for documents/payloads it reads (`drain_status.md` §5 / P0.5.5).

---

## 7. Related

| Concern | Canonical doc |
|---|---|
| Groups, negotiate, reconnect, Free_F1 budget | `contracts/pubsub_live.md` |
| Log line format + Blob append archive | `contracts/log_archive.md` |
| Bot heartbeat + status.py current latency/guilds | `contracts/status_document.md`, `bot/discord_bot.md` §6.3 |
| Head sampling/flush loops | `containers/head.md` §5/§7/§8 |
