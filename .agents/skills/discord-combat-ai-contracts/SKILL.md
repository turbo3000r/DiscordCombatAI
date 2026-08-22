---
name: discord-combat-ai-contracts
description: Implements and changes DiscordCombatAI shared models derived from docs/contracts/, including schema_version handling, identifiers, timestamps, serialization tests, and producer/consumer compatibility checks. Use when adding or editing contract models, shared envelopes, persisted document schemas, or contract test suites under src/shared/models or equivalent.
---

# DiscordCombatAI Contracts

Use together with `discord-combat-ai-implementation`. This skill specializes shared contract-model work; it does not replace the umbrella workflow.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read the specific file(s) under `docs/contracts/` that own the slice.
3. Read only the producer/consumer docs that implement those contracts (container, command, page, or scenario docs as needed).
4. Check `docs/to_resolve.md` for applicable P1 gates that change schema, identifiers, timestamps, versioning, or compatibility.
5. Inspect existing shared model modules before adding parallel types.

Do not copy contract schemas into this skill or into skill reference files. Canonical field lists, enums, and state machines live in `docs/contracts/`.

## Hard requirements

Require:

- one canonical model per documented contract;
- strict schema-version handling;
- UTC timestamp and identifier validation;
- serialization round-trip tests;
- unknown-version rejection tests;
- additive compatibility where documented;
- no transport or service logic inside contract models;
- propagation checks across all documented producers and consumers.

Reference the docs instead of copying their schemas.

## Model rules

- Create one canonical model (or tightly related model set) per contract document. Do not fork duplicate shapes in Head/Bot/Web/AI Worker packages.
- Models define data shape, validation, and serialize/deserialize behavior only.
- Keep RabbitMQ publish/ack, Mosquitto topics, Azure SDK calls, Discord API calls, Celery dispatch, retries, and business workflows out of contract models.
- Prefer shared primitives for `schema_version`, UTC timestamps, and identifiers rather than re-implementing them per file.
- Match documented nullability, defaults, enums, and optional fields exactly. Do not invent fields "for convenience."

## Versioning and compatibility

- Enforce `schema_version` exactly as the owning contract specifies.
- Reject unknown or unsupported versions in tests and parsing paths.
- Support additive compatibility only where documentation requires it (for example Web tolerating one prior/one following shared-document version). Do not invent broader compatibility.
- When a contract changes, update the single model and every documented producer/consumer path in the same slice or explicitly list deferred consumers as blockers.

## Identifiers and timestamps

- Validate identifier formats required by the contract (UUIDs, `SUG-` ticket UIDs, opaque leadership terms, task IDs, and so on).
- Treat timestamps as UTC. Reject naive or ambiguous local times unless a contract explicitly allows another form.
- Keep idempotency keys and correlation identifiers aligned with the owning contract's unification rules.

## Testing

For every changed contract model, include:

- serialization round-trip tests (object → JSON/wire → object);
- unknown-version rejection tests;
- invalid identifier and invalid timestamp tests where the contract constrains them;
- additive-compatibility tests only when the contract documents that window;
- fixtures that exercise required vs optional fields and documented enums.

Do not require live brokers or Azure for contract-model unit tests.

## Propagation checks

Before closing a slice:

1. List every documented producer and consumer of the changed contract.
2. Confirm each still references the same canonical model and version rules.
3. Confirm no service-local duplicate schema was introduced.
4. Confirm sensitive fields remain redacted at log/API boundaries owned by consumers (models must not log secrets).

If a producer/consumer cannot yet adopt the model, stop and report that gap rather than embedding a second schema.

## Change boundaries

- Do not rewrite contract behavior in this skill's absence of a docs change; if the docs are wrong or incomplete, request a documentation decision.
- Do not widen schemas to absorb transport metadata that belongs in RabbitMQ headers, MQTT topics, or HTTP envelopes unless the contract already defines it.
- Keep changes small: one contract family per slice when practical.

## Completion criteria

A slice is complete only when:

- one canonical model owns the documented contract shape;
- schema-version, UTC timestamp, and identifier rules are enforced and tested;
- round-trip and unknown-version tests pass;
- additive compatibility is tested only where documented;
- models contain no transport or service logic;
- documented producers and consumers are checked for propagation;
- handoff names the next contract or consumer integration slice.
