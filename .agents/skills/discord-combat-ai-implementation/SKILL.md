---
name: discord-combat-ai-implementation
description: Implements DiscordCombatAI architecture in small, verified slices while enforcing its canonical documentation, contracts, subsystem readiness gates, and acceptance scenarios. Use for every source code, infrastructure, configuration, migration, or implementation-planning task in this repository.
disable-model-invocation: true
---

# DiscordCombatAI Implementation

## Required context

Before planning or editing:

1. Read `docs/Readme.md`.
2. Read `docs/to_resolve.md`.
3. Identify the current implementation phase and applicable P1 blockers.
4. Read `docs/architecture.md` only as needed for system-wide context.
5. Read the canonical contracts, container docs, command/page/graph docs, and scenarios that govern the requested slice.
6. Inspect the current implementation before deciding what must change. Documentation describes the target architecture and may be ahead of the code.

Do not load unrelated documents merely for completeness.

## Sources of truth

- `docs/contracts/` owns cross-service wire formats, persisted schemas, ownership, state transitions, and compatibility rules.
- `docs/containers/` owns service behavior, dependencies, configuration, and failure handling.
- `docs/scenarios/` owns end-to-end acceptance invariants.
- `docs/to_resolve.md` owns readiness gates and implementation order.
- Existing source code is evidence of current behavior, not authority over the documented target architecture.

When sources conflict, stop and report the exact conflict. Do not silently choose a convenient interpretation.

## Readiness gate

Before implementing a slice:

1. List the P1 items that apply to it.
2. Confirm each applicable item is resolved or explicitly outside the slice.
3. Do not invent defaults, schemas, timeout behavior, recovery behavior, or ownership for an unresolved decision.
4. Unrelated unresolved P1 items do not block ready work.

If a required decision is unresolved, limit work to unaffected scaffolding or return the decision needed.

## Implementation workflow

1. Define one reviewable slice with explicit exclusions.
2. Identify its canonical documentation and acceptance scenarios.
3. Inspect affected code, tests, configuration, and dependency boundaries.
4. Implement the smallest complete vertical or foundational change.
5. Keep shared contracts independent from transport and service logic.
6. Preserve documented service ownership and least-privilege boundaries.
7. Add or update tests for success, validation, failure, retry, idempotency, and compatibility behavior relevant to the slice.
8. Run focused tests and static checks, then broader checks proportional to the risk.
9. Compare the result against the relevant scenario invariants.
10. Report completed scope, verification, remaining blockers, and any documentation mismatch.

## Change boundaries

- Do not modify unrelated legacy files or perform opportunistic refactors.
- Do not commit runtime guild data, generated battle output, credentials, tokens, API keys, local volumes, or other secrets.
- Do not weaken fencing, authentication, cancellation, durability, idempotency, schema-version, or secret-redaction requirements to simplify implementation.
- Do not claim exactly-once delivery where the contracts specify at-least-once delivery with effectively-once outcomes.
- Keep changes small enough to review and revert independently.
- Do not create commits or push unless explicitly requested.

## Contract and compatibility checks

For every affected cross-service contract, verify:

- producer and consumer agree on the same canonical model;
- `schema_version` behavior matches the contract;
- identifiers, timestamps, enums, optional fields, and unknown-version handling are tested;
- durable writes, acknowledgements, deduplication, and retry ordering preserve the documented invariant;
- sensitive fields are redacted from logs and user-facing APIs;
- additive compatibility is supported only where the documentation requires it.

## Completion criteria

A slice is complete only when:

- its applicable P1 gates are closed or documented as out of scope;
- focused tests pass;
- lint/type/static checks for changed files pass;
- relevant failure paths are tested or explicitly identified as requiring an environment-level test;
- applicable scenario invariants still hold;
- no undocumented behavior was introduced;
- the handoff names the next dependency-ordered slice.

If implementation reveals that canonical documentation is impossible or contradictory, stop implementation at that boundary and request a documentation decision rather than encoding an assumption.
