# DiscordCombatAI — Documentation Index

This README is the entry point to the project's architecture and documentation. **Read this first** before consulting any other file — it explains what each document covers and where to find the answer to a given question.

---

## How this documentation is organized

```
docs/
├── architecture.md          # High-level system overview — start here for the big picture
│
├── containers/              # One detailed technical doc per service/container
│   ├── template.md          # Canonical 12-section schema — check before drafting a new doc
│   ├── head.md
│   ├── Launcher.md
│   ├── ai_worker/              
│   |   ├── ai_worker.md      # Container doc: Celery mechanics, task routing, retry policy, health, scaling
│   |   ├── nodes.md          # Shared LangGraph nodes (Validator, Decider, the `refiner` pattern) — src/ai_worker/nodes/
│   |   ├── prompts.md        # Prompt system: target file structure, injection pattern, per-node mapping — prompts/
│   |   └── graphs/           # One file per internal Langgraph graph, named after the graph's OWN internal
│   |       │                 # name — NOT the user-facing command (see naming note below, revised)
│   |       ├── template.md      # Schema for documenting a LangGraph graph — check before drafting a new one
│   |       ├── battle.md        # Episode-based battle narration graph (planner → storyteller → refiner → finishing)
│   |       └── environment.md   # Environment generation/revision graph (Generator/Normalise → Validator/Enhancer loop → Decider)
│   ├── rabbitmq.md
│   ├── mosquitto.md
│   ├── azure.md             # Shared Azure library doc — not a container, see its own header note
│   ├── bot/                 # Bot is the largest and most complex container —
│   │   │                    # it gets its own sub-structure instead of a single file
│   │   ├── discord_bot.md   # Drafted — Bot's container-level doc (12-section schema): activation lifecycle,
│   │   │                    # guild onboarding, AI task pipeline plumbing, drain/stop signals, background services

│   │   ├── visuals.md       # Architecture decisions (Components V2, custom UI layer) + proposed design system
│   │   │                    # CONFIRMED; shared component catalog still CANDIDATE ONLY — see its own §0/§3-4
│   │   └── commands/        # One file per slash command — command-specific flow & logic
│   │       ├── template.md      # Schema for documenting one command — check before drafting a new one
│   │       ├── config.md        # Drafted — rewritten from scratch, legacy is reference only (see its own header note)
│   │       ├── quick-battle.md  # Drafted — rewritten from scratch, legacy is reference only (see its own header note)
│   │       └── suggest.md       # Drafted — rewritten from scratch, legacy is reference only (see its own header note)
│   │       # NOTE: legacy /ping is confirmed DROPPED (project owner) — not carried into this
│   │       # architecture, deliberately has no doc and no entry here.
│   └── web/                 # Web is the second-largest, most user-facing container after Bot, and gets the
│       │                    # same "own folder" treatment for the same reason: a single web.md would have to
│       │                    # cram frontend build/integration decisions, shared UI, AND six pages' worth of
│       │                    # per-page endpoint contracts into one file. Structure directly mirrors bot/'s split.
│       ├── web.md           # Container-level doc (12-section schema): FastAPI+React single-container build,
│       │                    # frontend/backend integration model (direct-to-Web-PubSub live data), Azure deps
│       ├── components.md    # Shared frontend UI pieces reused across ≥2 pages — mirrors bot/visuals.md
│       └── pages/           # One file per dashboard page — mirrors bot/commands/, pairs a page's UI with
│           │                # the specific backend endpoints it owns, instead of a separate all-routes doc
│           ├── template.md  # Schema for documenting one page — check before drafting a new one
│           ├── home.md
│           ├── dashboard.md
│           ├── performance.md
│           ├── guilds.md
│           ├── suggestions.md
│           └── webhook.md
│
├── contracts/                # Cross-service message/data format contracts.
│                              # Lives here, not inside any single service doc, because both
│                              # sides of a communication channel must agree on the same source
│                              # of truth (e.g. the exact JSON shape of an ai_task message,
│                              # the structure of a Cosmos DB suggestion document).
│   ├── ai_task.md            # RabbitMQ ai_tasks/ai_tasks_results envelope + result schema
│   ├── task_progress.md      # AI task phase-update contract (Mosquitto progress/ai_worker/<task_id>)
│   ├── leadership_control.md # Blob-Lease-derived Bot grants, desired mode, acknowledgements, leader heartbeat
│   ├── launcher_ipc.md       # Authenticated cross-host/container Head ↔ Launcher HTTP contract
│   ├── drain_status.md       # Drain completion, pause_ack, update broadcast dedup, schema evolution
│   ├── localization.md       # Guild locale contract: Bot UI strings + AI-content `language_locale`
│   ├── guild_config.md       # Guild Cosmos DB document schema — Bot writes, Web reads
│   ├── suggestion.md         # Suggestions Cosmos + Queue notification + atomic claim (P0.5.1)
│   ├── telemetry.md          # Table metrics + live telemetry_live payload (P0.5.2)
│   ├── status_document.md    # Shared Blob status JSON — section owners + seed (P0.5.3)
│   ├── battle_archive.md     # Battle story Blob archive (P0.5.4)
│   ├── log_archive.md        # Structured log format + Blob append archive (P0.5.6)
│   ├── pubsub_live.md        # dashboard-live group, negotiate, always-stream, Free_F1 (P0.6)
│   └── web_auth.md           # Entra ID Web admin auth boundary (P0.7)
│
└── scenarios/                 # End-to-end architecture acceptance cases (P0.8)
                                # that cross multiple services. Link into contracts/
                                # and container docs rather than inventing behavior.
    ├── Readme.md              # Index + purpose
    ├── 01_cold_boot_no_leader.md
    ├── 02_follower_race_failover.md
    ├── 03_leader_head_crash_bot_alive.md
    ├── 04_mosquitto_partial_outage.md
    ├── 05_rabbitmq_partial_outage.md
    ├── 06_azure_partial_outage.md
    ├── 07_planned_update_happy_path.md
    ├── 08_planned_update_drain_timeout.md
    ├── 09_planned_update_rollback.md
    ├── 10_bot_or_ai_worker_restart_mid_task.md
    ├── 11_quick_battle_success_abort_timeout.md
    ├── 12_suggestion_duplicate_or_lost_queue.md
    └── 13_web_auth_and_all_guild_broadcast.md
```

---

## Where to look depending on what you're discussing


| If the topic is...                                                                               | Go to...                                                                                    |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| Overall system design, tech stack, container breakdown, data storage, why a decision was made    | `architecture.md`                                                                           |
| How a specific service behaves internally (env vars, state machine, failure modes, health check) | `containers/<service>.md`                                                                   |
| Drafting a new service doc from scratch                                                          | `containers/template.md` (follow the schema exactly)                                        |
| Bot-specific behavior not tied to one command (client setup, shared UI)                          | `containers/bot/discord_bot.md` or `containers/bot/visuals.md`                              |
| One specific slash command's flow                                                                | `containers/bot/commands/<command>.md`                                                      |
| Drafting a new slash command doc from scratch                                                    | `containers/bot/commands/template.md` (follow the schema exactly)                           |
| Web's container-level design (single-container build, frontend/backend integration, Azure deps)  | `containers/web/web.md`                                                                     |
| Web-specific behavior not tied to one page (shared nav, charts, list/detail layout)               | `containers/web/components.md`                                                              |
| One specific dashboard page's UI + the backend endpoints it owns                                  | `containers/web/pages/<page>.md`                                                             |
| Drafting a new dashboard page doc from scratch                                                    | `containers/web/pages/template.md` (follow the schema exactly)                              |
| How a specific internal LangGraph graph is structured (nodes, state, retry loops, diagram)        | `containers/ai_worker/graphs/<name>.md` (see `graphs/template.md` for the schema)            |
| A LangGraph node/pattern reused by more than one graph (e.g. `Validator`, `Decider`, the `refiner` loop) | `containers/ai_worker/nodes.md` — don't redefine a shared node's contract inside a single graph's own doc |
| The prompt system: which file a node uses, the injection pattern, the legacy → target rewrite mapping | `containers/ai_worker/prompts.md` — don't re-cite exact prompt filenames inside a single graph's own doc, link here instead |
| The exact JSON schema of the `ai_tasks`/`ai_tasks_results` RabbitMQ messages, ack/durability/idempotency semantics | `contracts/ai_task.md` |
| How Head leadership fences Bot activation, including heartbeat/grant/demotion behavior | `contracts/leadership_control.md` |
| How Head and host Launcher reach/authenticate each other and exchange update/health messages | `contracts/launcher_ipc.md` |
| Suggestion tickets, Queue notifications, and atomic DM claim | `contracts/suggestion.md` |
| Table metrics, live `telemetry_live` payload, snapshot vs history | `contracts/telemetry.md` |
| Shared status Blob (identity / status / suggestion_catalog) | `contracts/status_document.md` |
| Battle result Blob archive | `contracts/battle_archive.md` |
| Operational log line format + Blob append archive | `contracts/log_archive.md` |
| Live dashboard PubSub groups, negotiate, always-stream budget | `contracts/pubsub_live.md` |
| Web admin authentication (Entra ID, Bearer JWT, admin group, webhook SSRF/audit) | `contracts/web_auth.md` |
| Where a guild's language/locale setting comes from, and what it drives (Bot UI vs. AI-generated content) | `contracts/localization.md` |
| The exact Cosmos DB document schema for a guild's config (admin-set fields + Discord-sourced metadata), and who writes which field | `contracts/guild_config.md` |
| The exact format of a message passed between two services | `contracts/` — check first; remaining gaps are tracked in `to_resolve.md` |
| A full request flow spanning multiple services (architecture acceptance cases) | `scenarios/` — index in `scenarios/Readme.md` |


---

## Notes for whoever (human or AI) is reading this next

- **Files marked "NOT YET WRITTEN" above do not exist yet.** Don't infer their contents from the architecture overview alone — if a question requires detail at that level, say so explicitly and treat it as an open item, rather than guessing.
- `containers/template.md` defines the required structure for any new service doc. Don't invent a different structure for a new service.
- `containers/ai_worker/graphs/template.md` defines an analogous but distinct schema for documenting a single LangGraph graph (state, nodes, control flow, diagram) — it is not the service template. Don't reuse `containers/template.md` for a graph doc, and don't invent a third structure; follow `graphs/template.md`.
- `bot/` is intentionally more granular than other containers because it is the largest and most user-facing service. New slash commands get their own file under `bot/commands/`, not a section inside `discord_bot.md`.
- **`web/` follows the exact same granularity pattern as `bot/`, deliberately.** `web.md` = container-level (build/deploy, frontend↔backend integration model, Azure deps — the 12-section schema), `components.md` = shared frontend UI pieces reused across ≥2 pages (the `visuals.md` equivalent), `pages/*.md` = one file per dashboard page, pairing that page's UI with the specific backend endpoints it owns (the `commands/*.md` equivalent — a page's endpoints live with its own doc, not in a separate all-routes file). New dashboard pages get their own file under `pages/`, following `pages/template.md`, not a section inside `web.md`.
- **`Web` is a single container**, not a frontend/backend pair — one Dockerfile, one multi-stage build (compile the React frontend, then serve it from the same FastAPI process that serves the API). "Standalone" for `Web` means decoupled from the local Docker Compose cluster (no `Bot`/`Head`/`RabbitMQ`/`Mosquitto` access, per `architecture.md`), not frontend deployed separately from backend — see `web.md` §1/§2 for the reasoning.
- ~~Several `web/pages/*.md` docs depend on decisions that belong to `bot/discord_bot.md`, which is still an unwritten stub~~ — **resolved: `bot/discord_bot.md` is now drafted.** Guild metadata for the Guilds page, bot status/latency for the Dashboard page, and bot identity for the Home page (`version` excluded, see below) are all resolved from `Bot`'s side now — `web.md` §6.2 and each affected page's own §9/§10 were revisited and updated in the same pass, not left as stale "open item" notes.
- **Implementation-readiness pass (this revision) — six confirmed corrections, all project owner:**
  - **Leader election is now a real mutex, not an assumption.** Azure Web PubSub group membership is additive, not exclusive (confirmed against Microsoft's own documentation) — the previous "only one join durably persists" claim in `head.md`/`architecture.md` was factually wrong, not just an accepted edge case. An **Azure Blob Lease** is now the actual mutual-exclusion primitive; Web PubSub is repurposed as a cheap, always-joined, push-based broadcast (leader heartbeat + update-available), keeping request volume unchanged from before. See `architecture.md`'s High-Level Architecture note and `head.md` §3/§6/§9.
  - **RabbitMQ locality wording corrected.** `architecture.md` previously implied a non-leader node's `AI Worker` could "process tasks for the active cluster leader" — this was always wrong given RabbitMQ's node-local design; corrected to state the worker simply has no tasks to pull. Same fix applied to `control/ai_worker/pause`/`resume`, which is node-local, not cluster-wide (`mosquitto.md`, `architecture.md`).
  - **Gemini credentials are per-guild on the task message, not a global `AI Worker` env var.** `ai_worker.md` previously declared one required global `GEMINI_API_KEY`, which contradicted `/config`'s per-guild key/model selection ever reaching the worker. Fixed: `api_key`/`model` now travel on the `ai_tasks` message itself (`ai_worker.md` §1/§3/§4, both graph docs' §2). Plaintext-in-transit/at-rest remains an accepted, documented risk — Key Vault was evaluated and rejected as incompatible with this project's cheap, self-hosted, user-supplied-key cost model.
  - **Per-service Azure identities, not one shared Service Principal.** `azure.md` §3 now issues `Head`/`Bot`/`Web` each their own least-privilege Service Principal instead of one shared identity with system-wide access — a free correction (App Registrations cost nothing) that reduces blast radius independently of the plaintext-key decision above.
  - **Hard-stop sequencing confirmed.** Retained `control/bot/desired_state = stopped` (or autonomous grant/watchdog expiry) runs: purge `ai_tasks`, terminate in-flight `AI Worker` execution, notify affected Discord threads, then disconnect the Gateway. See `contracts/leadership_control.md` and `bot/discord_bot.md` §6.5.
  - **Update propagation now reaches followers.** Previously only the leader's own `Launcher` ever got the update signal. Since every `Head` is now permanently joined to the same broadcast group used for the leader heartbeat, `update_available` reaches every node, and each follower's `Head` signals its own local `Launcher` directly (no drain needed, since a follower has no active `Bot`) — see `architecture.md` Scenario 5, `head.md` §6/§12.
  - **`Web` authentication is Entra ID + admin group (P0.7).** MSAL.js PKCE in the browser; Bearer JWT on all `/api/*`; static SPA shell public. Web may be internet-reachable — Entra is the security boundary, not private-network-only. Canonical: `contracts/web_auth.md`.
- **Implementation-readiness pass, round 2 (this revision) — three more resolved findings:**
  - **`contracts/ai_task.md` is new** — the `ai_tasks`/`ai_tasks_results` RabbitMQ envelope was previously scattered across `rabbitmq.md`, `ai_worker.md`, and both graph docs with no single schema anyone could implement against. It now defines the envelope/result shapes, persistence, acknowledgements, correlation, timeout, and dedup behavior. Each node uses its own local queues, so the contract does not depend on strict cluster-wide single-Bot activity; remaining Celery/wire issues are tracked under P0.4.
  - **`status.py`'s document is canonical in `contracts/status_document.md` (P0.5.3)** — nested `identity` / `status` / `suggestion_catalog`, Web seeds+edits identity/catalog, Bot owns only `status` via periodic push. ETag RMW + bootstrap defaults are specified there; `azure.md` §2 links rather than duplicating.
  - **Suggestion notification delivery is self-healing with atomic claim (P0.5.1)** — `contracts/suggestion.md` defines `pending`→`claiming`→`sent`/`failed`, Queue schema, and sweep min-age. Queue delete only after terminal delivery state.
- When a design decision is already documented somewhere in this tree, treat it as settled — build on top of it rather than re-litigating it, unless the project owner explicitly reopens the question.
- **"NOT YET WRITTEN" covers two distinct states, both meaning "don't infer content from here":** a path that doesn't exist on disk at all, and a file that exists but is an intentionally empty stub (e.g. `bot/commands/template.md`'s future siblings, `web/pages/performance.md` if it's ever split further). Both are tagged the same way in the tree above — treat either as "not yet written," never as "written but short."
- **Naming convention: docs vs. source folder names for the battle feature (revised, scoped rule).** This used to be one blanket rule ("docs always match the user-facing name"); it's now split by location, since a single graph doc lives in a folder whose *only* neighbors are other graphs, while a command doc lives in a folder whose *only* neighbors are other commands:
  - **`ai_worker/graphs/*.md` is named after the graph's own internal name**, matching `architecture.md`'s source tree 1:1 (`ai_worker/graphs/battle/` → `graphs/battle.md`, `ai_worker/graphs/environment/` → `graphs/environment.md`). `battle.md` was renamed from `quick-battle.md` (project owner) specifically to restore this 1:1 match — the old name was the inconsistent one, not this one.
  - **`bot/commands/*.md` stays named after the user-facing slash command** (`bot/commands/battle/` → `bot/commands/quick-battle.md`), since that folder is about commands, not internal graphs.
  - Keep this scoped version of the convention for any future command whose internal folder name differs from its slash-command name — don't rename `bot/commands/quick-battle.md` to match, and don't rename a future `ai_worker/graphs/*.md` to match its command instead of its graph.
- **Azure environment variables are defined exactly once, in `containers/azure.md`.** `azure.md` documents `src/shared/azure/` — an internal library, not a container — and is the single source of truth for every `AZURE_*` variable (Service Principal auth + per-resource endpoints). Any other container doc that depends on an Azure resource (e.g. `head.md`) must state *which* resource(s) it depends on and link to `azure.md` § 3 for the variable definitions — it must **not** redefine, re-list, or duplicate the variables itself. `head.md` was corrected to follow this convention; `ai_worker.md` follows it too (by having *no* Azure dependency at all, confirmed in its own §10); `web/web.md` and `bot/discord_bot.md` (§3 there, now drafted) both follow it as well.
- **`azure.md`'s dependency tables are a living document, not a one-time snapshot.** When `web/web.md` was written, it surfaced two gaps in `azure.md` that were corrected in the same pass rather than left silently mismatched: `Web`'s `table.py` (Table Storage) access was missing despite `architecture.md`'s Scenario 4 already implying it, and `pubsub.py` needed a `get_client_access_token` capability documented for the direct-browser-to-Web-PubSub design (`web.md` §6.1). Both are marked as "Addition applied in this revision" in `azure.md` §4/§5 — the same pattern used elsewhere in this tree for correcting an earlier doc once a later one exposes a gap in it.
- **`bot/visuals.md` §1 now holds two confirmed, project-wide architecture decisions that ripple elsewhere:** Components V2 (`discord.py`'s `ui.LayoutView`/`Container`) is the default building block for new Bot UI, which is *why* `architecture.md`'s Technology Stack table now pins `discord.py ≥ 2.6` instead of `≥ 2.3`; and reusable UI is a custom internal layer under `bot/modules/UI/`, deliberately not a third-party framework dependency (two candidates — `pycascadeui`/CascadeUI and `dpy-layout-builder` — were evaluated and rejected, reasons recorded there, not repeated here). Don't relitigate either decision inside a command doc; `commands/*.md` §4 should just state which model a given piece uses. `visuals.md`'s design system (§2) and shared component catalog (§3), by contrast, are explicitly *not* confirmed yet — still proposals/candidates, per that file's own status note.
- **The same "define once, link elsewhere" convention now also applies to `AI_WORKER_LLM_MAX_RETRIES`, in `containers/ai_worker/ai_worker.md` § 3** — the graph-agnostic LLM retry budget shared by every LangGraph node across `environment` and `battle`. Graph-*specific* tunables (`ENVIRONMENT_MAX_ENHANCER_RETRIES`, `BATTLE_MAX_MODIFIER_RETRIES`, etc.) stay in their own graph docs permanently — only the graph-agnostic one lives in `ai_worker.md`.
- **All three `bot/commands/*.md` docs are now drafted** (`quick-battle.md`, `config.md`, `suggest.md`) — `bot/visuals.md` §3's shared component catalog was updated to match: most rows now point at a real, drafted consumer instead of a stub, and the `WizardView` row was corrected (renamed `SuggestionView`) after drafting `suggest.md` revealed it described a sequential multi-step flow that command doesn't actually implement — it's really one view with simultaneous selects plus a single modal-trigger button. `config.md` also introduces one genuinely new component, `ModelSelect` (a live-populated Select sourced from Google's own model-listing API, replacing legacy's free-text Model field), not present anywhere in `visuals.md`'s pre-existing candidate inventory.
- **`/suggest`'s type/category list lives on `contracts/status_document.md`'s `suggestion_catalog`** as `list[{value, label}]` — Web seeds (legacy 5 types / 8 categories with labels) and may edit; Bot and Web both read. Ticket schema (UUID `id`, `SUG-` `ticket_uid`, conversation thread, submitter/locale snapshots): `contracts/suggestion.md`.
- **`bot/discord_bot.md` is now drafted, closing out the `bot/` container.** New decisions from this pass include guild config in `contracts/guild_config.md`; guild reconciliation; Bot status heartbeat/cloud snapshot; suggestion-response DM behavior; and per-command drain gating. Leadership control is now superseded and canonicalized by `contracts/leadership_control.md`: retained safe mode (`inactive`/`draining`/`stopped`) plus non-retained short-lived activation grants. The task-progress status bar is split between plumbing in `discord_bot.md` §6.3 and rendering in `bot/visuals.md` §3.1.
- **End-to-end scenarios (P0.8) live under `docs/scenarios/`** — architecture acceptance cases with preconditions, ordered steps, durable writes, timeouts, user-visible results, and invariants. See `scenarios/Readme.md`.
- **Implementation sequencing lives in `to_resolve.md` → “Suggested implementation sequence.”** It separates documentation-decision prerequisites from the dependency-ordered code phases and explicitly splits `Head`, `Launcher`, `AI Worker`, `Bot`, `/quick-battle`, and `Web` into smaller implementation slices.
- **Phase 0 doc prerequisites (P1.5, P1.7, P1.9, and the P1.3 Azure-client subset) are resolved** — see `to_resolve.md` Resolved decisions. Brokers, shared Azure clients, locale enum/mapping, and guild Patch/soft-delete helpers are implementation-ready; leftover P1.3/P1.1/P1.2/P1.4/P1.6 remain intentionally deferred.
- **Phase 1 Slice 0 documentation reconciliation is resolved.** `Head` lives at `src/head/`; coordinated tags/version injection, fixed Launcher image mapping, Docker-API-vs-Compose ownership, shared CLI/HTTP coordinator lock, interrupted-operation handling, exact-version liveness verification, one-shot automatic rollback, same-term Blob-renew recovery, and P1.8 heartbeat/buffer/live-cap defaults are canonical in `to_resolve.md`, `contracts/launcher_ipc.md`, `contracts/telemetry.md`, and the linked container/scenario docs. P1.8 is no longer deferred.
- **Phase 2 documentation is resolved.** Transport shell (`graph="environment"` canned path, no `stub` graph), host `NODE_ID` → `HEAD_NODE_ID`/`BOT_NODE_ID`/`AI_WORKER_NODE_ID`, Celery app `ai_worker.celery_app` + task `ai_worker.tasks.run_graph`, definitions-owned RabbitMQ topology, Bot asyncio vs Celery/MQTT concurrency, Phase 2 Bot scope (no slash commands), and S03/S04/S05/S07/S08/S10 acceptance ownership matrices are canonical in `to_resolve.md` and the linked contracts/container/scenario docs. Project skills: `discord-combat-ai-bot`, `discord-combat-ai-ai-worker`, `discord-combat-ai-web`.

