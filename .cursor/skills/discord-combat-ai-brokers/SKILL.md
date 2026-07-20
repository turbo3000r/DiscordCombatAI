---
name: discord-combat-ai-brokers
description: Implements and changes DiscordCombatAI broker infrastructure for RabbitMQ, Celery transport, Mosquitto, Docker Compose, healthchecks, networking, volumes, and broker integration tests. Use when configuring queues, DLX, MQTT topics, broker credentials, Compose services, reconnect behavior, or transport-level broker tests.
disable-model-invocation: true
---

# DiscordCombatAI Brokers

Use together with `discord-combat-ai-implementation`. This skill specializes broker and Compose work; it does not replace the umbrella workflow.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read `docs/containers/rabbitmq.md` and `docs/containers/mosquitto.md` for the requested slice.
3. Read the owning contracts for transport behavior (`ai_task`, `task_progress`, `leadership_control`, `drain_status`, and related contracts as applicable).
4. Read Compose / architecture sections that define service topology, networks, volumes, and prompt mounts.
5. Check `docs/to_resolve.md` for applicable P1.5 gates and related cleanup items.
6. Inspect existing broker configs, Compose files, and transport helpers before adding parallel definitions.

Do not invent queue names, MQTT topics, QoS, retention, credentials, or outage behavior that the docs leave unresolved. Stop and report the missing decision.

## Hard requirements

Require:

- local-only exposure unless explicitly documented otherwise;
- durable RabbitMQ queues, DLX, publisher confirms, and prefetch rules from the contracts;
- Mosquitto QoS/retention behavior from canonical contracts;
- non-guest credentials;
- healthchecks and startup ordering;
- bounded reconnect behavior;
- no claim of exactly-once delivery;
- tests for restart, duplicate delivery, malformed messages, and unavailable brokers.

## RabbitMQ and Celery transport

- Follow `contracts/ai_task.md` for dispatch identity, ack ordering, result publishing, cancellation, and dead-letter policy.
- Keep `ai_tasks` and `ai_tasks_results` durable and dead-letter-configured as documented.
- Enable publisher confirms on documented publish paths.
- Enforce prefetch rules from the worker contract (prefetch = 1 unless documentation changes).
- Use non-`guest` broker credentials and documented vhost/user variables.
- Treat delivery as at-least-once with effectively-once outcomes. Never claim exactly-once delivery.
- Keep Celery protocol usage aligned with the contract: app `ai_worker.celery_app`, task name `ai_worker.tasks.run_graph`, custom result queue; do not silently switch to Celery result backends or Redis.
- Treat `infra/rabbitmq/definitions.json` as the topology owner; clients verify only.
- Compose must inject `RABBITMQ_DEFAULT_VHOST` and host `NODE_ID` into Bot/AI Worker/Head as documented.

## Mosquitto

- Keep the broker local-only unless a canonical doc explicitly changes that.
- Apply QoS and retention exactly as leadership/drain/progress/heartbeat contracts specify.
- Do not retain activation grants or other short-lived control messages that contracts mark non-retained.
- Define observability for broker failures as documented; do not leave the control broker silently unobservable when the docs require a path.

## Compose, networking, and volumes

- Expose only documented ports and bind modes (for example loopback-only or Docker-network-only where specified).
- Wire healthchecks and startup ordering so dependents wait for ready brokers.
- Mount configs, secrets, and prompt volumes according to the target architecture docs.
- Keep broker data volumes and restart policies explicit.
- Mark target-only paths clearly if the tree is still docs-ahead-of-code.

## Reconnect and failure behavior

- Implement bounded reconnect/backoff for Bot, AI Worker, Head, and other documented clients.
- Do not invent infinite retry or silent drop policies for unresolved outage cases.
- Surface unavailable-broker behavior to callers according to contracts and container docs.
- Preserve publish-before-ack and discard-unknown-`task_id` invariants where documented.

## Testing

Include focused tests or integration checks for:

- broker restart and client recovery within bounded reconnect rules;
- duplicate delivery / redelivery outcomes;
- malformed or unknown-`schema_version` messages (nack-without-requeue / DLQ where documented);
- unavailable broker on publish or consume;
- healthcheck pass/fail and Compose dependency ordering where practical.

Prefer hermetic local broker tests over cloud dependencies. Do not require live Azure for broker-transport unit/integration slices.

## Change boundaries

- Do not redefine wire schemas inside broker config; own them in contract models.
- Do not expand network exposure, enable management UI remotely, or weaken credentials for local convenience unless documentation is updated first.
- Do not implement Head/Bot/AI Worker business logic in broker config files.
- Keep Compose changes reviewable: brokers and wiring first, application containers only when the slice requires them.

## Completion criteria

A slice is complete only when:

- exposure remains local-only unless docs say otherwise;
- durable queues, DLX, confirms, prefetch, QoS, and retention match contracts;
- non-guest credentials and healthchecks/startup ordering are in place;
- reconnect behavior is bounded and tested or explicitly gated on an unresolved P1.5 item;
- no exactly-once claim appears in code or docs introduced by the slice;
- restart, duplicate, malformed, and unavailable-broker tests cover the changed path;
- handoff names the next broker or consumer integration slice.
