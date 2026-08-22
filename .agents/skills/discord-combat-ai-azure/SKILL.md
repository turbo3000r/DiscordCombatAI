---
name: discord-combat-ai-azure
description: Implements and changes DiscordCombatAI's shared Azure library under src/shared/azure/, including credentials, resource clients, retries, RBAC assumptions, and domain service wrappers. Use when working on Azure access, ClientSecretCredential setup, Blob/Table/Queue/Cosmos/Web PubSub clients, status/guild/suggestion/metrics/logging services, Azure failure classification, or Azure-related tests.
---

# DiscordCombatAI Azure

Use together with `discord-combat-ai-implementation`. This skill specializes Azure library work; it does not replace the umbrella workflow.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read `docs/containers/azure.md` as the single source of truth for Azure env vars, client construction, and RBAC assumptions.
3. Read only the Azure-related contracts needed by the slice (`status_document`, `guild_config`, `suggestion`, `telemetry`, `battle_archive`, `log_archive`, `pubsub_live`, `web_auth` as applicable).
4. Check `docs/to_resolve.md` for applicable P1.3 / P1.7 gates and related cleanup items.
5. Inspect existing `src/shared/azure/` (or target paths in `azure.md` §2) before adding parallel modules.

## Layering

Keep a strict three-layer split:

1. **Credential/config** — load and validate env vars; construct and cache one per-process `ClientSecretCredential` for the calling service (`HEAD_` / `BOT_` / `WEB_` prefixes).
2. **Raw clients** — thin per-resource SDK wrappers (`blob`, `table`, `queue`, `cosmos`, `pubsub`). No domain policy beyond transport helpers.
3. **Domain services** — ownership-aware wrappers (`status`, `guilds`, `suggestions`, `metrics`, `logging`, `guild_logs`) that implement documented contracts on top of clients.

Do not put business/state-machine logic in raw clients. Do not construct Azure SDK clients outside `src/shared/azure/`.

## Hard requirements

Require:

- per-service credentials and least privilege;
- async-safe SDK usage;
- clear separation of credential/config, raw clients, and domain services;
- explicit transient versus permanent failure classification;
- bounded retry ownership;
- secret redaction;
- ETag/concurrency handling where documented;
- mocked unit tests with no live Azure dependency by default;
- no undocumented fallback behavior.

## Credentials and RBAC

- Use shared `AZURE_TENANT_ID` plus the calling service's own `<SERVICE>_AZURE_CLIENT_ID` / `<SERVICE>_AZURE_CLIENT_SECRET`.
- Never introduce a shared cross-service Service Principal.
- Grant only the roles/scopes documented in `azure.md`; do not widen access "for convenience."
- Derive storage endpoints from `AZURE_STORAGE_ACCOUNT_NAME` as documented; do not invent duplicate endpoint env vars unless `azure.md` is updated first.

## Retry, failure, and concurrency

- Classify auth/permission failures as permanent; do not retry them as transient network errors.
- Classify throttling/network blips as transient; use bounded backoff.
- Own retry at one layer: either tune SDK retry or application retry, but do not multiply both unexpectedly.
- Implement ETag RMW / conditional patch exactly where contracts require it (`status_document`, `guild_config`, suggestion claim machine). Cap retries as documented.
- On exhaustion or permanent failure, surface a typed/library error to the caller. Do not invent silent skip/fallback unless a canonical doc already specifies it.

## Secrets and logging

- Never log client secrets, connection strings, PubSub full URLs with tokens, guild API keys, webhook URLs, or raw JWTs.
- Redact sensitive fields in exception messages and debug dumps.
- Do not commit `.env`, credential files, or live Azure responses that contain secrets.

## Testing

- Default to mocked unit tests: no live Azure account required for CI/local unit runs.
- Cover credential/config validation, client construction wiring, transient vs permanent classification, bounded retry, ETag conflict/retry, and secret redaction.
- Integration tests against real Azure are optional and must be explicitly opted into (separate marker/env); never the default path.
- Prefer fakes/mocks at the SDK or raw-client boundary so domain-service tests stay fast and deterministic.

## Change boundaries

- Do not redefine `AZURE_*` variables in Head/Bot/Web docs or service code; link to `azure.md`.
- Do not duplicate contract schemas inside Azure services; import shared models / follow contracts.
- Do not implement Head/Bot/Web feature behavior inside this library beyond the documented service wrappers.
- Stop and report if `azure.md` or a contract leaves failure recovery unspecified for the slice; do not encode an assumption.

## Completion criteria

A slice is complete only when:

- layering remains credential/config → clients → services;
- per-service identity and least-privilege assumptions still hold;
- documented ETag/concurrency paths are implemented and tested;
- transient vs permanent failures are explicit and bounded;
- secrets are redacted in logs/errors;
- mocked unit tests pass without live Azure;
- no undocumented fallback was introduced;
- handoff names the next Azure or consumer slice.
