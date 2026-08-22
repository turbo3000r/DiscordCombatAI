# Documentation Implementation-Readiness Backlog

> **Purpose:** documentation changes still required before the docs are an implementation specification without inventing behavior at code time. Not a product wishlist or coding checklist.
>
> **Readiness rule:**
> - **P0 — global blocker:** resolve before treating the architecture as implementable end to end.
> - **P1 — subsystem blocker:** resolve before implementing the affected subsystem; unrelated work may proceed.
> - **P2 — deferred/non-blocking:** safe to decide during implementation or later.
>
> An item is resolved only when the decision is in one canonical document, propagated to affected sources, and contradictory text is removed.

## Current verdict

**No remaining P0 blockers.** Documentation gates closed through **Phase 3** (`/config`, `/suggest`, S12).

**Quick Battle and AI graph gates are closed:** P1.1 and P1.2 now have canonical session, schema, rubric, bound, timeout, and acceptance behavior. Still open only in unrelated later scope: offline guild-removal sweep (P1.3 leftover), P1.6 (remaining Web API/ops), and P2 items.

| Gate | Status | Canonical home |
|---|---|---|
| Phase 0 foundation | Closed | contracts + `azure.md` + brokers |
| Phase 1 Launcher/Head | Closed | `Launcher.md`, `head.md`, `launcher_ipc.md` |
| Phase 2 Bot/AI transport | Closed | `ai_task.md`, `discord_bot.md` §6.0, scenarios S03–S05/S07/S08/S10 |
| Phase 2.5 local-dev | Closed (docs) | `local_development.md`, S14 |
| Phase 3 `/config`+`/suggest` | Closed | `discord_bot.md` §6.4/§6.6, `guild_config.md`, `suggestion.md`, S12 |
| Phase 4 AI graphs | Closed (docs) | `nodes.md`, `environment.md`, `battle.md`, `ai_task.md` |
| Phase 5 `/quick-battle` | Closed (docs) | `quick-battle.md`, `discord_bot.md` §6.3–§6.5, S11 |

---

# Resolved decisions

Compact index only. Full schemas, state machines, and env defaults live in the linked docs — do not re-litigate here.

## P0 — Global contracts

| ID | Topic | Canonical |
|---|---|---|
| P0.1 | Bot fencing / grants | `contracts/leadership_control.md` |
| P0.2 | Launcher IPC | `contracts/launcher_ipc.md` |
| P0.3 | Drain / update completion | `contracts/drain_status.md` |
| P0.4 | RabbitMQ / Celery wire | `contracts/ai_task.md`, `containers/rabbitmq.md` |
| P0.5 | Suggestion, telemetry, status, archives | `suggestion.md`, `telemetry.md`, `status_document.md`, `battle_archive.md`, `log_archive.md` |
| P0.6 | Web PubSub live | `contracts/pubsub_live.md` |
| P0.7 | Web Entra admin boundary | `contracts/web_auth.md` |
| P0.8 | Acceptance scenarios | `docs/scenarios/` |

**Delivery rule (project-wide):** at-least-once delivery, effectively-once outcome where contracts define dedup/claim — never claim exactly-once unless a contract says so.

## P1 — Closed subsystem gates

| ID | Topic | Canonical |
|---|---|---|
| P1.3 (partial + Phase 3 `/config`) | Guild Patch/ETag, soft-delete/rejoin, lists, first-use, Apply partial-field, model list truncate-25 | `contracts/guild_config.md` §4a/§7/§7a/§8, `config.md` |
| P1.4 | Suggestion claim/sweep/DM, failed-notification retry, catalog fail-closed | `contracts/suggestion.md`, `discord_bot.md` §6.6, S12 |
| P1.5 | Broker outage / Compose | `rabbitmq.md`, `mosquitto.md`, S05 |
| P1.7 | Azure RBAC / transient vs permanent / retries | `containers/azure.md` |
| P1.8 | Heartbeats, staleness, live caps | `contracts/telemetry.md` |
| P1.9 | Locale enum + AI mapping | `contracts/localization.md` |
| P1.1 | Quick Battle session behavior, Discord lifecycle, cancellation, delivery | `bot/commands/quick-battle.md`, `discord_bot.md` §6.3–§6.5, S11 |
| P1.2 | Structured graph schemas, attempts, rubrics, bounds, winner validation, timing | `ai_worker/nodes.md`, both graph docs, `contracts/ai_task.md` |

## Phase documentation gates

| Phase | One-line decision | Canonical |
|---|---|---|
| **1 Slice 0** | `src/head/`; SemVer release tags; fixed image set; Go pull + Compose recreate; interrupted ops not auto-resumed; verify = Head liveness + exact version | `Launcher.md`, `head.md`, `launcher_ipc.md` |
| **2** | Transport shell via `graph="environment"` + `AI_WORKER_TRANSPORT_SHELL` (no `stub` graph); shared `NODE_ID`; Celery task `ai_worker.tasks.run_graph`; Bot asyncio vs broker/MQTT threads; no slash commands | `to_resolve` historically; detail in `ai_task.md` §11, `discord_bot.md` §6.0, scenarios 03/04/05/07/08/10 |
| **2.5** | Fail-closed `DCA_RUNTIME_MODE`; separate Discord app; domain repos → Azure or `dev-support`; no Azure emulator; Queue/DM/webhooks suppressed in development | `contracts/local_development.md`, S14 |
| **3** | `ProcessCommand`; `/config` + `/suggest`; Components V2; Queue poller/sweep/DM; minimal Web Suggestions; S12. Excludes graphs, `/quick-battle`, offline guild sweep, Dashboard/Performance, webhook/S13 | `discord_bot.md` §6.4/§6.6, `guild_config.md` §7a/§8, `config.md`, `suggest.md`, `suggestion.md` §3/§3a, `web.md` Phase 3 note, `suggestions.md`, S12 |

### Phase 3 decision summary

- **`/config`:** `ensure_active_guild` then panel; one partial-field ETag Patch; plaintext `api_key`; `google-genai` list + Apply probe off the event loop (`asyncio.to_thread`); truncate models to 25.
- **`/suggest`:** `get_suggestion_catalog()` every invocation; fail closed; prod guild-or-DM; development guild-only (reject DM); no ticket UID without durable write success.
- **Delivery:** Cosmos authoritative; Queue is a hint; claim timeout `BOT_SUGGESTION_CLAIM_TIMEOUT_SEC=120`; failed → Web `mode=send` retry; bounded duplicate DM possible after crash-after-DM-before-Cosmos.
- **Web slice:** Entra (prod) / local admin (dev); Suggestions list/detail/respond only; target `src/web/`; legacy root `web/` reference-only.

---

# P1 — Subsystem blockers

## P1.1 Quick Battle session behavior and Discord constraints — resolved

Canonical: `bot/commands/quick-battle.md` §3–§14, `bot/discord_bot.md` §6.3–§6.5, `contracts/ai_task.md` §5–§8, and S11.

Closed behavior includes complete-ballot `ceil(70%)`; initial + three revisions then abort; concrete human/AI deadlines; 1–10 participants; one lobby per guild; snapshot/shrink/owner-transfer rules; input/output/token limits; all-phase abort/timeout/restart handling; cooldown/admission controls; safe mentions; Bot-owned static arenas; Bot-authenticated messaging; exact winner IDs; enabled-guild guard; expected task/revision correlation; stable delivery IDs; and bounded progress edits. Fighter collection is consistently step 6.

`random_winner_mode` remains hardcoded `false`; future exposure and UI-component promotion remain P2/non-blocking.

---

## P1.2 AI graph state and bounded-generation contracts — resolved

Canonical: `ai_worker/nodes.md` §1–§5, `graphs/environment.md` §2/§5–§9, `graphs/battle.md` §2/§5–§9, `ai_worker.md` §3/§6, and `contracts/ai_task.md` §5.

Closed behavior includes exact structured schemas and strict coercion; nullable-then-filled Validator verdicts; `attempts_used=len(attempts)`; 2–5 episodes and content/token/deadline bounds; malformed Predefine failure; exact winner cardinality/IDs with fail-closed emergent resolution; scripted consistency; environment/battle Validator and Decider rubrics; one-call Decider over at most four candidates; and worst-case call/latency math tied to 30s/120s/900s heartbeat/stall/overall timers.

Prompt wording/content and physical migration remain Phase 4 implementation work. They may implement but not redefine the canonical schemas/rubrics/bounds.

---

## P1.3 Guild configuration — remaining open

Most of P1.3 is closed (see Resolved). **Still deferred:**

- Detection of guild removals missed while Bot was offline (mark `left_at` for Cosmos docs absent from `bot.guilds`).
- Reconciliation scheduling algorithm — implementation detail after that semantics decision.

---

## P1.4 Suggestion delivery — remaining open

Phase 3 closed delivery correctness (see Resolved → P1.4 / Phase 3). **Still deferred:**

- Suggestion list/conversation **pagination** → track under **P1.6** (not required for Phase 3 minimal list/detail).
- Unrestricted multi-admin editing beyond ETag + `Idempotency-Key`.

---

## P1.6 Web API behavior and operational safety

**Evidence:** `web.md`; all `web/pages/*.md`.

Resolve before treating full Web (beyond Phase 3 Suggestions) as complete:

1. Exact request/response/error schemas (prefer one OpenAPI/Pydantic source of truth), status codes, validation limits, and pagination (including suggestion list/conversation pagination from P1.4).
2. `/api/metrics` and history fields after the telemetry contract is fixed; omit unsupported fields rather than fabricate/null them inconsistently.
   Define whether stale retained values are visibly marked when either metrics request fails.
3. PubSub negotiate errors/token expiry/reconnect behavior (auth boundary itself is **resolved P0.7** / `contracts/web_auth.md`).
4. Webhook broadcast: Discord POST timeout/retry detail (rate/cooldown/idempotency/audit already **resolved P0.7**). Suggestion `/respond` Idempotency-Key + ETag already **resolved Phase 3**.
5. Health/readiness contract and deployment target/rollout/rollback mechanism.
6. Web logging destination and retention. Web self-metrics are optional unless selected for v1 operations.
7. Version/changelog ownership: decide whether update announcements are merely content or are tied to the actual deployed release. Define the version source and persistence, or remove those endpoint fields for v1.

The charting library, CSS token system, and live-vs-polling choice for compact charts do not block backend/API implementation.

---

# P2 — Explicitly deferred or implementation-owned

These items should not prevent the docs from becoming implementation-ready once P0/P1 contracts are settled:

- authoring and tuning prompt text, and physically migrating `prompts/`;
- charting-library choice and CSS/design tokens;
- shared UI base-class API shape and manual-vs-auto rerender mechanics;
- promotion of command-specific components before a second consumer exists;
- exposing `random_winner_mode` in a future version;
- future split between Bot UI locale and AI content locale;
- localized suggestion category labels;
- exact localization copy/key names;
- optional metrics with no agreed v1 consumer (including RabbitMQ queue depth and Mosquitto broker metrics — explicitly deferred in P1.5);
- exact typed-exception class hierarchy (permanent vs transient classification is enough for Phase 0 — `azure.md` §6);
- applying already-documented RBAC to live infrastructure (required for deployment, but not a missing architecture decision once roles/scopes are documented in `azure.md` §3a);
- Entra group-claim **overage** Graph `memberOf` fallback for Web admin authorization (v1 requires emit-groups + small admin group; reject with **403** on overage — `contracts/web_auth.md` §2).
- Mosquitto broker-log bridging into Head archive (explicitly accepted as `docker logs` only in P1.5).

Move an item back to P1 only if implementation proves it changes a public contract, safety invariant, or persisted data shape.

---

# Remaining documentation cleanup

Only unfinished mechanical items outside the closed P1.1/P1.2 gates:

1. Remove stale “resolved” tombstones from component docs over time — ongoing hygiene.
2. Remove any remaining wording that Web PubSub group presence is used for leader election (heartbeat transport only) — if found.
3. Reconcile `web/pages/home.md` `version` response with the status document — **P1.6**.
4. Update `web/pages/template.md` stale “eventual authenticated admin” wording — **P1.6**.

---

# Suggested resolution order

1. **Offline guild-removal sweep** (P1.3 leftover), if needed before full guild lifecycle ops.
2. **Web details** (P1.6) for non-Suggestions pages and operational polish.

When all P0 items and the P1 items for a target subsystem are closed, that subsystem's docs are ready for implementation.

---

# Suggested implementation sequence

> This orders **code implementation**, not documentation decisions. A phase may start when its own P1 prerequisites are closed; it does not need to wait for unrelated P1 items. The documented `src/`, `launcher/`, broker configuration, and Compose structure is a target architecture, not proof that those artifacts already exist.

## Phase 0 — Foundation and executable contracts

1. Create the target package/container scaffold, dependency manifest, environment template, Compose skeleton, and shared logging/retry utilities.
2. Turn `contracts/*.md` into shared typed models and contract tests. Keep `schema_version`, identifiers, timestamps, and reject-unknown-version behavior uniform.
3. Build the shared Azure credential/client layer and thin resource services from `containers/azure.md`.
4. Configure RabbitMQ and Mosquitto, including healthchecks, durable queues/DLX, required plugins, local network exposure, volumes, and restart policy.

**Gate:** P1.5 broker decisions and P1.7 Azure client/RBAC/retry contracts are **closed**. Typed models (including P1.9 locale enum/mapping and P1.3 guild Patch helpers) and real Azure/broker wiring can proceed. Remaining leftover P1.3 items do **not** block Phase 0 scaffolding.

## Phase 1 — Launcher and Head coordination spine

`Launcher` and `Head` both require multiple implementation slices:

1. **Launcher:** authenticated IPC and persisted request deduplication → image pull/recreate/version history → Head health verification → rollback and CLI operations.
2. **Head:** Mosquitto safe-state startup → Blob Lease acquire/renew/release → PubSub heartbeat/update broadcast → Bot grant/desired-state machine → authenticated Launcher IPC → drain/update orchestration → release polling → log aggregation and telemetry.

Implement election/fencing before update automation or telemetry. Validate S01–S03 and S06 before adding S07–S09 behavior. Launcher IPC work can proceed in parallel with Head's broker/Azure election work.

**Documentation gate:** Phase 1 Slice 0 coordination reconciliation is closed. Implement against the exact release/version, fixed-image, coordinator/lock, interrupted-operation, verification/rollback, and heartbeat/buffer contracts above; do not invent alternate defaults.

## Phase 2 — AI Worker transport shell and Bot core

Build these in parallel around the **transport shell** (`graph="environment"` canned path — no `stub` discriminator) so the message path can be tested before LLM behavior:

1. **AI Worker shell:** Celery app `ai_worker.celery_app` + task `ai_worker.tasks.run_graph` / prefetch → pause/resume and heartbeat → progress publishing (exact Phase 2 sequence) → manual result publishing with publish-before-ack → redelivery/cancellation behavior → transport-shell canned environment result when `AI_WORKER_TRANSPORT_SHELL=true`.
2. **Bot core:** inactive startup and leadership watchdog → guild reconciliation/status heartbeat → Celery dispatch/result consumer/task tracker (asyncio vs broker-thread boundaries) → progress rendering plumbing → drain counter and hard-stop cancellation. **No slash commands; `ProcessCommand` deferred.**

**Documentation gate:** Phase 2 decisions in this file’s Resolved section are closed. Implement against transport-shell, `NODE_ID` injection, definitions-owned RabbitMQ topology, and the S03/S04/S05/S07/S08/S10 ownership matrices — do not invent a Discord stub command or a `stub` graph.

**Gate:** a harness-driven dummy `ai_task` must complete Bot → RabbitMQ → AI Worker → result/progress → Bot. Then validate the Phase 2-owned steps of S03, S04, S05, S07, S08, and S10.

## Phase 2.5 — Local product-development spine (documentation closed; implementation in progress)

Build after domain models exist; can proceed in parallel with Phase 2 transport once repository protocols are defined. Does **not** replace Phase 1 Azure fencing validation.

**Foundation spine (implement now):**

1. **Runtime guards:** `DCA_RUNTIME_MODE`, mandatory `DISCORD_DEVELOPMENT_GUILD_ID`, development `DISCORD_DEVELOPMENT_APPLICATION_ID` + overlay/`DEV_SUPPORT_URL` checks; derive `STORAGE_PROVIDER`; refuse Azure leakage in development.
2. **Domain ports + factory:** narrow `GuildRepository` / `SuggestionRepository` / `StatusRepository` / `MetricsRepository` with production Azure adapters and development HTTP adapters; composition root keyed by runtime mode.
3. **`dev-support`:** FastAPI internal API + SQLite volume + Mosquitto grant publisher + status seed. Reset via volume removal only (no host-published reset API).
4. **Bot wiring:** composition root; application-id verification before sync; guild-scoped sync; interaction/lifecycle filtering; inject local repositories; suppress suggestion queue/DM path in development; accept `dev-support` grants.
5. **Compose/packaging:** development overlay brings up `dev-support`, excludes Head from the merged development stack, maps `DISCORD_DEVELOPMENT_BOT_TOKEN` → Bot token, sets overlay marker.
6. **Tests:** unit tests for mode guards and guild filters; integration for `dev-support` persistence/grants; **partial S14 spine** acceptance. Keep S01–S10 on real Azure.

**Deferred (not spine gate):**

- Web wiring (local-admin auth, banner, loopback host publish, live feed, webhook dry-run) until `src/web/` / P1.6.
- `/config`, `/suggest` command exercise — **Phase 3 docs closed**; implement in Phase 3. `/quick-battle` implementation remains Phase 5; its P1.1/P1.2 documentation is closed.
- Full S14 steps that require commands/Web.

**Documentation gate:** Resolved → Local product-development architecture is closed. Implement against `contracts/local_development.md`; do not invent an Azure emulator.

**Gate:** S14 spine invariants hold on a laptop stack with zero Azure/Entra/webhook egress (Discord development app + Gemini allowed).

## Phase 3 — Configuration and suggestions vertical slices

**Documentation gate:** Phase 3 decisions in this file’s Resolved section are closed. Implement against `discord_bot.md` §6.4/§6.6, `guild_config.md` §7a/§8, `config.md`, `suggest.md`, `suggestion.md` §3/§3a, `web.md` Phase 3 slice, `suggestions.md`, and S12 — do not invent Apply atomicity, catalog fallbacks, or exactly-once DM delivery.

1. Implement `ProcessCommand` + localization keys needed by the two commands.
2. Implement `/config` (ensure_active, staged UI, Google list/probe off-loop, partial Apply Patch).
3. Implement `/suggest` ticket creation (catalog fail-closed) and Bot delivery claim/sweep/DM together with Web’s minimal Suggestions respond path; validate S12.
4. Use `/config` to establish valid guild/key/model data before any later real AI command phase.

These slices give useful functionality without depending on the unresolved battle graphs. `/config` and suggestion work can proceed in parallel after their shared Cosmos/Queue (or `dev-support`) services exist.

**Gate:** S12 invariants hold for production delivery paths; development suppresses Queue/DM per `local_development.md`.

## Phase 4 — AI graphs

Documentation gate is closed. Implement in three reviewable gates:

### Phase 4A — shared graph contracts

Implement strict structured parsing/coercion, usage/deadline accounting, shared retry wrapper, nullable-then-filled `AttemptRecord`, Validator/Decider schemas and rubrics, one-call ≤4-candidate Decider, and shared contract tests. Author prompt prose against—without changing—those contracts.

**Gate:** malformed output, retry exhaustion, deadline/token exhaustion, rubric verdict shape, Decider index validation, and attempts accounting tests pass independently of either full graph.

### Phase 4B — environment graph

Implement initial/revision routing, exact node schemas, maximum four candidates, 600s/120k-input/30k-output bounds, and deterministic output tests. Static generic arenas are not part of this graph.

**Gate:** initial/revision success, malformed-node retry/failure, Decider fallback, deadline/token failure, and `attempts_used` tests pass.

### Phase 4C — battle graph

Implement 2–5 episode planning/composition, exact outcome/winner cardinality, solo no-victor support, scripted consistency, emergent fail-closed winner IDs, 840s/350k-input/90k-output bounds, and worst-case tests.

**Gate:** episode boundaries, every node schema, story bounds, all outcome cardinalities, invalid/late winner IDs, Decider fallback, and call/deadline/token tests pass.

## Phase 5 — `/quick-battle`

The Bot container's flagship command must be split into reviewable subtasks:

1. command enabled/config guards, one-lobby/membership/cooldown/AI-slot admission, and drain gate;
2. 1–10 participant ownership, snapshot/shrink/transfer rules, concrete deadlines, and restart expiry;
3. Bot-owned generic arenas plus strict `.txt` → `Environment`;
4. custom environment collection and expected environment task IDs;
5. complete-ballot `ceil(70%)` loop, three revisions, then abort;
6. fighter collection and validation;
7. expected battle task, bounded progress edits, safe mentions, chunk/attachment delivery, and archive;
8. full abort/timeout/hard-stop/late-result/unavailable matrix.

**Gate:** Phase 4A–4C complete for real AI execution; all concrete S11 invariants pass. P1.1, P1.2, and P1.9 documentation are already closed.

## Phase 6 — Web

Web is one deployed container but should be implemented in slices:

1. multi-stage React/FastAPI shell, Entra middleware, admin authorization, and status-document seed;
2. Home and Guilds;
3. Dashboard and Performance after Head telemetry/history are available;
4. Suggestions alongside Phase 3;
5. Webhook broadcast and audit after guild data and P1.6 endpoint behavior are fixed;
6. health/readiness, logging destination, deployment, and rollback.

Close the relevant P1.6 items before treating each Web API/operations slice as complete; P1.8 observability contracts are closed. Validate S13 after auth and broadcast are both present.

## Parallel work and final gate

- After Phase 0 models exist, broker setup, Azure clients, Launcher, Web auth shell, early Head work, and the Phase 2.5 local-dev spine can proceed in parallel (local-dev must not be pointed at production Azure).
- After the transport shell exists, Bot core and AI Worker graph work can proceed in parallel; `/quick-battle` waits for both real graphs.
- Head telemetry can proceed in parallel with `/config` and `/suggest`; Dashboard/Performance waits for telemetry.
- The recommended critical path to the first complete battle is: foundation/contracts → brokers → minimal Head fencing → Bot/AI Worker transport → `/config` → graph contracts and graphs → `/quick-battle`. Local UI iteration may use Phase 2.5 once commands exist.
- Final architecture acceptance requires S01–S13 plus closure or explicit v1 removal of every remaining applicable P1 item. S14 is required for claiming safe product-development mode. P2 items do not block the first implementation.
