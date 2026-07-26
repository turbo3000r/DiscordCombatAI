---
name: discord-combat-ai-web
description: Implements and changes the DiscordCombatAI Web admin dashboard (FastAPI + React) under src/web/, including Entra/local-admin auth, Phase 3 Suggestions list/detail/respond, shared suggestion response service, Queue enqueue, and later Home/Guilds/Dashboard/Performance/webhook slices. Use when working under src/web/, Web Docker/deployment wiring, or Web-side S12–S13 coverage.
disable-model-invocation: true
---

# DiscordCombatAI Web

Use together with `discord-combat-ai-implementation`. This skill specializes the Web container; it does not replace the umbrella workflow.

## Required context

Before planning or editing:

1. Follow the umbrella skill's required-context steps.
2. Read `docs/containers/web/web.md` (Phase 3 minimal slice note), then only the `pages/*.md` / `components.md` files for the slice.
3. Read owning contracts: `web_auth`, `status_document`, `guild_config`, `suggestion`, `telemetry`, `pubsub_live`, `local_development`, and `battle_archive` as applicable.
4. Read `docs/containers/azure.md` for Web SP credentials and RBAC — never redefine `AZURE_*` in page docs.
5. Check `docs/to_resolve.md` for the **Phase 3** gate (Suggestions slice) versus remaining **P1.6** items for later Web pages.
6. Inspect existing `src/web/` (target) before adding parallel routers. Treat repository-root legacy `web/` as **reference-only** unless docs prescribe migration.

Do not invent full OpenAPI for every deferred page while implementing the Phase 3 Suggestions slice. Stop and report missing decisions that block the current slice.

## Phase-aware scope

### Phase 3 — minimal Suggestions slice (in scope)

Own explicitly:

- production Entra/admin authorization boundary (`web_auth.md`);
- fixed local administrator in development (`local_development.md` §8) + DEVELOPMENT banner;
- Suggestions list/detail sufficient for response handling (`pages/suggestions.md`);
- `POST /api/suggestions/{id}/respond` with `Idempotency-Key`;
- shared suggestion response service (Cosmos/dev-support mutation + conditional Queue enqueue);
- Queue enqueue for notifying response modes in production; suppress enqueue/DM side effects in development;
- catalog seed/update only if required for `/suggest`;
- S12 coupling with Bot delivery (duplicate/lost queue, failed-notification retry, bounded duplicate DM honesty);
- implement under target **`src/web/`**.

### Explicitly deferred (not Phase 3)

- Home, Guilds, Dashboard, Performance;
- webhook broadcasting and S13;
- complete P1.6 API and operational polish (health/readiness, logging destination, deploy/rollback, full schemas for non-Suggestions pages);
- Head or Launcher redesign.

### Later / Phase 6 full Web

1. Auth shell (shared with Phase 3) + status seed  
2. Home + Guilds  
3. Dashboard + Performance (needs Head telemetry)  
4. Suggestions (Phase 3 owns the vertical slice)  
5. Webhook broadcast + audit  
6. Health/readiness, logging destination, deploy/rollback (P1.6)

### Not in Phase 2

Phase 2 is Bot + AI Worker transport. Do not implement Web features inside a Phase 2 transport slice.

## Hard requirements

Require:

- single container, multi-stage React→FastAPI build under `src/web/`;
- no direct network access to Bot/Head/RabbitMQ/Mosquitto — Azure only in production; `dev-support` only in development;
- Entra Bearer + admin group on all production `/api/*`; static SPA shell public; development local-admin credential;
- never return raw `webhook_url`, `api_key`, Azure secrets, or PubSub connection strings from list/detail APIs;
- suggestion respond ETag + Idempotency-Key rules from `suggestion.md`;
- webhook SSRF allowlist and broadcast idempotency/cooldown only when implementing webhook (deferred);
- PubSub negotiate returns join/leave-only tokens for `dashboard-live` only (deferred until Dashboard);
- shared Azure access only through `src/shared/azure/` with `WEB_` credentials in production.

## Change boundaries

- Do not embed Bot/AI Worker/Head business logic in Web.
- Do not proxy live telemetry through Web when docs require direct browser→PubSub.
- Do not widen Entra or Azure roles “for convenience.”
- Do not extend legacy root `web/` as the implementation home for Phase 3.
- Do not invent a `version` field on the status document — identity excludes version (`status_document.md`); P1.6 owns any alternate version source for Home.

## Testing and completion criteria

Include focused tests for:

- 401 unauthenticated / 403 non-admin on `/api/*` (production auth);
- local-admin gate in development;
- suggestion respond modes + failed-notification retry + Idempotency-Key replay;
- Queue enqueue skipped in development; attempted in production after Cosmos pending;
- secrets redacted in API responses and logs.

Validate S12 together with Bot delivery. Prefer mocked Azure; live Azure only behind explicit opt-in. Do not claim S13 from the Phase 3 Suggestions slice alone.

A slice is complete only when:

- Phase 3 Suggestions gates are closed or explicitly out of scope;
- focused tests and static checks pass;
- secrets remain redacted;
- handoff names the next Web page/API slice or Bot delivery follow-up.
