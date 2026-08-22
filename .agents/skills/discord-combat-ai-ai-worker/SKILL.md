---
name: discord-combat-ai-ai-worker
description: Implements and changes the DiscordCombatAI AI Worker Celery container, including celery_app/tasks registration, RabbitMQ consume/result publish, Mosquitto progress/pause/heartbeat, transport-shell mode, and later LangGraph environment/battle graphs. Use when working under src/ai_worker/, AI Worker Docker/Compose wiring, Celery prefetch/ack policy, or worker-side scenario coverage for S05 and S10.
---

# DiscordCombatAI AI Worker

Use together with `discord-combat-ai-implementation`. This skill specializes the AI Worker container; it does not replace the umbrella workflow.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read `docs/containers/ai_worker/ai_worker.md`.
3. Read owning contracts: `ai_task` (including §11 transport shell), `task_progress`, `drain_status` (pause_ack), `telemetry`, and graph docs only when implementing real graphs.
4. Read `docs/containers/rabbitmq.md` for topology ownership, reconnect, confirms, and prefetch.
5. Read only the scenarios the slice claims (typically S05, S07 pause path, S08 revoke interaction, S10).
6. Check `docs/to_resolve.md` for Phase 2 gates and P1.2 graph blockers.
7. Inspect existing `src/ai_worker/`, shared models/messaging constants, and Compose AI Worker wiring before adding parallel modules.

Do not invent a `stub` graph discriminator, Celery result backends, Redis brokers, or competing queue topology. Stop and report unresolved P1.2 decisions before implementing real LLM graph behavior.

## Phase-aware scope

### Phase 2 (transport shell)

In scope:

- `ai_worker.celery_app:app` and registered task `ai_worker.tasks.run_graph`;
- prefetch = 1, `task_acks_late=True`, JSON only, publisher confirms;
- manual `ai_tasks_results` publish before ack;
- Mosquitto progress (`launching`→`composing`→`refining`→`finishing`), pause/resume, heartbeat;
- `AI_WORKER_TRANSPORT_SHELL=true` canned `graph="environment"` success result from `contracts/ai_task.md` §11;
- redelivery and revoke-interrupt behavior.

Out of scope:

- real LangGraph `environment`/`battle` execution and Gemini calls (Phase 4 / P1.2);
- Bot Gateway, Discord UI, Web;
- creating RabbitMQ topology that conflicts with `infra/rabbitmq/definitions.json`.

### Later phases

- Phase 4: shared nodes + real graphs after P1.2.
- Keep transport-shell flag default `false` in production images.

## Hard requirements

Require:

- Celery worker CLI `celery -A ai_worker.celery_app worker -Q ai_tasks`;
- task name exactly `ai_worker.tasks.run_graph`;
- broker URL with URL-encoded `RABBITMQ_DEFAULT_VHOST`;
- `AI_WORKER_NODE_ID` matches host `NODE_ID` (grammar `^[A-Za-z0-9._-]+$`, 1–128);
- definitions.json is topology owner — verify only;
- publish-before-ack; never use Celery result backends;
- progress must never block task completion when Mosquitto is down;
- transport shell uses `graph="environment"` only — no `stub` graph;
- never log per-task `api_key`.

## Change boundaries

- Do not implement Bot fencing, Head election, Launcher, or Web inside the worker.
- Do not add Redis as broker or result backend.
- Do not silently replace canned transport-shell behavior with ad hoc LLM calls in Phase 2.
- Do not invent graph acceptance rubrics while P1.2 remains open.

## Testing and completion criteria

Include focused tests for:

- envelope validation and unknown `schema_version` → DLQ path;
- transport-shell progress sequence + canned result shape;
- publish-before-ack and crash/redelivery;
- pause finishes current claim then `pause_ack`;
- heartbeat schema and RabbitMQ dependency flag;
- revoke/terminate interrupt during a running task.

Mark S05/S10 steps per scenario Phase 2 ownership matrices. Prefer hermetic broker tests; no live Gemini for Phase 2.

A slice is complete only when:

- Phase 2 transport gate (Bot → RabbitMQ → Worker → progress → result → Bot) is demonstrable via harness;
- applicable docs gates are closed or explicitly out of scope;
- focused tests and static checks pass;
- handoff names the next worker graph slice or Bot integration slice.
