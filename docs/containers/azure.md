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
│   ├── status.py        # Bot status, version, invite link, etc. (`bot.json`-style document)
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

> **Open item:** the exact file that constructs and caches the shared `ClientSecretCredential` (Section 6) isn't named yet in `architecture.md`'s tree (e.g. a `configs/credential.py`). Flagged here as an implementation detail to fill in when this library is actually built, not decided in this doc.

---

## 3. Environment Variables

This is the **complete and only** list of Azure-related environment variables in the system. Any container doc that depends on Azure must reference this table rather than repeat it.

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_TENANT_ID` | Yes | — | Azure AD tenant ID — shared across services (identifying the tenant is not a privilege boundary; the client ID/secret pair is). |
| `HEAD_AZURE_CLIENT_ID` / `HEAD_AZURE_CLIENT_SECRET` | Yes | — | `Head`'s own Service Principal — **corrected this revision, per-service now** (was one shared `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET` for every service). Scoped only to Table Storage, Blob Storage, and Web PubSub (§4). |
| `BOT_AZURE_CLIENT_ID` / `BOT_AZURE_CLIENT_SECRET` | Yes | — | `Bot`'s own Service Principal. Scoped only to Queue Storage and Cosmos DB (§4). |
| `WEB_AZURE_CLIENT_ID` / `WEB_AZURE_CLIENT_SECRET` | Yes | — | `Web`'s own Service Principal. Scoped only to Cosmos DB, Queue Storage, Web PubSub, and Table Storage (§4). |
| `AZURE_COSMOS_ENDPOINT` | Yes | — | Cosmos DB account endpoint (e.g. `https://<account>.documents.azure.com:443/`). Backs the Guild Configs and Suggestions containers. |
| `AZURE_STORAGE_ACCOUNT_NAME` | Yes | — | Single Storage Account name backing **Blob**, **Table**, and **Queue** Storage. Per-service endpoints (`https://<name>.blob.core.windows.net`, `.table.core.windows.net`, `.queue.core.windows.net`) are derived from this one name — there is deliberately no separate endpoint variable per storage service. |
| `AZURE_QUEUE_NAME` | Yes | `suggestions` | Name of the queue (within the storage account above) used for Web → Bot suggestion notification events (`architecture.md` Scenario 3). |
| `AZURE_WEBPUBSUB_ENDPOINT` | Yes | — | Azure Web PubSub resource endpoint, used for leader election presence and live telemetry/log streaming. |
| `AZURE_WEBPUBSUB_HUB_NAME` | No | `discordcombatai` | Web PubSub hub name under which the `leader` election group and telemetry/log broadcast groups live. |

**Design decision, revised this revision:** the previous version of this doc used one Azure AD Service Principal shared across every resource *and every service* (`Head`, `Bot`, `Web`) — one identity with broad access regardless of which service actually needed which resource. **Corrected:** each Azure-consuming service (`Head`, `Bot`, `Web`) gets its **own** Service Principal, scoped only to the resources that service actually uses per §4's table (`Head`: Table + Blob + Web PubSub; `Bot`: Queue + Cosmos; `Web`: Cosmos + Queue + Web PubSub + Table). This is a **free correction** — creating additional Azure AD App Registrations costs nothing — and is logically independent of the plaintext-Gemini-key decision below; it closes an unnecessary blast-radius gap (a compromised `Web` container no longer has any path to credentials scoped for `Head`/`Bot`, and vice versa) without adding any infrastructure cost or complexity that this project's "cheap, self-hosted" model needs to avoid.

Per service, one `ClientSecretCredential` is still constructed once (from the shared `AZURE_TENANT_ID` plus that service's own `<SERVICE>_AZURE_CLIENT_ID`/`<SERVICE>_AZURE_CLIENT_SECRET`) and shared across whichever clients in `clients/` that service actually imports — the "one credential object per process" pattern is unchanged, only the "one identity for the whole system" part is corrected.

> **Open item, not yet applied to actual infrastructure:** each service's own Service Principal needs RBAC role assignments scoped to only its own resources — e.g. `Head`'s SP gets *Storage Table Data Contributor* + *Storage Blob Data Contributor* + *Web PubSub Service Owner* only, not Cosmos or Queue access at all. Exact role names/scopes per service are to be confirmed at IaC/provisioning time. This doc defines the *application-level* contract (which env vars, which SDK auth flow, which service gets which identity); actually granting those roles on the live Azure resources is a separate infrastructure task outside this doc's scope.
>
> **Accepted risk, not solved by the above (per-guild Gemini API keys, plaintext):** per-guild Gemini credentials (`contracts/guild_config.md` §3/§7) are a *separate* secret from anything in this file — they're never touched by the Service Principal/RBAC model above, since they're user-supplied third-party API keys, not Azure resources. They are stored plaintext in Cosmos DB and travel plaintext on RabbitMQ messages (`ai_worker.md` §1). An Azure Key Vault secret-reference indirection was considered and rejected for v1: this project's cost model is "cheap, self-hosted, users bring their own Gemini key" — Key Vault's per-secret pricing and the added latency/complexity of a secret-fetch-per-task don't fit that model. **This is a deliberate, documented trade-off, not an oversight** — revisit only if the project's cost constraints change (e.g. a hosted/managed tier where the operator, not the guild, owns the key).

---

## 4. Inbound Communication

*(Reframed for a library: this section lists which internal services consume which client, rather than literal inbound network traffic — the actual inbound traffic is Azure's API responses, which is a normal synchronous SDK call, not worth tabulating per-call.)*

| Consumer | Client(s) Used | Azure Resource(s) | Purpose |
|---|---|---|---|
| `Head` | `table.py`, `blob.py`, `pubsub.py` | Table Storage, Blob Storage, Web PubSub | Batched telemetry writes; batched log archival; **leader lease acquire/renew/release on Blob Storage — new use of `blob.py`, this revision** (`head.md` §3/§6, the actual mutex); leader heartbeat + update broadcast + live streaming over Web PubSub (see `head.md` §4/§5). |
| `Bot` | `queue.py`, `cosmos.py` | Queue Storage, Cosmos DB | Polls for suggestion notifications, reads/writes guild configs and suggestions. |
| `Web` (remote) | `cosmos.py`, `queue.py`, `pubsub.py`, `table.py` | Cosmos DB, Queue Storage, Web PubSub, Table Storage | Suggestion CRUD, notification publish, live dashboard stats subscription, **and** historical metrics reads for the Dashboard/Performance pages (`web.md` §4/§5). |
| `AI Worker` | — | — | No direct Azure dependency today — all cloud I/O for AI tasks flows through `Bot`/`Head`, per `architecture.md`'s container breakdown. |

> **Addition applied in this revision:** `Web`'s `table.py` dependency was missing here even though `architecture.md`'s own Scenario 4 already implies it ("the browser fetches the last 24h of history via a static API call" — that API call has to read the same Table Storage `Head` writes batched metrics to). Added rather than inventing a second metrics path. **Deliberately not added:** Blob Storage. A historical log-replay feature for the Dashboard's live console would need it, but `web.md`'s current design keeps the console live-only (starts empty, fills from Web PubSub going forward) specifically to avoid taking on this dependency before it's actually needed — see `web.md` §12 Open Items.

---

## 5. Outbound Communication

*(Reframed for a library: this section describes what each client actually does against Azure, i.e. the library's own outbound calls, rather than a service's outbound messages.)*

| Client | SDK Package (assumed, standard for the resource) | Auth | Primary Operations |
|---|---|---|---|
| `cosmos.py` | `azure-cosmos` | Shared `ClientSecretCredential` | Read/upsert on Guild Configs and Suggestions containers. |
| `blob.py` | `azure-storage-blob` | Shared `ClientSecretCredential` | Append-blob writes for batched logs; block-blob uploads for battle result archives. |
| `table.py` | `azure-data-tables` | Shared `ClientSecretCredential` | Batched `upsert_entity` calls for performance metrics rows. |
| `queue.py` | `azure-storage-queue` | Shared `ClientSecretCredential` | `send_message` (Web → queue) / `receive_messages` (Bot polling, per `architecture.md`'s 5-minute check interval). |
| `pubsub.py` | `azure-messaging-webpubsubservice` | Shared `ClientSecretCredential` | Group join/leave (leader election presence), `send_to_group` (telemetry/log streaming, dashboard broadcast), **and** `get_client_access_token` (issues a short-lived, group-scoped token so a browser can join a broadcast group directly — see next note). |

> **Open item:** exact SDK package names above are the standard Azure SDK for Python packages for each resource type — reasonable to assume given the "Technology Stack" already commits to these Azure services, but not literally pinned in any requirements file yet, so treat the package names as expected rather than finalized.
>
> **Open item, blocking for `web.md`'s live-data design — narrowed this revision:** `get_client_access_token` needs to know *which group* to scope the token to, and that requires `Head`'s telemetry/log broadcast group(s) (`head.md` §5/§8) to actually have a stable, documented name. `head.md` now names its internal cluster-coordination group (`HEAD_PUBSUB_CLUSTER_GROUP`, §3) — but that group is Head-to-Head only (leader heartbeat, update broadcast) and should **not** be the one a browser gets a token for. The dashboard-facing telemetry/log group is a separate, still-unnamed group. Whether that one is a fixed name, derived per-node, or something else is not decided anywhere. Not resolved here — flagged so `head.md` and `web.md` §12 both point at the same open question instead of each guessing independently.

---

## 6. Internal Logic

- **Corrected this revision:** one `ClientSecretCredential` is constructed once **per consuming service**, from that service's own `<SERVICE>_AZURE_CLIENT_ID` / `<SERVICE>_AZURE_CLIENT_SECRET` (§3) plus the shared `AZURE_TENANT_ID`, and shared only across the clients that service itself imports (§4). There is no longer one credential object shared system-wide — `Head`, `Bot`, and `Web` each build their own, scoped to their own Service Principal.
- Each client in `clients/` is lazily instantiated on first use by whichever service imports it (no eager connection to all five resources at library import time, since most services only need a subset — see Section 4).
- Retry/backoff behavior beyond each Azure SDK's own default retry policy is **not decided** — flagged as an open item. This matters most for `Head`, since `head.md` §6/§9 already defines its own Azure-outage handling (immediate `Bot` stop + exponential backoff up to `HEAD_RECONNECT_BACKOFF_MAX_SEC`) at the *application* level; whether the SDK-level retry policy should be tuned to cooperate with that (e.g. fail fast instead of retrying internally) is unresolved.

---

## 7. Logging

- This library does not publish to the shared Mosquitto logging pipeline itself — it has no "service name" of its own. Errors are expected to propagate as exceptions to the *calling* service, which logs them using its own structured format (`[%time%][%level%][%service%][%file/module%]<tags>: [%message%]`, per `head.md` §7).
- **Recommended (not yet decided):** this library should raise its own typed exceptions (e.g. `AzureCosmosError`, `AzureBlobError`) instead of leaking raw SDK exceptions, so every consuming service logs Azure failures consistently. The exact exception hierarchy is not designed yet — flagged as an open item for whoever implements `clients/`.
- **Sensitive data exclusion:** no service's own `<SERVICE>_AZURE_CLIENT_SECRET` (§3, per-service since this revision) must ever appear in any log line, at any level — each service is responsible for its own secret, not just a single shared one.

---

## 8. Metrics

Not applicable directly — this library does not self-report metrics anywhere. If Azure SDK call latency or error-rate metrics are ever wanted, that responsibility belongs to whichever consuming service uses the client (e.g. `Head` could fold "Azure call failures" into its own election-related metrics per `head.md` §8). Not decided in any current doc — flagged as an open item, not assigned to any service yet.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Cosmos DB unreachable | Exception raised from `cosmos.py` call | Surfaced to the calling service (`Bot`/`Web`); this library defines no retry/fallback of its own — see the calling service's own doc for its reaction. |
| Blob Storage unreachable | Exception raised from `blob.py` call | Surfaced to `Head`; per `head.md` §9, buffered logs accumulate in memory until capacity, then oldest entries are dropped. |
| Table Storage unreachable | Exception raised from `table.py` call | Surfaced to `Head` (writer side): same buffering/drop behavior as Blob Storage, per `head.md` §9. Surfaced to `Web` (reader side): Dashboard/Performance historical charts fail to load — see `web.md` §9. Two independent failure paths off the same resource, not one shared reaction. |
| Queue Storage unreachable | Exception raised from `queue.py` call | Surfaced to `Bot`/`Web`; no documented fallback yet — flagged as an open item (does `Bot` simply skip that polling cycle, like `Head` does for GitHub Releases? Not specified anywhere). |
| Web PubSub unreachable | Exception raised from `pubsub.py` call | Surfaced to `Head` (publisher side): per `head.md` §6/§9, treated as loss-of-internet — immediate local `Bot` stop, exponential backoff reconnect. Surfaced to `Web` (subscriber side, via `get_client_access_token` or the browser's own connection): live dashboard data falls back to historical-only — see `web.md` §9. |
| A service's own `<SERVICE>_AZURE_CLIENT_SECRET` expired, revoked, or that service's Service Principal missing a required RBAC role | Every call against the affected resource, from that service only (per-service SPs, §3, mean this is now isolated to one service rather than the whole system), starts failing with an auth error | Not distinguished from a generic "resource unreachable" failure by any consuming service today — flagged as a design gap: an auth failure is permanent until a human rotates that service's secret/fixes its RBAC, whereas a transient network failure resolves itself. Treating them identically (e.g. `Head`'s infinite exponential backoff) risks retrying forever against a failure that will never self-heal. Worth a follow-up decision. |

---

## 10. Dependencies

None of its own — this library sits at the bottom of the dependency graph; every other Azure-consuming service depends on *it*, not the other way around.

---

## 11. Health Check

No exposed health endpoint — this is a library, not a process. **Recommended (not yet implemented):** each consuming service that depends on Azure connectivity should fold an `azure_connected`-style flag into its own `/status` endpoint, the same way `head.md` §11 already exposes `pubsub_connected: true`. Whether that should be one combined flag or a per-resource breakdown (`cosmos_connected`, `blob_connected`, ...) is not decided — flagged as an open item.

---

## 12. Versioning & Update Behavior

`src/shared/azure/` is not versioned independently. Per `architecture.md`'s Key Structural Decisions, `src/shared/` is copied into every service image at build time — this library ships as part of whichever service embeds it, on that service's own version tag (`bot`, `head`, `ai_worker`, `web` all share one coordinated tag per `Launcher.md` §12).
