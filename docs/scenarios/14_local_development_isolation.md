# S14 — Local product-development isolation

**Id:** `S14`  
**Canonical:** `contracts/local_development.md`; `architecture.md` Environments; `containers/bot/discord_bot.md` §3/§6.1a; `containers/web/web.md` §1/§6; `contracts/web_auth.md` §1a

> **Scope note:** this scenario proves **product-development isolation**, not Azure leadership/failover fidelity. Passing S14 does **not** claim S01–S10, production S12, or production S13.

## Staging

| Stage | Covered now (Phase 2.5 spine) | Deferred (commands / Web / P1) |
|---|---|---|
| Mode/guards | Startup refusal for bad mode/guild/provider/overlay/Azure leakage | — |
| Identity | `DISCORD_DEVELOPMENT_APPLICATION_ID` verified before sync | — |
| Grants | `dev-support` seeds status + publishes grants; stop → Bot hard-stop | — |
| Sync / guild | Guild-scoped sync; foreign guild lifecycle ignored; prod rejects reserved guild | Slash command UX / ephemeral reject copy when commands exist |
| Persistence | Guild/status/suggestion CRUD via `dev-support` SQLite; restart keeps data | `/config`, `/suggest`, `/quick-battle` end-to-end |
| Web | — | Local admin, DEVELOPMENT banner, local live feed, webhook dry-run UI |

Foundation tests should be labeled as the **S14 spine** subset. Full S14 acceptance requires deferred steps.

## Preconditions

- Operator starts the development Compose overlay with `DCA_RUNTIME_MODE=development`.
- Separate Discord application/token (not production); `DISCORD_DEVELOPMENT_APPLICATION_ID` matches that application.
- `DISCORD_DEVELOPMENT_GUILD_ID` set to a private test guild; development bot invited only there.
- Spine stack: `dev-support`, Mosquitto, RabbitMQ, Bot, AI Worker are up; Head and Launcher are absent. Web is optional until `src/web/` exists.
- No production Azure SP secrets / Cosmos / PubSub endpoints are configured for use.

## Ordered steps

1. Bot (and Web when present) refuse to start if mode/guild/application-id/provider/overlay/Azure guards fail (`local_development.md` §3/§9).
2. `dev-support` seeds status identity/catalog and begins publishing short-lived Mosquitto activation grants.
3. Bot accepts a grant, connects Gateway, verifies the authenticated application id, and performs **guild-scoped** command sync to `DISCORD_DEVELOPMENT_GUILD_ID` only (never global sync).
4. **Deferred until commands exist:** slash commands visible/executable only in the development guild; foreign interactions rejected with no repository write. **Spine:** guild admission predicate and scoped sync are in place.
5. Guild join/update/remove and periodic sync ignore foreign guilds (no writes).
6. **Deferred until command phase:** `/config` and `/suggest` persist through local repositories. **Spine:** local repository adapters + `dev-support` suggestion CRUD API exist; suggestion queue poller / Discord response DMs do not run in development.
7. **Deferred until Web:** fixed local admin + DEVELOPMENT banner; local live feed; webhook dry-run with no Discord webhook HTTPS POST. Compose publishes Web only on host loopback when Web is added.
8. Restart Bot/`dev-support` without deleting the volume: guild/suggestion/status data still present.
9. Stop `dev-support`: Bot grant renewals cease → Bot hard-stops on grant expiry.
10. Separately (production process): with `DCA_RUNTIME_MODE=production` and the required reserved guild id, production Bot rejects interactions / lifecycle for that guild.

## Durable writes

- Development: SQLite rows in `dev-support` volume only.
- Production Azure: **none** from the development stack.
- Discord webhook destinations: **none** from development (when Web dry-run is implemented).
- Discord suggestion response DMs: **none** from development Bot.

## Timeouts

- Grant TTL/renew follow `dev-support` cadence compatible with Bot grant watchdog (defaults may match production 45s/15s).
- Explicit volume reset is operator-driven; restart alone does not wipe state.

## User-visible result

- Developer can exercise Bot command UI and Web pages against the designated guild **once deferred slices land**.
- Spine: isolated Guild Gateway + local persistence + grants without Azure.
- No production Discord application command tree changes from development sync.
- No Azure / Entra / webhook egress from the development stack.

## Invariant checked

**Product-development isolation:** development uses a separate Discord identity, one guild, local domain providers, and suppressed announcement/DM side effects; it never substitutes for Azure coordination acceptance (S01–S10).
