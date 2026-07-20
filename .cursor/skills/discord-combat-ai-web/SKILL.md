---
name: discord-combat-ai-web
description: Implements and changes the DiscordCombatAI Web admin dashboard container (FastAPI + React), including Entra auth, status/guild/suggestion/metrics/webhook APIs, PubSub negotiate, and page UI. Use when working under src/web/, Web Docker/deployment wiring, or Web-side scenario coverage for S12–S13.
disable-model-invocation: true
---

# DiscordCombatAI Web

Use together with `discord-combat-ai-implementation`. This skill specializes the Web container; it does not replace the umbrella workflow.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read `docs/containers/web/web.md`, then only the `pages/*.md` / `components.md` files for the slice.
3. Read owning contracts: `web_auth`, `status_document`, `guild_config`, `suggestion`, `telemetry`, `pubsub_live`, and `battle_archive` as applicable.
4. Read `docs/containers/azure.md` for Web SP credentials and RBAC — never redefine `AZURE_*` in page docs.
5. Check `docs/to_resolve.md` for **P1.6** (and leftover suggestion items under P1.4) before treating an API slice as complete.
6. Inspect existing `src/web/` (or documented target tree) before adding parallel routers or clients.

Do not invent request/response schemas, pagination, or operational endpoints while P1.6 leaves them unresolved. Stop and report the missing decision.

## Phase-aware scope

Web is **Phase 6** in the implementation sequence. Earlier phases may prepare shared Azure clients and contracts only.

### Allowed early scaffolding (when explicitly requested)

- multi-stage Dockerfile / package layout matching `architecture.md`;
- Entra middleware skeleton against `contracts/web_auth.md`;
- status-document seed helpers via shared Azure services.

### Not in Phase 2

Phase 2 is Bot + AI Worker transport. Do not implement Web features inside a Phase 2 transport slice. Do not claim S13 acceptance from Bot/AI Worker work.

### Full Web slices (Phase 6 / related)

1. Auth shell + status seed  
2. Home + Guilds  
3. Dashboard + Performance (needs Head telemetry)  
4. Suggestions (with Phase 3 Bot delivery)  
5. Webhook broadcast + audit  
6. Health/readiness, logging destination, deploy/rollback (P1.6)

## Hard requirements

Require:

- single container, multi-stage React→FastAPI build;
- no direct network access to Bot/Head/RabbitMQ/Mosquitto — Azure only;
- Entra Bearer + admin group on all `/api/*`; static SPA shell public;
- never return raw `webhook_url`, `api_key`, Azure secrets, or PubSub connection strings from list/detail APIs;
- webhook SSRF allowlist and broadcast idempotency/cooldown rules from `web_auth.md`;
- PubSub negotiate returns join/leave-only tokens for `dashboard-live` only;
- shared Azure access only through `src/shared/azure/` with `WEB_` credentials.

## Change boundaries

- Do not embed Bot/AI Worker/Head business logic in Web.
- Do not proxy live telemetry through Web when docs require direct browser→PubSub.
- Do not widen Entra or Azure roles “for convenience.”
- Do not invent a `version` field on the status document — identity excludes version (`status_document.md`); P1.6 owns any alternate version source for Home.

## Testing and completion criteria

Include focused tests for:

- 401 unauthenticated / 403 non-admin on `/api/*`;
- JWT validation failure modes documented in `web_auth.md`;
- webhook host allowlist / no-redirect;
- suggestion mutation authz + audit fields where the slice owns them;
- negotiate token shape without leaking privileged roles.

Validate S13 only after auth and ALL-guild broadcast both exist. Prefer mocked Azure; live Azure only behind explicit opt-in.

A slice is complete only when:

- applicable P1.6 (and related) gates are closed or explicitly out of scope;
- focused tests and static checks pass;
- secrets remain redacted in API responses and logs;
- handoff names the next Web page/API slice.
