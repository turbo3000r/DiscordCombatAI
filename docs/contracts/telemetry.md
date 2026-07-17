# Contract: Telemetry Persistence and Live Stream

> **New this revision — closes P0.5.2** (persistence + live payload) and supplies the live metric/log batch schema for **P0.6**. Group topology, negotiate tokens, and reconnect behavior are canonical in `contracts/pubsub_live.md`; this file owns Table Storage rows, field producers, retention, current-snapshot vs history APIs, and the `telemetry_live` payload shape.

---

## 1. Scope and Multi-Node Rule

**Owner decision (option A):** only the **leader** `Head` batches metrics to Table Storage, archives logs to Blob (`contracts/log_archive.md`), and publishes live telemetry to the dashboard PubSub group. Follower Heads sample locally if useful for diagnostics but **do not** upload dashboard telemetry.

Sources:

| Field | Producer | Notes |
|---|---|---|
| `cpu_percent`, `memory_mb`, `memory_percent` | Leader `Head` host sampling every `HEAD_METRICS_INTERVAL_SEC` | |
| `uptime_sec` | Leader `Head` process uptime (seconds since this Head process started) | Not cluster uptime |
| `errors_in_window` | Leader `Head` | Count of **ERROR**-level log lines ingested into the aggregator during the current batch window; **reset to 0 each flush** |
| `latency_ms`, `guild_count` | Sampled from Bot Mosquitto `status/bot/heartbeat` (`bot/discord_bot.md` §6.3) into the same Table rows | Nullable if no fresh Bot heartbeat in the window |
| Live stream | Leader `Head` → `dashboard-live` group | Always, every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC` — see `pubsub_live.md` |

**Do not** use Cosmos `GuildConfigs` document count for Dashboard guilds — soft-deleted (`left_at != null`) pollution. Current guild count and latency for “now” come from Bot `status.py` (`contracts/status_document.md` §3 `status` section). History (cpu, memory, latency) comes from Table only.

---

## 2. Table Storage

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

---

## 3. Current Snapshot vs History (Web APIs)

| API | Source | Fields |
|---|---|---|
| `GET /api/metrics` (current) | Mix: latest Table row for CPU/memory/uptime/errors **plus** `status.py` `status` section for `latency_ms` / `guild_count` “now” | Do not invent Cosmos guild counts |
| `GET /api/metrics/history` | Table only | Time series for `cpu`, `memory`, `latency` (nullable points where `latency_ms` was null) |

Which leader/node the Dashboard shows when multiple historical partition keys exist: prefer the **current** leader’s `node_id` when known (via latest status / negotiate context); otherwise the most recently written partition. Fine multi-node picker UI remains P1.8.

---

## 4. Live PubSub Payload

Published by leader `Head` to `HEAD_PUBSUB_DASHBOARD_GROUP` (`dashboard-live`) every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC` (default `10`), **unconditionally while leader** — no listener detection (`contracts/pubsub_live.md`).

```json
{
  "schema_version": 1,
  "type": "telemetry_live",
  "seq": 1842,
  "node_id": "node-a",
  "leadership_term": "cd88086a-fd6d-48d4-8446-39523af2bf70",
  "sampled_at": "2026-07-15T17:02:10Z",
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
  ]
}
```

| Rule | Detail |
|---|---|
| `seq` | Monotonic per Head **process instance**; browsers ignore older/duplicate `seq` after reconnect |
| Log cap | Max **N** log lines and/or max payload bytes per tick (implementation default suggestion: N = 50, ~64 KiB). Drop **oldest** logs in the tick if over budget — oversized logs must not blow the Free_F1 quota |
| No backfill | Live channel carries only forward ticks; history via REST `/api/metrics/history` |
| Sensitive | Never include tokens, api_keys, webhook URLs, PubSub URLs, HMAC secrets (same policy as `contracts/log_archive.md`) |

---

## 5. Schema Evolution

`schema_version` on Table entities and live payloads; reject unknown on writers; Web ±1 additive tolerance for documents/payloads it reads (`drain_status.md` §5 / P0.5.5).

---

## 6. Related

| Concern | Canonical doc |
|---|---|
| Groups, negotiate, reconnect, Free_F1 budget | `contracts/pubsub_live.md` |
| Log line format + Blob append archive | `contracts/log_archive.md` |
| Bot heartbeat + status.py current latency/guilds | `contracts/status_document.md`, `bot/discord_bot.md` §6.3 |
| Head sampling/flush loops | `containers/head.md` §5/§7/§8 |
