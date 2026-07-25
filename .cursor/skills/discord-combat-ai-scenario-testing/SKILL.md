---
name: discord-combat-ai-scenario-testing
description: Designs and implements DiscordCombatAI acceptance and integration tests that use deterministic clocks, fault injection, protocol simulators, and explicit complete-versus-deferred scenario reporting. Use when writing Phase 1+ convergence suites, S01–S13 harnesses, multi-process Head/Launcher tests, or simulated Bot/AI Worker peers for incomplete later phases.
disable-model-invocation: true
---

# DiscordCombatAI Scenario Testing

Use together with `discord-combat-ai-implementation`. This skill specializes acceptance/integration harnesses; it does not replace the umbrella workflow or product implementation skills.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read the specific files under `docs/scenarios/` for the claimed scenario IDs.
3. Read only the contracts and container docs those scenarios name as canonical.
4. Check `docs/to_resolve.md` for gates that make a scenario step incomplete or deferred.
5. Inspect existing `tests/acceptance/`, `tests/integration/`, fakes, and fixtures before adding a parallel harness.

Do not invent scenario outcomes that contradict the owning contract. If a scenario and contract conflict, stop and report the documentation decision.

## Hard requirements

Require:

- scenario IDs and invariants taken from `docs/scenarios/`;
- deterministic clocks for grant, heartbeat, drain, and verification timing;
- fault injection for broker, Azure, IPC, and process failures;
- protocol simulators for components not yet implemented in the current phase;
- explicit labeling of complete versus deferred scenario steps;
- no claim that a scenario fully passes when required later-phase behavior is simulated;
- hermetic defaults with opt-in live Azure only;
- secret-safe fixtures and logs.

## Harness design

- Prefer one harness style per phase convergence suite rather than ad hoc sleeps in each test.
- Inject clocks, transports, and Azure/Docker boundaries so timing and failures are reproducible.
- Keep real brokers/services only where the phase owns them (for example Phase 1: Mosquitto, RabbitMQ bridge surfaces, Head, Launcher).
- Simulate peer protocols with the shared contract models, not loose dictionaries that drift from schemas.
- Isolate multi-process tests so parallel CI runs cannot collide on ports, state files, or image tags.

## Deterministic time

- Drive grant TTL, heartbeat timeout, drain timeout, ack waits, and Launcher verification windows from injected monotonic/wall clocks.
- Advance time explicitly in tests instead of relying on real multi-second sleeps whenever practical.
- Keep wall-clock audit fields separate from monotonic expiry decisions, matching `leadership_control.md`.

## Fault injection

Cover the failure classes the scenario actually asserts:

- process crash/disappearance;
- Mosquitto disconnect or publish-confirm failure;
- Blob Lease conflict, renew failure, or unavailability;
- Web PubSub disconnect or send failure;
- Launcher IPC timeout, `409`, or auth failure;
- Docker unavailable, partial pull, recreate failure, verification timeout, rollback failure.

Inject one controlled fault per assertion path unless the scenario explicitly requires combined loss.

## Protocol simulators

When a later-phase component is out of scope:

- simulate only the wire/protocol surface needed by the current phase;
- validate simulator payloads through shared models and reject unknown `schema_version`;
- for Phase 1, simulate Bot control ack, drain progress, and AI Worker pause ack without implementing Discord Gateway or Celery graphs;
- for Phase 3 S12, prefer hermetic fakes of Cosmos claim/Queue/Discord DM; mark crash-after-DM bounded duplicate explicitly; do not claim exactly-once DM delivery;
- never silently treat a simulator acknowledgement as proof that the real component works.

## Scenario reporting

For every scenario test module or acceptance case:

1. Name the scenario ID (`S01`, `S07`, `S12`, …).
2. List ordered steps covered by the test.
3. Mark each step `complete` or `deferred` with the owning later phase when deferred.
4. Assert only the invariants that the current phase can honestly prove.
5. Fail the suite if a test claims full scenario acceptance while deferred steps remain.

**Phase 3 / S12:** when claiming S12, cover normal delivery, duplicate Queue, lost Queue→sweep, claim race, enqueue failure, Web idempotent retry, failed-notification admin retry, crash before DM, crash after DM before Cosmos `sent` (bounded duplicate), Discord forbidden/not-found/transient, and production vs development suppression — per `docs/scenarios/12_suggestion_duplicate_or_lost_queue.md`.

Example reporting shape:

```text
S03: Head crash / grant cessation = complete
S03: Bot autonomous hard-stop sequence = deferred (Phase 2)
```

## Environment rules

- Default CI/local suites must not contact live Azure.
- Live Azure tests require explicit opt-in, allowlisted resources/prefixes, unique per-run names, and cleanup in teardown.
- Prefer Compose brokers already covered by Phase 0 integration helpers when broker realism is required.
- Do not publish broker ports or Head IPC beyond documented bind modes in test Compose overrides unless a test is specifically asserting those boundaries.

## Change boundaries

- Do not reimplement product logic inside the harness to make a scenario pass.
- Do not weaken contract validation in simulators for convenience.
- Do not convert deferred steps into green full-pass markers.
- Keep harness utilities under `tests/`; do not couple production packages to test-only clocks/faults except through intentional injectable interfaces.

## Completion criteria

A harness slice is complete only when:

- claimed scenarios map to `docs/scenarios/` IDs and invariants;
- clocks and faults make the covered steps deterministic;
- simulators use shared contract models;
- complete versus deferred steps are explicit in code or report output;
- no full-pass claim is made for partially simulated scenarios;
- hermetic tests pass without live Azure by default;
- handoff names remaining deferred scenario steps and the phase that owns them.
