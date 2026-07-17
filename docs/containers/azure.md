# Service: Azure (Shared Cloud Integration Library)

> **Schema deviation, flagged explicitly:** `azure.md` does not document a running container/process the way `head.md` or `Launcher.md` do. It documents `src/shared/azure/` — an internal library (per `architecture.md`'s Key Structural Decisions, `src/shared/` "is not a container... copied into each service image at build time") that every Azure-consuming service (`Head`, `Bot`, `Web`) imports directly. The 12-section template is followed for consistency, but sections that assume a standalone process (Inbound/Outbound Communication, Health Check) are reframed below to fit a library instead. This is the single reason `azure.md` reads differently from `head.md`/`Launcher.md` — everything else follows the same schema.

## 1. Responsibility

`azure.md` / `src/shared/azure/` centralizes **all** authentication and client construction for the five Azure resources used by the system (per `architecture.md`'s Technology Stack): **Web PubSub**, **Queue Storage**, **Cosmos DB**, **Blob Storage**, and **Table Storage**.

Its core purpose is to be the **single source of truth for Azure environment variables**. No other container doc should redefine or duplicate an `AZURE_*` variable — a consuming service's own doc should state *which* of these resources it depends on and link here for the variable definitions, rather than re-listing them. (`head.md` follows this convention as of this revision — see its Section 3 note.)

`azure.md` does not decide *what* each service does with Azure (that's each service's own responsibility, documented in its own file) — it only decides *how a client gets constructed and authenticated*.

---

## 2. File Structure

```
src/shared/azure/
├── services/            # Higher-level, domain-specific wrappers built on top of clients/
│   ├── suggestions.py   # Suggestion CRUD (Cosmos DB) + notification publish (Queue Storage)
│   ├── guilds.py        # Guild configs, battle logs
│   ├── status.py        # Single shared status document — nested schema resolved this revision, see §2's note below
│   ├── logging.py       # Log batch send/receive helpers
│   ├── metrics.py       # Metrics batch load/unload from Table Storage
│   └── guild_logs.py    # Blob Storage wrapper for battle/log archives
├── clients/             # Thin per-resource SDK client wrappers, one per Azure resource
│   ├── cosmos.py        # Azure Cosmos DB client wrapper
│   ├── queue.py         # Azure Queue Storage client wrapper
│   ├── blob.py          # Azure Blob Storage client wrapper
│   ├── pubsub.py        # Azure Web PubSub client wrapper
│   └── table.py         # Azure Table Storage client wrapper
├── configs/             # Azure config (env var loading/validation)
└── models/              # Azure-related Pydantic models
```

> **Credential construction:** `configs/credential.py` constructs and caches the per-process `ClientSecretCredential` (Section 6). Target path under `src/shared/azure/configs/`.

> **`status.py` — canonical contract is now `contracts/status_document.md` (P0.5.3 resolved).** One Blob JSON document with typed nested sections (`identity` / `status` / `suggestion_catalog`), `schema_version`, ETag RMW, bootstrap/seed defaults, and per-section owners: **Web** writes `identity` + `suggestion_catalog` (after deploy-time / first-load seed); **Bot** writes only `status`. This module remains the sole accessor; do not duplicate the schema here.

> **`guilds.py` — write/concurrency contract:** field-scoped Cosmos Patch + ETag, soft-delete/`left_at`, and rejoin semantics are canonical in `contracts/guild_config.md` §4/§7/§8 (P1.3 subset for shared clients, resolved). This module must implement those helpers; it must not whole-document overwrite admin and metadata fields in one blind replace.

---

## 3. Environment Variables

This is the **complete and only** list of Azure-related environment variables in the system. Any container doc that depends on Azure must reference this table rather than repeat it.

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_TENANT_ID` | Yes | — | Azure AD tenant ID — shared across services (identifying the tenant is not a privilege boundary; the client ID/secret pair is). |
| `HEAD_AZURE_CLIENT_ID` / `HEAD_AZURE_CLIENT_SECRET` | Yes | — | `Head`'s own Service Principal — **corrected this revision, per-service now** (was one shared `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET` for every service). Scoped only to Table Storage, Blob Storage, and Web PubSub (§4). |
| `BOT_AZURE_CLIENT_ID` / `BOT_AZURE_CLIENT_SECRET` | Yes | — | `Bot`'s own Service Principal. Scoped to Queue Storage, Cosmos DB, and Blob Storage (status `status` section + battle archive — §4). |
| `WEB_AZURE_CLIENT_ID` / `WEB_AZURE_CLIENT_SECRET` | Yes | — | `Web`'s own Service Principal. Scoped to Cosmos DB, Queue Storage, Web PubSub, Table Storage, and Blob Storage (status document identity/catalog — §4). |
| `AZURE_COSMOS_ENDPOINT` | Yes | — | Cosmos DB account endpoint (e.g. `https://<account>.documents.azure.com:443/`). Backs the Guild Configs and Suggestions containers. |
| `AZURE_COSMOS_DATABASE` | No | `DiscordCombatAI` | Shared Cosmos database name for `GuildConfigs` and `Suggestions` (`contracts/suggestion.md`, `contracts/guild_config.md`). |
| `AZURE_STORAGE_ACCOUNT_NAME` | Yes | — | Single Storage Account name backing **Blob**, **Table**, and **Queue** Storage. Per-service endpoints (`https://<name>.blob.core.windows.net`, `.table.core.windows.net`, `.queue.core.windows.net`) are derived from this one name — there is deliberately no separate endpoint variable per storage service. |
| `AZURE_QUEUE_NAME` | Yes | `suggestions` | Name of the queue (within the storage account above) used for Web → Bot suggestion notification events (`contracts/suggestion.md` §4). |
| `AZURE_METRICS_TABLE` | No | `NodeMetrics` | Table Storage table for leader Head batched metrics (`contracts/telemetry.md` §2). |
| `AZURE_STATUS_BLOB_CONTAINER` | No | `coordination` | Blob container for the shared status document (`contracts/status_document.md`). |
| `AZURE_STATUS_BLOB_NAME` | No | `bot_status.json` | Blob name for the shared status document. |
| `AZURE_BATTLE_ARCHIVE_CONTAINER` | No | `battle-results` | Blob container for battle story + metadata archives (`contracts/battle_archive.md`). |
| `AZURE_LOG_ARCHIVE_CONTAINER` | No | `service-logs` | Blob container for append-blob operational logs (`contracts/log_archive.md`). |
| `AZURE_WEBPUBSUB_ENDPOINT` | Yes | — | Azure Web PubSub resource endpoint, used for cluster broadcast and live telemetry/log streaming. |
| `AZURE_WEBPUBSUB_HUB_NAME` | No | `discordcombatai` | Web PubSub hub name under which `cluster` and `dashboard-live` groups live (`contracts/pubsub_live.md`). |

**Design decision, revised this revision:** the previous version of this doc used one Azure AD Service Principal shared across every resource *and every service* (`Head`, `Bot`, `Web`) — one identity with broad access regardless of which service actually needed which resource. **Corrected:** each Azure-consuming service (`Head`, `Bot`, `Web`) gets its **own** Service Principal, scoped only to the resources that service actually uses per §4's table (`Head`: Table + Blob + Web PubSub; `Bot`: Queue + Cosmos + Blob; `Web`: Cosmos + Queue + Web PubSub + Table + Blob). This is a **free correction** — creating additional Azure AD App Registrations costs nothing — and is logically independent of the plaintext-Gemini-key decision below; it closes an unnecessary blast-radius gap (a compromised `Web` container no longer has any path to credentials scoped for `Head`/`Bot`, and vice versa) without adding any infrastructure cost or complexity that this project's "cheap, self-hosted" model needs to avoid.

Per service, one `ClientSecretCredential` is still constructed once (from the shared `AZURE_TENANT_ID` plus that service's own `<SERVICE>_AZURE_CLIENT_ID`/`<SERVICE>_AZURE_CLIENT_SECRET`) and shared across whichever clients in `clients/` that service actually imports — the "one credential object per process" pattern is unchanged, only the "one identity for the whole system" part is corrected.

### 3a. RBAC roles and provisioning contract (resolved, P1.7)

**Where instructions live:** this section is the application-level provisioning contract. Target IaC / runbook path (when created): `infra/azure/` (Bicep/Terraform or a checked-in role-assignment script). Applying roles to a live subscription is an ops task (P2), not a missing architecture decision once the table below is followed.

| Service | Service Principal | Azure RBAC role(s) | Scope |
|---|---|---|---|
| `Head` | `HEAD_AZURE_CLIENT_*` | `Storage Blob Data Contributor` | Storage account containers used for Blob Lease + log archive (`AZURE_LOG_ARCHIVE_CONTAINER`). Head does **not** write the status document. |
| `Head` | | `Storage Table Data Contributor` | Storage account / `AZURE_METRICS_TABLE` |
| `Head` | | `Web PubSub Service Owner` | Web PubSub resource (join/leave/send + group ops for `cluster` and `dashboard-live`) |
| `Bot` | `BOT_AZURE_CLIENT_*` | `Cosmos DB Built-in Data Contributor` | Cosmos account / database `AZURE_COSMOS_DATABASE` (containers `GuildConfigs`, `Suggestions`) |
| `Bot` | | `Storage Queue Data Contributor` | Storage account / queue `AZURE_QUEUE_NAME` |
| `Bot` | | `Storage Blob Data Contributor` | Status blob (status section only) + `AZURE_BATTLE_ARCHIVE_CONTAINER` |
| `Web` | `WEB_AZURE_CLIENT_*` | `Cosmos DB Built-in Data Contributor` | Same database (read guilds; read/write suggestions) |
| `Web` | | `Storage Queue Data Contributor` | Suggestion notification queue (send) |
| `Web` | | `Storage Table Data Reader` | Metrics table (history charts) |
| `Web` | | `Storage Blob Data Contributor` | Status document identity/catalog RMW + `admin-audit` container (`contracts/web_auth.md`) |
| `Web` | | `Web PubSub Service Owner` | Negotiate-only: `get_client_access_token` for `dashboard-live` (`contracts/pubsub_live.md`) |

Do **not** grant Head Cosmos/Queue access, or Bot Web PubSub access, in v1.

> **Accepted risk, not solved by the above (per-guild Gemini API keys, plaintext):** per-guild Gemini credentials (`contracts/guild_config.md` §3/§7) are a *separate* secret from anything in this file — they're never touched by the Service Principal/RBAC model above, since they're user-supplied third-party API keys, not Azure resources. They are stored plaintext in Cosmos DB and travel plaintext on RabbitMQ messages (`ai_worker.md` §1). An Azure Key Vault secret-reference indirection was considered and rejected for v1: this project's cost model is "cheap, self-hosted, users bring their own Gemini key" — Key Vault's per-secret pricing and the added latency/complexity of a secret-fetch-per-task don't fit that model. **This is a deliberate, documented trade-off, not an oversight** — revisit only if the project's cost constraints change (e.g. a hosted/managed tier where the operator, not the guild, owns the key).

---

## 4. Inbound Communication

*(Reframed for a library: this section lists which internal services consume which client, rather than literal inbound network traffic — the actual inbound traffic is Azure's API responses, which is a normal synchronous SDK call, not worth tabulating per-call.)*

| Consumer | Client(s) Used | Azure Resource(s) | Purpose |
|---|---|---|---|
| `Head` | `table.py`, `blob.py`, `pubsub.py` | Table Storage, Blob Storage, Web PubSub | Batched telemetry writes (`contracts/telemetry.md`); log archival (`contracts/log_archive.md`); leader lease acquire/renew/release; cluster heartbeat + **always-on** live stream to `dashboard-live` (`contracts/pubsub_live.md`). |
| `Bot` | `queue.py`, `cosmos.py`, `blob.py` | Queue Storage, Cosmos DB, Blob Storage | Suggestion notifications + guild/suggestion Cosmos docs; `status` section of status document; battle result archives (`contracts/battle_archive.md`). |
| `Web` (remote) | `cosmos.py`, `queue.py`, `pubsub.py`, `table.py`, `blob.py` | Cosmos DB, Queue Storage, Web PubSub, Table Storage, Blob Storage | Suggestion CRUD, notification publish, PubSub **negotiate only** (browser connects), historical metrics, status document identity/catalog RMW. |
| `AI Worker` | — | — | No direct Azure dependency today — all cloud I/O for AI tasks flows through `Bot`/`Head`, per `architecture.md`'s container breakdown. |

> **Addition applied earlier:** `Web`'s `table.py` dependency. **Updated (P0.5.3):** `Web` also uses Blob Storage for the shared status document (identity/catalog RMW) — not for historical log replay. Dashboard console remains live-only via PubSub (`contracts/pubsub_live.md`); Blob log-read for replay is still deferred.

---

## 5. Outbound Communication

*(Reframed for a library: this section describes what each client actually does against Azure, i.e. the library's own outbound calls, rather than a service's outbound messages.)*

| Client | SDK Package (assumed, standard for the resource) | Auth | Primary Operations |
|---|---|---|---|
| `cosmos.py` | `azure-cosmos` | Shared `ClientSecretCredential` | Read/upsert on Guild Configs and Suggestions containers. |
| `blob.py` | `azure-storage-blob` | Shared `ClientSecretCredential` | Append-blob writes for batched logs; block-blob uploads for battle result archives. |
| `table.py` | `azure-data-tables` | Shared `ClientSecretCredential` | Batched `upsert_entity` calls for performance metrics rows. |
| `queue.py` | `azure-storage-queue` | Shared `ClientSecretCredential` | `send_message` (Web → queue) / `receive_messages` (Bot polling, per `architecture.md`'s 5-minute check interval). |
| `pubsub.py` | `azure-messaging-webpubsubservice` | Shared `ClientSecretCredential` | Group join/leave, `send_to_group` (telemetry/log streaming, dashboard broadcast), **and** `get_client_access_token` (short-lived, group-scoped browser token — `contracts/pubsub_live.md`). |

> **SDK package names** above are the expected Azure SDK for Python packages; exact pins live in `pyproject.toml` at implementation time (P2 / implementation detail).
>
> **Resolved (P0.6):** dashboard group is fixed — `HEAD_PUBSUB_DASHBOARD_GROUP` default `dashboard-live`. Negotiate issues join/leave-only tokens for that group; TTL/user-id/`oid` and Entra admin auth in `contracts/pubsub_live.md` §4 / `contracts/web_auth.md`. Cluster group remains `HEAD_PUBSUB_CLUSTER_GROUP` / `cluster` (Head-only).
>
> **Web human auth ≠ Azure SP auth:** browser Entra login (`WEB_ENTRA_*`, `contracts/web_auth.md`) is separate from the Web container’s Service Principal (`WEB_AZURE_CLIENT_*` in this file). Do not reuse SP secrets in MSAL.

---

## 6. Internal Logic

- **Credential:** one `ClientSecretCredential` per consuming service from `configs/credential.py` (§2/§3), shared only across that process's imported clients.
- **Lazy clients:** each `clients/*` wrapper instantiates on first use.
- **Failure classification (resolved, P1.7):** every client maps SDK/HTTP failures into one of two library-level categories (exact Python class names are implementation/P2 — behavior is normative):
  | Class | Typical signals | Retry? |
  |---|---|---|
  | **Permanent auth/permission** | HTTP 401/403; AADSTS credential errors; explicit “unauthorized” / RBAC denial | **Never** retry as transient. Fail fast; surface to caller; log at error without secret values. |
  | **Transient** | Network errors; timeouts; HTTP 408/429/5xx; Cosmos 429; storage throttling | May retry per §6a. |
- **SDK vs application retry (resolved, P1.7):** configure Azure SDK retry policies to **`max_retries = 0`** (or the minimum the SDK allows that effectively disables stacked retries) on shared clients. **Application-owned** backoff is the only intentional retry layer — this prevents SDK × app double-retry. Head's existing Azure-outage loop (`HEAD_RECONNECT_BACKOFF_MAX_SEC`, `head.md` §6/§9) remains the coordinator for election-critical Azure resources and must treat permanent auth failures as non-retryable (stop the infinite backoff and alarm / stay demoted).

### 6a. Timeouts and transient retry defaults (resolved, P1.7)

| Client | Per-call timeout | Transient retry | Notes |
|---|---|---|---|
| `cosmos.py` | `10s` | ≤3 attempts, exponential 0.5s → ×2 → cap 4s + jitter | Includes guild Patch/ETag RMW (`contracts/guild_config.md` §4a) and suggestion claim patches |
| `blob.py` | `15s` | ≤3 attempts, same backoff | Lease ops: fail fast into Head's election logic; do not hide lease loss behind long SDK waits |
| `table.py` | `15s` | ≤3 attempts | Head batch upload; Web reads |
| `queue.py` | `15s` (API); visibility timeout stays **60s** per `contracts/suggestion.md` | Send: ≤3 attempts. **Receive/poll:** no intra-cycle retry storm — see Bot behavior below | |
| `pubsub.py` | `10s` | ≤2 attempts for send/negotiate | Head soft/hard-stop rules still own “PubSub down” semantics |

**Bot Queue poll (canonical here + `discord_bot.md` §9):** on any receive failure, **skip the cycle** and wait until the next `BOT_QUEUE_POLL_INTERVAL_SEC`. After **3 consecutive** failed poll cycles, mark dependency health `azure_queue` degraded (for whatever health surface Bot exposes — P1.8 may refine the payload). Recovery is automatic on the next successful receive; do not duplicate delivery — claim rules in `contracts/suggestion.md` still apply. Sweep path remains independent of Queue.

**`/config` Cosmos Apply:** see `bot/commands/config.md` §12 — library raises classified errors; command keeps staged UI state and shows a localized error (permanent vs transient wording).

---

## 7. Logging

- This library does not publish to the shared Mosquitto logging pipeline itself — it has no "service name" of its own. Errors propagate as exceptions to the *calling* service, which logs them using its own structured format (`[%time%][%level%][%service%][%file/module%]<tags>: [%message%]`, per `head.md` §7 / `contracts/log_archive.md`).
- Prefer wrapping SDK failures in the two classified categories in §6 (permanent vs transient). A finer typed hierarchy (`AzureCosmosError`, …) is **P2** — optional sugar on top of the classification, not required before Phase 0 clients ship.
- **Sensitive data exclusion:** no service's own `<SERVICE>_AZURE_CLIENT_SECRET` must ever appear in any log line, at any level.

---

## 8. Metrics

Not applicable directly — this library does not self-report metrics. Optional Azure call latency/error counters remain P2 / P1.8 unless a consuming service adopts them.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Cosmos DB unreachable (transient) | Transient classification from `cosmos.py` | Caller-specific. Bot `/config`: keep staged changes, localized error (`config.md` §12). Suggestion writes: fail the command/request. Guild sync: skip guild, retry next sweep. |
| Cosmos DB auth/RBAC permanent | Permanent classification | Fail fast; do not apply Head-style infinite backoff. Operator must rotate secret or fix role assignment (§3a). |
| Blob Storage unreachable | Exception from `blob.py` | Surfaced to `Head`; per `head.md` §9, buffered logs accumulate then drop oldest. Lease failure follows leadership soft/hard-stop rules. |
| Table Storage unreachable | Exception from `table.py` | Head writer: buffer/drop per `head.md` §9. Web reader: historical charts fail — `web.md` §9. |
| Queue Storage unreachable | Exception from `queue.py` | **Bot poll:** skip cycle + consecutive-failure degraded flag (§6a); sweep still delivers pending tickets (`contracts/suggestion.md`). **Web enqueue after Cosmos write:** ticket remains `pending`; Bot sweep recovers — not a lost suggestion (`suggestion.md`). |
| Web PubSub unreachable | Exception from `pubsub.py` | Head: loss-of-coordination path (`head.md` §6/§9). Web negotiate: fail the API call; browser cannot get a token. |
| Secret expired / RBAC missing | Permanent auth failures on affected resource only (per-service SPs) | Fail fast for that service; other services' SPs unaffected. |

---

## 10. Dependencies

None of its own — this library sits at the bottom of the dependency graph; every other Azure-consuming service depends on *it*, not the other way around.

---

## 11. Health Check

No library HTTP endpoint. **Resolved default for consuming services (P1.7):** when a service exposes readiness/dependency health, use **per-resource booleans** for the resources in §4 (e.g. Head: `pubsub_connected`, `blob_lease_ok`; Bot: `cosmos_ok`, `azure_queue`; Web: optional). A single combined `azure_connected` is insufficient when only one resource is down. Exact Bot/AI Worker heartbeat payload enrichment remains P1.8; the classification and per-resource rule here are fixed.

---

## 12. Versioning & Update Behavior

`src/shared/azure/` is not versioned independently. Per `architecture.md`'s Key Structural Decisions, `src/shared/` is copied into every service image at build time — this library ships as part of whichever service embeds it, on that service's own version tag (`bot`, `head`, `ai_worker`, `web` all share one coordinated tag per `Launcher.md` §12).