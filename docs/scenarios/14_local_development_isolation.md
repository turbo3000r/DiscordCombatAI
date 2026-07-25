# S14 — Local product-development isolation

**Id:** `S14`  
**Canonical:** `contracts/local_development.md`; `architecture.md` Environments; `containers/bot/discord_bot.md` §3/§6.1a; `containers/web/web.md` §1/§6; `contracts/web_auth.md` §1a

> **Scope note:** this scenario proves **product-development isolation**, not Azure leadership/failover fidelity. Passing S14 does **not** claim S01–S10, production S12, or production S13.

## Preconditions

- Operator starts the development Compose overlay with `DCA_RUNTIME_MODE=development`.
- Separate Discord application/token (not production).
- `DISCORD_DEVELOPMENT_GUILD_ID` set to a private test guild; development bot invited only there.
- `dev-support`, Mosquitto, RabbitMQ, Bot, AI Worker, and Web are up; Head and Launcher are absent.
- No production Azure SP secrets / Cosmos / PubSub endpoints are configured for use.

## Ordered steps

1. Bot and Web refuse to start if mode/guild/provider/bind guards fail (`local_development.md` §3/§9).
2. `dev-support` seeds status identity/catalog and begins publishing short-lived Mosquitto activation grants.
3. Bot accepts a grant, connects Gateway, and performs **guild-scoped** command sync to `DISCORD_DEVELOPMENT_GUILD_ID` only (never global sync).
4. Slash commands are visible and executable only in the development guild. An interaction from any other guild id is rejected; no repository write occurs.
5. Guild join/update/remove and periodic sync ignore foreign guilds (no writes).
6. `/config` and `/suggest` persist through local repositories → `dev-support` SQLite. Suggestion queue poller / Discord response DMs do not run.
7. Web serves with fixed local admin + DEVELOPMENT banner. Dashboard live data uses local feed (no Azure PubSub negotiate). Webhook submit returns dry-run / preview and performs **no** Discord webhook HTTPS POST.
8. Restart Bot/Web/`dev-support` without deleting the volume: guild/suggestion/status data still present.
9. Stop `dev-support`: Bot grant renewals cease → Bot hard-stops on grant expiry; Web data APIs fail closed.
10. Separately (production process): with `DCA_RUNTIME_MODE=production` and the same reserved guild id configured, production Bot rejects interactions from that guild.

## Durable writes

- Development: SQLite rows in `dev-support` volume only.
- Production Azure: **none** from the development stack.
- Discord webhook destinations: **none** from development Webhook page.
- Discord suggestion response DMs: **none** from development Bot.

## Timeouts

- Grant TTL/renew follow `dev-support` cadence compatible with Bot grant watchdog (defaults may match production 45s/15s).
- Explicit volume reset is operator-driven; restart alone does not wipe state.

## User-visible result

- Developer can exercise Bot command UI and Web pages against the designated guild.
- No production Discord application command tree changes from development sync.
- No Azure / Entra / webhook egress from the development stack.

## Invariant checked

**Product-development isolation:** development uses a separate Discord identity, one guild, local domain providers, and suppressed announcement/DM side effects; it never substitutes for Azure coordination acceptance (S01–S10).
