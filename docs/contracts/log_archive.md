# Contract: Operational Log Archive (Blob Append)

> **New this revision — closes P0.5.6.** Canonical structured log line format, sensitive-content policy, and leader Head → Blob append archive. Live dashboard log tails use the same parsed fields inside `telemetry_live` payloads (`contracts/telemetry.md` §4); this file owns durable cold storage.

---

## 1. Canonical Line Format

Published on Mosquitto as one structured string (`architecture.md` / `mosquitto.md`), then parsed by leader `Head` and appended to Blob.

Wire shape (Mosquitto / in-memory):

```text
[%time%][%level%][%service%][%file/module%]<tags>: [%message%]
```

| Field | Rules |
|---|---|
| `time` | UTC ISO-8601, preferably with milliseconds, e.g. `2026-07-15T17:02:09.123Z` |
| `level` | Enum: `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `service` | Service name (`head`, `bot`, `ai_worker`, …) — no `]` or newlines |
| `file/module` | Short module path; no raw newlines |
| `tags` | Comma-separated `key=value` pairs. Escape: replace `]` → `%5D`, newline → `\n` (two-char backslash-n), `%` → `%25` inside tag values. Do not embed unescaped `]` that would close the tags bracket early. |
| `message` | Single-line for archival: newlines → `\n`; do not include unescaped control characters that break parsers |

Archived Blob lines are the same canonical string, one record per line (trailing `\n` record separator).

---

## 2. Sensitive Content Policy

**Never** log, at any level:

- Discord bot tokens
- Guild Gemini `api_key` values
- Discord webhook URLs
- Azure Web PubSub connection/client access URLs
- Launcher IPC HMAC secrets / Azure client secrets

Redact or omit; prefer logging identifiers (`guild_id`, `ticket_uid`) instead.

---

## 3. Blob Archive

| | |
|---|---|
| **Container** | `AZURE_LOG_ARCHIVE_CONTAINER` (default `service-logs`) |
| **Path** | Append blob: `logs/{node_id}/{yyyy}/{mm}/{dd}.log` (UTC date of the flush) |
| **Who writes** | Leader `Head` only (same multi-node rule as `contracts/telemetry.md` §1) |
| **Flush** | Every `HEAD_TELEMETRY_BATCH_INTERVAL_SEC` (default `60`) |
| **Buffer caps** | Max **10_000** lines **or** **8 MiB**, whichever first — then drop **oldest** |
| **On Blob failure** | Keep buffering with the same drop-oldest policy; retry next flush |

### Idempotency (v1)

Append is naturally append-only. On retry after an **uncertain** flush success, prefer **at-least-once**: rare duplicate lines are **acceptable for v1**. Do not require block-list tracking in v1.

---

## 4. Schema Evolution

Log lines are not JSON documents; the **format version** is implicit in parser compatibility. If the bracketed format changes incompatibly, bump a documented format version in this contract and teach Head’s parser both. Buffered/batch envelopes that wrap lines for live PubSub still carry `schema_version` per `telemetry.md`.

---

## 5. Related

| Concern | Canonical doc |
|---|---|
| Head aggregation | `containers/head.md` §7 |
| Mosquitto topics | `containers/mosquitto.md` |
| Live log batches | `contracts/telemetry.md` §4 |
| Azure env | `azure.md` §3 |
