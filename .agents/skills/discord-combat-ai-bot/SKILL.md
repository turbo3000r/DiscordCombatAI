---
name: discord-combat-ai-bot
description: Implements and changes the DiscordCombatAI Python Bot container, including Gateway lifecycle under leadership grants, guild sync, Celery AI-task dispatch/result transport, task tracking, MQTT control/progress, drain/hard-stop, ProcessCommand, /config, /suggest, Components V2, localization, suggestion Queue poller/sweep/DM delivery, and Bot-side S03–S05, S07–S08, S10, S12 coverage. Use when working under src/bot/, Bot Docker/Compose wiring, discord.py integration, or Bot-side scenario coverage.
disable-model-invocation: true
---

# DiscordCombatAI Bot

Use together with `discord-combat-ai-implementation`. This skill specializes the Bot container; it does not replace the umbrella workflow.

**Whenever discord.py code is planned, written, reviewed, or debugged, also load and follow the global `discord-py-api` skill.** Bot concurrency and Gateway decisions must stay compatible with discord.py’s asyncio model.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read `docs/containers/bot/discord_bot.md` (especially §6.0 concurrency, §6.4 ProcessCommand, §6.6 suggestion delivery).
3. Read owning contracts for the slice: `leadership_control`, `ai_task`, `task_progress`, `drain_status`, `telemetry`, `guild_config`, `suggestion`, `localization`, `local_development`, and command docs only when the slice includes them.
4. Read only the scenarios the slice claims (Phase 2: S03–S05, S07–S08, S10; Phase 3: S12 plus command surfaces).
5. Check `docs/to_resolve.md` for the Phase 3 gate and remaining deferred P1 items (P1.1/P1.2 offline-removal, etc.).
6. Inspect existing `src/bot/`, shared models/messaging, Compose Bot wiring, and Head grant simulators before adding parallel modules.

Do not invent `/quick-battle` lobby recovery or real AI graphs while those decisions remain deferred. Stop and report the missing decision.

## Phase-aware scope

### Phase 2 (transport + core runtime)

In scope:

- inactive startup; grant/watchdog fencing; Gateway connect/disconnect;
- guild join/update/remove + periodic metadata sync (no `/config` UI);
- Celery `send_task("ai_worker.tasks.run_graph", ...)` dispatch + `ai_tasks_results` consumer + task map + progress plumbing;
- heartbeat + status Blob push;
- drain progress and hard-stop (purge/revoke/synthetic failures);
- harness-triggered transport-shell tasks only (`contracts/ai_task.md` §11).

Out of scope for Phase 2-only slices: user-facing slash commands (land in Phase 3).

### Phase 3 (`/config` + `/suggest` vertical slice)

**Own explicitly:**

- `ProcessCommand` per `discord_bot.md` §6.4 (ack ownership, guild/enabled/permissions/drain, prod vs dev filtering, typed guild load, ephemeral denials, localization, logging, exception classes, modal/autocomplete compatibility);
- `/config` and `/suggest` command modules + Components V2 UIs (`config.md`, `suggest.md`, `visuals.md`);
- Bot localization required by those two commands (`contracts/localization.md`);
- Google `google-genai` list + Apply probe **off the event loop** (`asyncio.to_thread`);
- production suggestion Queue poller, reconciliation sweep, claim timeout recovery, Discord DM delivery (`suggestion.md`, `discord_bot.md` §6.6);
- S12 acceptance ownership (with Web respond path);
- local-development side-effect suppression (no Queue/DM; development guild-only; reject DMs) per `local_development.md`.

**Out of Phase 3:** real AI graphs; `/quick-battle`; offline guild-removal sweep; Web pages beyond Suggestions coupling.

### Later phases

- Phase 5: `/quick-battle` after P1.1/P1.2 and real graphs.

## Hard requirements

Require:

- asyncio event loop owns discord.py and Bot domain state mutations;
- blocking Celery/Kombu I/O on dedicated threads — never on the event loop;
- blocking Google SDK calls via `asyncio.to_thread` (or equivalent) — never on the event loop;
- MQTT (paho) callbacks only enqueue work onto the loop;
- thread-safe handoff via `call_soon_threadsafe` / `run_coroutine_threadsafe` (or an asyncio queue);
- `BOT_NODE_ID` matches host `NODE_ID` / peer service node ids (grammar `^[A-Za-z0-9._-]+$`, 1–128);
- broker URL includes URL-encoded `RABBITMQ_DEFAULT_VHOST`;
- task name `ai_worker.tasks.run_graph`; JSON serialization; publisher confirms; Bot sole `revoke`/purge actor;
- at-least-once delivery with effectively-once outcome by `task_id` for AI tasks;
- suggestion DM delivery documented as at-least-once with possible bounded duplicate after crash-after-DM — do not claim exactly-once;
- no legacy developer permission bypass;
- documented shutdown order: stop new AI work → hard-stop/drain path → cancel timers → stop MQTT → stop consumer/Celery → close Gateway → exit.

## Change boundaries

- Do not implement AI Worker graphs, Head election, Launcher, or Web APIs inside Bot.
- Do not widen Azure roles or log `DISCORD_BOT_TOKEN` / guild `api_key` / Google probe bodies.
- Do not invent durable orphan-task reconciliation beyond contracts.
- Do not register `/quick-battle` until its phase/P1 gates close.
- Keep Components V2 / shared UI limited to what the slice needs; follow `bot/visuals.md` when adding UI.

## Testing and completion criteria

Include focused tests for:

- grant accept/reject, expiry hard-stop, MQTT disconnect soft-stop;
- publish failure creates no `TaskRecord`; result discard for unknown `task_id`;
- progress phase handling and stall/overall timers;
- drain progress and hard-stop purge/revoke;
- concurrency: broker/MQTT/Google threads do not mutate state without loop handoff;
- ProcessCommand denials (permission, guild, drain, runtime filter);
- `/config` ensure_active + Apply partial Patch + model list truncate/probe mapping;
- `/suggest` catalog fail-closed + create failure without ticket UID;
- Queue claim race, sweep min-age, claim timeout, failed-notification path coupling to S12;
- development: DM rejected; Queue/DM suppressed.

Mark scenario steps `complete`, `integration-only`, or `deferred` per `docs/scenarios/` ownership matrices.

A slice is complete only when:

- applicable Phase 2/3 gates are closed or explicitly out of scope;
- focused tests and static checks pass;
- secrets remain redacted;
- handoff names the next Bot, AI Worker, Web, or command-phase slice.
