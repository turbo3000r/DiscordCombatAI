# Documentation Implementation-Readiness Backlog

> **Purpose:** the authoritative list of documentation changes still required before the docs can be used as an implementation specification without engineers inventing behavior at code time.
>
> This is **not** a product wishlist or a coding checklist. Prompt authoring, file moves, RBAC provisioning, and UI polish belong to implementation unless a missing decision changes a contract or safety property.
>
> **Readiness rule:**
> - **P0 — global blocker:** resolve before treating the architecture as implementable end to end.
> - **P1 — subsystem blocker:** resolve before implementing the affected subsystem; unrelated work may proceed.
> - **P2 — deferred/non-blocking:** explicitly safe to decide during implementation or in a later version.
>
> An item is resolved only when the decision is propagated to every affected source document, exact schemas/state transitions are recorded in one canonical location, and obsolete contradictory text is removed. Merely choosing an option in this file is not enough.

## Current verdict

**No remaining P0 global blockers.** Phase 0 foundation prerequisites that were documentation-blocked are now closed: **P1.5** (brokers + Compose skeleton), **P1.7** (Azure client failure/RBAC/retry), **P1.9** (locale value contract), and the **P1.3 subset required by shared `guilds.py` clients** (field-scoped Patch + ETag, soft-delete/rejoin/list filters).

This does **not** mean every subsystem is ready to implement. In particular, `/quick-battle`, the real LangGraph graphs, remaining guild lifecycle edges, suggestion edge cases, and Web API schemas still require the open P1 items below. None of those gaps changes the global service topology or already-resolved cross-service wire contracts.

**Phase 0 implementation-ready now:** scaffold/Compose skeleton, shared typed models from contracts (including `language` enum + mapping), shared Azure credential/client layer, RabbitMQ + Mosquitto configuration. **Phase 1 coordination Slice 0 documentation is reconciled:** Launcher/Head versioning, image/recreate ownership, interrupted-operation recovery, verification/rollback, Blob-renew recovery, and P1.8 heartbeat/buffer/live-cap contracts are closed. **Still deferred for later phases:** remaining P1.3 command/lifecycle items, P1.1–P1.2, P1.4 leftovers, P1.6, and all P2 items.

---

# Resolved decisions

## P0.1 — Leadership-derived Bot fencing (resolved)

- Canonical contract: `contracts/leadership_control.md`.
- Bot defaults inactive and can activate only from a fresh, non-retained short-lived grant derived from the current Blob Lease term. Retained desired mode contains safe states only.
- Leadership term is an opaque UUID per successful lease acquisition; per-term command sequence is monotonic. UUID terms are not globally ordered.
- Grant/heartbeat expiry uses local monotonic elapsed time. Head crash/grant expiry hard-stops Bot autonomously.
- PubSub-only, Blob-renew-only, and Mosquitto failures soft-stop (bounded drain, reject new AI work, then hard-stop unless restored); total loss of Blob Lease and PubSub hard-stops immediately. Blob-renew-only recovery is same-term only: confirm the existing lease term and issue a fresh same-term grant within the bound, otherwise hard-stop. S06-C follows this path.
- Voluntary demotion commands hard-stop and waits boundedly for acknowledgement before lease release. Exact drain-completion contents are `contracts/drain_status.md` (P0.3, resolved).
- Strict at-most-one Gateway connection is not guaranteed in every partition/delay; bounded dual-active overlap is an explicitly accepted limitation.

## P0.2 — Cross-platform authenticated Launcher IPC (resolved)

- Canonical contract: `contracts/launcher_ipc.md`.
- Head → Launcher uses `host.docker.internal` host-reachable TCP; Linux adds `host-gateway`, Windows Docker Desktop uses the built-in hostname. Launcher binds `0.0.0.0` behind a Docker-network-only host firewall rule.
- Launcher → Head uses Head's container bind `0.0.0.0:9800`, published as host loopback only.
- Both directions use HMAC-SHA256 with timestamp, request ID, body hash, skew check, and replay cache; secret-file handling and redaction are specified.
- `/v1/update` has versioned schemas, asynchronous/idempotent admission, busy behavior, persisted deduplication, 2s/5s timeouts, and bounded jittered retries. `/v1/health` is authenticated liveness-only; post-update success additionally requires its reported version to exactly equal the admitted target.

## P0.3 — Drain and update completion protocol (resolved)

- Canonical contract: `contracts/drain_status.md`, cross-referenced from `containers/head.md` §3–§6, `containers/bot/discord_bot.md` §6.3a/§6.4/§6.5, `containers/ai_worker/ai_worker.md` §4–§6/§12, `containers/mosquitto.md` §4/§5, and `containers/bot/commands/quick-battle.md` §4.
- "Drained" is the full user-workflow, not just RabbitMQ `ai_tasks`: `Bot`'s `in_flight_workflows` counter is a strict superset of the existing task map, also covering open `/quick-battle` lobbies, collectors, and votes that never touch RabbitMQ.
- `Bot` publishes `status/bot/drain_progress` (QoS 1, not retained, `BOT_DRAIN_PROGRESS_INTERVAL_SEC` default `5`) while draining; `Head` watches this instead of inferring completion from empty queues, and transitions `DRAINING` → `UPDATING` on `in_flight_workflows == 0` or `HEAD_DRAIN_TIMEOUT_SEC`, whichever comes first.
- Drain timeout always escalates to the existing hard-stop sequence (never silent abandonment) — resolving the previous "abandon work" vs. "hard-stop only for loss-of-internet" inconsistency — and now explicitly includes cancelling open lobby/collector/vote views with a localized "update in progress, please retry" notice, not just RabbitMQ purge/revoke.
- `AI Worker` finishes its current claim, stops consuming, and publishes `status/ai_worker/pause_ack` once idle after a pause request — informational/diagnostic only, never a second blocking condition on drain completion. Recovery from an abandoned/superseded drain is a plain resume publish, no teardown needed.
- `update_available` broadcasts are deduplicated by `target_version`; a different-version broadcast received mid-cycle is queued and acted on only after the current drain/update cycle completes. Manual `launcher update --version` is unaffected (already idempotent via `contracts/launcher_ipc.md`).
- Every wire contract carries `schema_version` with a uniform reject-unknown-version rule; `Web` must tolerate one prior/one following schema version of any shared document, additive-only within that window — no cluster-wide update barrier is introduced.

## P0.4 — RabbitMQ/Celery wire design (resolved)

- Canonical contract: `contracts/ai_task.md` §2/§3 (wire/dispatch), §8 (cancellation matrix), §9 (dead-letter policy), §10 (schema versioning); cross-referenced from `containers/rabbitmq.md` §1–§3/§6/§9/§13, `containers/ai_worker/ai_worker.md` §1/§4–§6, `containers/bot/discord_bot.md` §3–§6.5, and `architecture.md`'s `RabbitMQ`/design-boundary sections.
- Native Celery task protocol: `Bot` dispatches via `apply_async(kwargs={"envelope": {...}}, task_id=task_id, queue="ai_tasks")`; `AI Worker` is a real Celery worker at prefetch = 1. `task_id` = the Celery task id = the AMQP `correlation_id` = the domain idempotency key — one identifier, four names.
- `ai_tasks_results` stays a custom, manually-published queue with auto-ack on `Bot`'s side; Celery's own result backend (`AsyncResult`) is never used. Publisher confirms are enabled on both publish paths.
- Delivery guarantee corrected project-wide from "exactly-once" to **at-least-once delivery, effectively-once outcome** — manual ack after result-publish bounds duplicate generation, and discard-by-unknown-`task_id` makes the outcome effectively-once.
- A full cancellation matrix now covers user-abort, stall timeout, overall timeout, drain timeout, and hard-stop, with `Bot` confirmed as the sole actor for every `revoke` call (resolving the previous hard-stop-authorship ambiguity).
- Both `ai_tasks` and `ai_tasks_results` are dead-letter-configured; malformed/unknown-`schema_version` messages are nacked-without-requeue to a shared `dead_letter` queue instead of retried forever.
- Broker-side plaintext exposure (guild API keys in-flight and in RabbitMQ's volume) is explicitly accepted, consistent with the existing Cosmos/transit trade-off, with defined volume/retention scope. Non-`guest` broker credentials (`RABBITMQ_DEFAULT_USER`/`PASS`/`VHOST`, per-consumer `<SERVICE>_RABBITMQ_USER`/`PASS`) are defined in `rabbitmq.md` §3/§13.

## P0.5 — Cross-service data contracts (resolved)

- Canonical contracts: `contracts/suggestion.md`, `contracts/telemetry.md`, `contracts/status_document.md`, `contracts/battle_archive.md`, `contracts/log_archive.md`; schema-evolution rule extended in `contracts/drain_status.md` §5 (P0.5.5).
- **Suggestions (P0.5.1):** Cosmos DB `AZURE_COSMOS_DATABASE` default `DiscordCombatAI`, container `Suggestions`, partition `/guild_id`. Cosmos `id` = UUID; separate unique `ticket_uid` = `SUG-{8 uppercase hex}`. Full ticket + `notification_status` claim machine (`pending`→`claiming`→`sent`/`failed`); Queue message carries both `id` and `ticket_uid`; visibility 60s; poison after 5 dequeues; sweep min age `BOT_SUGGESTION_SWEEP_MIN_AGE_SEC` default 600. Propagated to `azure.md`, `discord_bot.md` §6.6, `suggest.md`, `suggestions.md`.
- **Suggestion schema enrichment (post-P0.5.1, owner-approved):** tickets match or exceed legacy `generic/suggestions.json` richness — `submitter`, structured `locale`, `guild_snapshot`, `context`, `conversation[]` (replaces thin `responses[]`); catalog is `{value, label}` on `status_document`; no runtime read of legacy flat JSON (operator migrates with a custom script). Canonical: `contracts/suggestion.md` (`schema_version` = 2).
- **Telemetry (P0.5.2):** Table `AZURE_METRICS_TABLE` / `NodeMetrics`; PK `node_id`; leader-only upload; CPU/RAM/uptime/errors from Head; latency/guild_count from Bot Mosquitto heartbeat into Table; Dashboard “now” latency/guilds from `status.py` (not Cosmos count); history from Table; 30-day retention.
- **Status document (P0.5.3):** `contracts/status_document.md` — Web seeds/edits `identity` + `suggestion_catalog` (`list[{value,label}]`); Bot owns only `status`; missing blob → create defaults; ETag RMW ≤5 retries; labeled catalog seed lists documented.
- **Battle archive (P0.5.4):** Bot-owned; `battle-results` container; `{guild_id}/{yyyy}/{mm}/{task_id}.txt` + `.meta.json`; unbounded retention v1; best-effort after Discord-ready.
- **Log archive (P0.5.6):** Escaping rules + sensitive policy; append path `logs/{node_id}/{yyyy}/{mm}/{dd}.log`; buffer caps; at-least-once duplicate lines acceptable v1.
- Propagated through `azure.md`, `head.md`, `architecture.md`, Web/Bot page docs, and `Readme.md`.

## P0.6 — Azure Web PubSub live-data flow (resolved)

- Canonical: `contracts/pubsub_live.md` (+ live payload in `contracts/telemetry.md` §5).
- Groups: `cluster` (existing) and `dashboard-live` (`HEAD_PUBSUB_DASHBOARD_GROUP`). Leader always streams every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC`; **no** listener detection / subscribe-event path.
- Browser connects; Web only negotiates `{ url, expires_at, group }` with join/leave-only roles, TTL 60m; auth = Entra Bearer + admin group (`contracts/web_auth.md`, closed under P0.7); PubSub `user id` = Entra `oid`.
- Free_F1 budget documented (~17k msgs/day with 1 viewer + cluster heartbeats; empty-group stream ≈0 outbound). Caps on live log batch size.
- Removed contradictory “Web listens to PubSub” / conditional streaming language from `architecture.md`, `head.md`, `web.md`, `dashboard.md`.

## P0.7 — Web administrative security boundary (resolved)

- Canonical contract: `contracts/web_auth.md`; env vars mirrored in `containers/web/web.md` §3; middleware summary in `web.md` §6.2.
- **Entra ID (single-tenant) + admin security group** is the v1 boundary. MSAL.js Authorization Code + PKCE in the browser; `Authorization: Bearer` on all `/api/*`; FastAPI validates JWT (issuer, audience, JWKS, `tid`, expiry) then requires `WEB_ENTRA_ADMIN_GROUP_ID ∈ token.groups`. Static SPA shell public. Unauthenticated → **401**; authenticated non-admin → **403**. No cookie session / no BFF / no CSRF token for v1.
- Primary group path: emit security group claims in the access token; keep the admin group small. Group-overage Graph fallback is **P2** — overage without `groups` → **403** until ops fixes emit-groups or group size.
- Web **may** be internet-reachable; Entra + group is the security boundary (VPN optional hardening only).
- Audit: suggestion respond/done persists `acted_by_oid` / `acted_by_upn` / `acted_at` (`contracts/suggestion.md`); webhook broadcasts write durable Blob audit under `admin-audit` (`web_auth.md` §7); status identity/catalog writes log actor `oid`.
- Broadcast safety: UI `ConfirmDialog` for ALL; `Idempotency-Key` on send/update; ALL cooldown `WEB_WEBHOOK_ALL_COOLDOWN_SEC` default 60; SELECTED rate `WEB_WEBHOOK_SELECTED_RATE_PER_MIN` default 10.
- Webhook SSRF: Discord-host HTTPS allowlist only; no redirects; Web re-validates before POST; Bot `/config` validates on save. List/detail APIs never return raw `webhook_url` / `api_key` / Azure secrets / PubSub connection strings; negotiate may return short-lived client URL to authenticated admins only.
- Propagated through `architecture.md`, `web.md`, all `web/pages/*.md`, `components.md`, `pubsub_live.md`, `suggestion.md`, `guild_config.md`, `status_document.md`, `config.md`, `azure.md` (SP vs Entra distinction), `Readme.md`.

## P0.8 — End-to-end scenarios and acceptance invariants (resolved)

- Canonical set: `docs/scenarios/` (index: `docs/scenarios/Readme.md`).
- Architecture acceptance cases (not test-code prescriptions): preconditions, ordered steps, durable writes, timeouts, user-visible result, invariant checked.
- Coverage includes cold boot, follower failover, leader Head crash, Mosquitto/RabbitMQ/Azure partial outages, planned update happy path / drain timeout / rollback, Bot or AI Worker restart mid-task, Quick Battle success/abort/timeout (P1.1 product rules apply where numbers are open), suggestion duplicate/lost Queue, Web auth + all-guild broadcast.
- Each scenario references existing contracts (`leadership_control`, `drain_status`, `ai_task`, `suggestion`, `web_auth`, `launcher_ipc`, `pubsub_live`, etc.) rather than inventing new behavior.
- S05 updated with concrete Bot/AI Worker reconnect and user-visible publish-failure outcomes (P1.5).

## P1.5 — Broker configuration and outage behavior (resolved)

- Canonical: `containers/rabbitmq.md`, `containers/mosquitto.md`, `architecture.md` → Target Compose skeleton; S05.
- **RabbitMQ:** Compose-internal plaintext AMQP accepted (no host `5672`/`15672` in prod); target `infra/rabbitmq/`; plugins `rabbitmq_event_exchange` + internal `rabbitmq_management`; image `rabbitmq:3.13-management`; healthcheck `rabbitmq-diagnostics check_running`; client reconnect 1s→×2→60s+jitter; confirm wait 5s; Bot publish failure → no TaskRecord + localized error; AI Worker reconnects and never acks without result; queue-depth metrics **not** v1 telemetry; manual image-pin upgrades.
- **Mosquitto:** target `infra/mosquitto/mosquitto.conf` (anonymous, persistence false, no host publish); image `eclipse-mosquitto:2.0`; QoS 0 for logs/progress/heartbeats, QoS 1 for control/drain; heartbeats never retained; Head republishes retained desired modes on reconnect (no grant backlog); healthcheck via `mosquitto_sub`; broker self-logs = `docker logs` only; broker metrics not v1.
- Propagated to `discord_bot.md`, `ai_worker.md`, `head.md`, S05.

## P1.7 — Azure client failure classification and provisioning (resolved)

- Canonical: `containers/azure.md` §3a/§6/§6a/§9/§11.
- Exact RBAC roles/scopes per Head/Bot/Web SP; provisioning instructions live in this contract, target IaC `infra/azure/` when created (live apply remains P2 ops).
- Permanent auth/permission vs transient classification; SDK `max_retries = 0` so only application backoff retries; Head must not infinitely back off permanent auth failures.
- Per-client timeouts + ≤3 transient attempts (PubSub ≤2); Bot Queue poll skip + degraded after 3 consecutive failures; `/config` Apply keeps staged state (`config.md` §12).
- Per-resource health flags preferred over a single `azure_connected`.
- Propagated to `config.md`, `discord_bot.md`, cleanup note on Queue enqueue recovery already aligned with `suggestion.md`.

## P1.9 — Localization value contract (resolved)

- Canonical: `contracts/localization.md` §3–§4; `contracts/guild_config.md` `language` field; `config.md` LanguageSelect; graph input notes in `environment.md` / `battle.md`; Bot publish mapping in `discord_bot.md` §6.2.
- Stored enum: `en` \| `es` \| `ua`. `ua` is a legacy UI key (not ISO `uk`).
- Bot maps to AI `language_locale`: `en`→`en`, `es`→`es`, `ua`→`uk-UA`.
- UI fallback: exact file → primary subtag → `en`.

## P1.3 (partial) — Guild config decisions needed by shared Azure clients (resolved)

- Canonical: `contracts/guild_config.md` §4a/§7; `azure.md` `guilds.py` note; `discord_bot.md` §6.2; `guilds.md` list filter.
- Field-scoped Cosmos Patch + ETag (≤5 on 412); soft-delete confirmed unbounded v1; rejoin preserves `created_at` + admin fields; default lists exclude `left_at != null`.
- **Still open under P1.3:** offline removal sweep, Apply atomicity for invalid key/model, model-catalog pagination/25-cap, command recovery beyond Cosmos Apply already specified.

## Phase 1 Slice 0 — Launcher/Head coordination reconciliation (resolved)

- `Head` implementation location is `src/head/`.
- Canonical release tags use the exact Docker-safe SemVer-compatible grammar in `contracts/launcher_ipc.md` §4. GitHub drafts are always ignored; automatic polling ignores prereleases by default and compares parsed SemVer precedence.
- Compose injects required `APPLICATION_VERSION` into `head`, `bot`, and `ai_worker`. Launcher maps one admitted version to the fixed local image set under `LAUNCHER_GHCR_NAMESPACE`; brokers and independently deployed Web are outside this recreate set.
- Go Docker API owns daemon ping and authenticated pulls. A controlled, no-shell Docker Compose CLI invocation owns recreation.
- HTTP and mutating CLI operations share one coordinator, persisted state, and host-wide cross-process lock. Restarted active work is marked `INTERRUPTED` and requires explicit reconciliation/new admission; it is never blindly resumed.
- Verification is authenticated Head liveness plus exact target version, not leadership/Bot/dependency readiness.
- Failed recreate/verification receives one automatic rollback attempt. Manual `launcher rollback` is a separate admission.
- Canonical docs: `containers/Launcher.md`, `containers/head.md`, `contracts/launcher_ipc.md`, `contracts/drain_status.md`, `architecture.md`, and S06–S09.

## P1.8 — Observability and health semantics (resolved)

- Canonical: `contracts/telemetry.md` §2/§3/§5, propagated to `head.md`, `bot/discord_bot.md`, `ai_worker/ai_worker.md`, `mosquitto.md`, and `pubsub_live.md`.
- Bot and AI Worker heartbeat schemas are exact and versioned. Both default to 30-second cadence; Head uses a 90-second monotonic receipt-time staleness threshold.
- Dependency health is proportionate: Bot reports Gateway plus latest RabbitMQ/Cosmos/Queue/status-Blob state; AI Worker reports RabbitMQ and deliberately does not synthesize a global Gemini probe.
- Head buffers at most 60 completed one-minute metrics windows, uploads oldest-first after recovery, and drops the oldest window on overflow. Buffer loss on Head restart is accepted and operator-visible by warning.
- Live payloads keep the fixed envelope/metrics, include at most 50 newest log lines, and are capped at 65,536 UTF-8 JSON bytes; oldest selected logs are omitted first and counted in `logs_dropped`.
- Candidate Bot/AI Worker business/quality counters without a v1 transport or consumer are explicitly deferred, not left as unrouteable metrics.

---

# P0 — Global architecture and contract blockers

---

**P0.3–P0.8 are resolved — see the "Resolved decisions" section above.** There are **no remaining unresolved P0 items**. Remaining blockers are P1/P2.

## P0.5 Cross-service data contracts — resolved

→ See **Resolved decisions → P0.5**. Canonical paths listed there; do not re-open ownership/schema questions without an explicit owner decision.

## P0.6 Azure Web PubSub live-data flow — resolved

→ See **Resolved decisions → P0.6**. Canonical: `contracts/pubsub_live.md`.

## P0.7 Web administrative security boundary — resolved

→ See **Resolved decisions → P0.7**. Canonical: `contracts/web_auth.md`.

## P0.8 End-to-end scenarios and acceptance invariants — resolved

→ See **Resolved decisions → P0.8**. Canonical: `docs/scenarios/`.

---

# P1 — Subsystem blockers

## P1.1 Quick Battle session behavior and Discord constraints

**Evidence:** `bot/commands/quick-battle.md` §3–§14; `bot/discord_bot.md` §6.4; `ai_worker/graphs/battle.md` §9/§12.

Resolve before implementing `/quick-battle`:

1. Approval threshold rounding.
2. Revision-round cap and exhaustion result (force-proceed vs. abort).
3. Deadlines and outcomes for lobby, environment input, votes, and fighter input.
4. Maximum participants, one active lobby policy (per guild/channel/user), and concurrent-invocation behavior.
5. Participant snapshot and behavior when someone leaves the lobby, guild, or becomes unavailable after start.
6. Input length/content limits and Gemini context/cost budget.
7. Output delivery when story/UI content exceeds Discord limits (chunking, attachment, or truncation).
8. Abort/cancellation behavior in every phase, including already-running AI tasks and late results.
9. Cooldown/rate limit and cost-abuse policy.
10. Remove or constrain the documented `@everyone` ping. Define `allowed_mentions` and missing-permission behavior.
11. Static arena ownership: Bot cannot read a directory mounted only in AI Worker. Choose Bot image data, shared package data, or an explicit worker task. Also define how a raw `.txt` arena becomes the battle graph's required `Environment {description, tags, setting}` object.
12. Long-flow Discord messaging: interaction tokens cannot be “re-fetched.” Specify that the initial interaction and every component/modal interaction are acknowledged on time, and whether later phase messages use Bot-authenticated channel sends/message edits rather than an expired original follow-up token.
13. Winner identity: duplicate display names make nickname matching ambiguous. Require structured winner IDs from the model and validate that every ID belongs to input fighters; define invalid/empty/multiple-winner handling.
14. Enabled-guild guard: define the exact `enabled`/non-empty key/model validation before any task is published and the localized response when the guard fails.
15. Persist or explicitly abandon active lobby/collector/vote state on Bot restart. This is broader than the task-map result problem because a lobby may not have published an AI task yet.
16. Track the currently expected environment/battle `task_id` per lobby and define handling for late or superseded revision-round results.
17. Define the durable Discord delivery reference stored for each task (channel/thread/message IDs vs. interaction token) so timeout and hard-stop notifications target a surface that is still usable.
18. Bound progress-container edit cadence. The independent Duration refresh plus phase changes must respect Discord rate limits when many tasks are active.

Also correct the step-number inconsistencies in §6 (fighter collection is step 6, not step 7).

**Not a v1 blocker:** `random_winner_mode` origin while the command hardcodes it to `false`; `EnvironmentApprovalView` promotion to shared UI.

---

## P1.2 AI graph state and bounded-generation contracts

**Evidence:** `ai_worker/nodes.md` §1–§6; `ai_worker/graphs/environment.md` §2/§5–§12; `ai_worker/graphs/battle.md` §2/§5–§12; `ai_worker/prompts.md`.

Resolve before graph implementation:

1. Exact structured output schema and validation/coercion policy for every LLM-backed node.
2. `AttemptRecord.validator_verdict` cannot be required when a candidate is appended before Validator runs. Make it optional/null until validation or change the append timing.
3. Define `attempts_used` consistently as total candidates/attempts; current “Enhancer/Modifier passes including the first pass” wording is false for Generator/storyteller attempt #0.
4. Add a maximum episode count/token/cost bound. A minimum of 2 with no maximum allows unbounded calls and can violate task timeouts/Discord output limits.
5. Define `Predefine` invalid-output handling and allowed `outcome_type`/winner cardinalities, including the explicit `episode_count < BATTLE_MIN_EPISODES` path.
6. Define deterministic winner-ID validation/fallback; nickname-to-ID matching is not safe.
7. Confirm whether Validator enforces scripted winner consistency, even if scripted mode is deferred.
8. Define the behavioral rubrics for Validator/Decider in both graphs. Prompt wording is implementation work; the acceptance criteria are architecture.
9. Decide Decider scaling behavior if the candidate pool can exceed one prompt's context (single call vs. bounded tournament), or prove the pool is strictly bounded.
10. Reconcile maximum graph cost/latency with `AI_WORKER_PROGRESS_HEARTBEAT_SEC`, `BOT_AI_TASK_STALL_TIMEOUT_SEC`, and `BOT_AI_TASK_TIMEOUT_SEC`; confirm defaults only after the bounded worst case is defined.

Prompt file authoring and physical migration remain implementation tasks once these behavioral contracts are fixed.

---

## P1.3 Guild configuration lifecycle and concurrency

**Evidence:** `contracts/guild_config.md` §3–§8; `bot/discord_bot.md` §6.2; `bot/commands/config.md` §6/§9/§12.

**Resolved for shared Azure clients (this revision):**

1. ~~Field-scoped Cosmos PATCH + ETag/concurrency~~ — **resolved:** `guild_config.md` §4a.
2. ~~Rejoin semantics~~ — **resolved:** `guild_config.md` §7.
4. ~~Whether lists/counts exclude `left_at != null`~~ — **resolved:** default exclude; `guild_config.md` §7 / `guilds.md`.
7. ~~Webhook URL validation~~ — **resolved (P0.7).**
8. ~~Soft-delete policy / retention~~ — **resolved:** soft-delete confirmed, unbounded v1 (`guild_config.md` §7).
   `/config` Cosmos Apply user-visible recovery — **resolved with P1.7:** `config.md` §12.

**Still resolve before full `/config` + guild lifecycle implementation (not required for Phase 0 Azure clients):**

3. Detection of guild removals missed while Bot was offline; iterating only current `bot.guilds` cannot mark absent documents left.
5. Behavior when guild document creation failed but a command arrives (beyond Apply error already specified).
6. Apply semantics when a staged API key/model is invalid: which fields commit atomically, whether the invalid key is stored, and what happens to the previously valid model/key.
9. Model-catalog timeout/pagination and Discord's 25-option Select limit, without blocking the async Discord event loop.

The exact reconciliation scheduling algorithm is implementation detail after its correctness semantics are fixed.

---

## P1.4 Suggestion delivery correctness

Depends on `contracts/suggestion.md` from P0.5.1 — **contract exists**; remaining items are implementation-edge refinements:

1. ~~Atomic claim preventing queue poller and sweep from sending the same DM~~ — **specified** in `contracts/suggestion.md` §3; verify in code when implementing.
2. Azure Queue at-least-once delivery, duplicate events, visibility renewal — poison/delete rules are in the contract; visibility *renewal* mid-DM still an implementation detail if DM can exceed 60s.
3. Attempt-count persistence and retry/backoff semantics — attempts + max are specified; exponential backoff between retries is optional/implementation.
4. Multiple/concurrent admin response behavior and idempotency of `/respond`.
5. DM invocation locale/guild fields and auto-feedback fallback — structured `LocaleInfo` exists on the ticket; resolution order for edge cases still to nail.
6. Terminal `failed` recovery/retry by an administrator.
7. Pagination/continuation tokens for suggestion list and conversation history.
8. ~~Shared catalog bootstrap/ownership~~ — **resolved P0.5.3**; catalog is `{value, label}`; cache/fallback on read failure still open.
9. ~~Sweep minimum pending age~~ — **resolved:** `BOT_SUGGESTION_SWEEP_MIN_AGE_SEC` default 600.
10. Queue polling failure behavior: define whether Bot skips a cycle, how it backs off, which health/status field becomes stale or degraded, and how recovery resumes without duplicating delivery.

---

## P1.5 Broker configuration and outage behavior — resolved

→ See **Resolved decisions → P1.5**. Canonical: `rabbitmq.md`, `mosquitto.md`, Compose skeleton in `architecture.md`, S05.

---

## P1.6 Web API behavior and operational safety

**Evidence:** `web.md`; all `web/pages/*.md`.

Resolve before Web implementation:

1. Exact request/response/error schemas (prefer one OpenAPI/Pydantic source of truth), status codes, validation limits, and pagination.
2. `/api/metrics` and history fields after the telemetry contract is fixed; omit unsupported fields rather than fabricate/null them inconsistently.
   Define whether stale retained values are visibly marked when either metrics request fails.
3. PubSub negotiate errors/token expiry/reconnect behavior (auth boundary itself is **resolved P0.7** / `contracts/web_auth.md`).
4. ~~Idempotency and confirmation for broadcasts~~ — **resolved (P0.7)** for webhook send/update; suggestion mutation concurrency/idempotency remains P1.4.
5. Webhook broadcast concurrency limits, timeout, retry policy, and result schema (rate/cooldown/idempotency/audit **resolved P0.7**; remaining: Discord POST timeout/retry detail).
6. Health/readiness contract and deployment target/rollout/rollback mechanism.
7. Web logging destination and retention. Web self-metrics are optional unless selected for v1 operations.
8. Version/changelog ownership: decide whether update announcements are merely content or are tied to the actual deployed release. Define the version source and persistence, or remove those endpoint fields for v1.

The charting library, CSS token system, and live-vs-polling choice for compact charts do not block backend/API implementation.

---

## P1.7 Azure client failure classification and resource provisioning contract — resolved

→ See **Resolved decisions → P1.7**. Canonical: `containers/azure.md` §3a/§6/§6a/§9/§11.

---

## P1.8 Observability and health semantics — resolved

→ See **Resolved decisions → P1.8**. Canonical heartbeat, staleness, metrics-buffer, and live-payload rules: `contracts/telemetry.md`. Launcher verification: `contracts/launcher_ipc.md` §5.

---

## P1.9 Localization value contract — resolved

→ See **Resolved decisions → P1.9**. Canonical: `contracts/localization.md` §3–§4.

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

# Required documentation cleanup

These edits are mechanical after the decisions above; they should be completed before declaring readiness:

1. ~~Replace every RabbitMQ "exactly-once" claim with the chosen at-least-once/effectively-once semantics~~ — **done, P0.4**.
2. ~~Remove stale `reply_to`-based routing text where `correlation_id` is canonical~~ — **done, P0.4**.
3. Remove references to unwritten `ai_worker/graphs/quick-battle.md`; `bot/commands/quick-battle.md` already defines sequencing.
4. Correct `environment.md`'s old `prompts/core/generic_environments` path to the target `prompts/static/generic_environments` path, then resolve which container owns it.
5. Correct `quick-battle.md` step numbers.
6. ~~Correct `home.md`'s implication that status updates maintain identity~~ — **done, P0.5.3**.
7. ~~Reconcile Dashboard guild-count and latency-history sources~~ — **done, P0.5.2**.
8. ~~Update `Readme.md` after new contracts~~ — **done for P0.5/P0.6/P0.7 contracts and P0.8 scenarios**.
9. Ensure source-tree comments match the chosen IPC/config files and service ownership.
10. Remove resolved/open-item prose from component docs once its canonical decision is recorded, instead of leaving “resolved” tombstones indefinitely.
11. ~~Reconcile `task_progress.md`'s `queued` publisher: Bot creates that phase locally; an AI Worker cannot report a task while it is still waiting unclaimed in RabbitMQ.~~ — **done (Phase 0 Slice 0):** `task_progress.md` §3/§4 and `mosquitto.md` §4.
12. Remove stale wording that Web PubSub group presence is used for leader election; it is only heartbeat transport after the Blob Lease redesign.
13. ~~Correct `Readme.md` and `web/pages/home.md` claims that Bot status writes resolve/maintain identity~~ — **done, P0.5.3**.
14. Correct `bot/commands/suggest.md` §5's claim that the `WizardView` correction is unapplied; its own §14 and `visuals.md` say it was applied.
15. Reconcile the high-level project tree with detailed service trees (notably Web home/PubSub routes and `status.py`'s obsolete version comment).
16. ~~Remove conditional live streaming / “Web listens to PubSub” / unnamed dashboard group~~ — **done, P0.6**.
17. ~~Remove “auth deferred / no auth / Auth Placeholder / not publicly safe until P0.7”~~ — **done, P0.7**.
18. Remove `ai_worker.md` §9's dangling “see next row for the open question” reference; the next row describes crash/redelivery and `contracts/ai_task.md` already fixes publish-before-ack behavior.
19. Replace `bot/visuals.md`'s stale statement that the suggestion catalog is “moving off” a hardcoded list; `contracts/status_document.md` already owns it.
20. Reconcile `web/pages/home.md`'s documented `version` response with the canonical status document, which deliberately excludes version. Until P1.6 chooses a source, mark the field unavailable or remove it from the v1 response.
21. Remove or update stale self-marked “resolved” prose, including the old `task_progress.md` open-item tombstone, after its canonical text is corrected.
22. ~~Correct `azure.md` and `web.md` Queue-failure wording: a failed Web enqueue after the Cosmos write is recoverable through the canonical pending-ticket sweep~~ — **done with P1.7 / `azure.md` §9**; poller skip/degraded behavior also closed there.
23. Update `web/pages/template.md`'s stale “eventual authenticated admin” wording; Entra admin authorization is resolved in P0.7 and applies to all `/api/*` routes.

**Verified repository fact:** there are no duplicate slash/backslash variants of docs files in Git; the earlier duplicate-path concern was a Windows path-rendering artifact and is closed.

---

# Suggested resolution order

1. ~~Drain/update completion protocol (P0.3)~~ and ~~RabbitMQ/Celery executable wire design (P0.4)~~ — **resolved**; leadership fencing and Launcher transport were already resolved.
2. ~~**Suggestion, telemetry, status, archive, and schema-evolution contracts** (P0.5)~~ — **resolved**.
3. ~~**Web PubSub topology** (P0.6)~~ and ~~**Web security boundary** (P0.7)~~ — **resolved**.
4. ~~**End-to-end scenarios** (P0.8)~~ — **resolved** (`docs/scenarios/`) + cleanup pass.
5. **Quick Battle + AI graph bounded behavior** (P1.1–P1.2). Locale mapping is done (P1.9).
6. **Remaining guild/suggestion concurrency** (leftover P1.3 items, P1.4).
7. **Web details** (P1.6). Observability (P1.8), brokers (P1.5), and Azure clients (P1.7) are done.

When all P0 items and the P1 items for a target subsystem are closed, that subsystem's docs can be considered ready for implementation. “All docs ready” requires every P0 and P1 item to be either resolved or explicitly removed from v1 scope with affected fields/endpoints/flows deleted from the specification.

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

Build these in parallel around a stub graph so the message path can be tested before LLM behavior:

1. **AI Worker shell:** Celery task registration/prefetch → pause/resume and heartbeat → progress publishing → manual result publishing with publish-before-ack → redelivery/cancellation behavior.
2. **Bot core:** inactive startup and leadership watchdog → guild reconciliation/status heartbeat → Celery dispatch/result consumer/task tracker → progress rendering plumbing → drain counter and hard-stop cancellation → shared Components V2 UI base.

**Gate:** a dummy `ai_task` must complete Bot → RabbitMQ → AI Worker → result/progress → Bot. Then validate the transport portions of S04, S05, and S10. P1.5 must define standalone RabbitMQ recovery before S05 can pass faithfully.

## Phase 3 — Configuration and suggestions vertical slices

1. Close remaining P1.3 leftovers if needed for production `/config`, plus P1.9 (**done**) before treating config as complete; use `/config` to establish valid guild/key/model data before any real AI command.
2. Implement `/suggest` ticket creation and Bot delivery claim/sweep together with Web's Suggestions response path; validate S12.

These slices give useful functionality without depending on the unresolved battle graphs. `/config` and suggestion work can proceed in parallel after their shared Cosmos/Queue services exist.

## Phase 4 — AI graphs

The AI Worker container cannot be completed in one pass:

1. Close P1.2 and P1.9.
2. Implement shared LLM retry, Validator, Decider, and refiner-node contracts.
3. Implement the `environment` graph and its structured-output tests.
4. Implement the `battle` graph, bounded episode loop, winner-ID validation, and timeout/cost tests.

Do not replace missing rubrics, bounds, or malformed-output behavior with ad hoc code defaults. Prompt text may be tuned later, but graph acceptance behavior must be fixed first.

## Phase 5 — `/quick-battle`

The Bot container's flagship command must be split into reviewable subtasks:

1. command guards, options, and drain admission;
2. lobby ownership, participant snapshot, timeout, and restart policy;
3. generic-arena ownership plus `.txt` → `Environment` conversion;
4. custom environment collection and environment-graph task;
5. approval/revision consensus loop;
6. fighter collection and validation;
7. battle-graph task, bounded progress edits, result delivery, and archive;
8. abort/timeout/hard-stop behavior across every phase.

**Gate:** close P1.1, P1.2, and P1.9 before implementing the affected subtask; validate all S11 invariants at completion.

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

- After Phase 0 models exist, broker setup, Azure clients, Launcher, Web auth shell, and early Head work can proceed in parallel.
- After the transport shell exists, Bot core and AI Worker graph work can proceed in parallel; `/quick-battle` waits for both real graphs.
- Head telemetry can proceed in parallel with `/config` and `/suggest`; Dashboard/Performance waits for telemetry.
- The recommended critical path to the first complete battle is: foundation/contracts → brokers → minimal Head fencing → Bot/AI Worker transport → `/config` → graph contracts and graphs → `/quick-battle`.
- Final architecture acceptance requires S01–S13 plus closure or explicit v1 removal of every remaining applicable P1 item. P2 items do not block the first implementation.
