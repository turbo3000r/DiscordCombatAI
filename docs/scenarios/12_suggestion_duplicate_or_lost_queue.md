# S12 — Suggestion respond with duplicate or lost Queue delivery

**Id:** `S12`  
**Canonical:** `contracts/suggestion.md` §3–§4; `bot/discord_bot.md` §6.6; `web/pages/suggestions.md`; `bot/commands/suggest.md`

## Preconditions

- Ticket exists in Cosmos (`schema_version` 2) with `status=pending`, seeded `conversation`, `contact.method=dm`.
- Admin authenticates to Web (Entra + admin group — `web_auth.md`) and POSTs `/respond` with `send` or `done_auto_feedback`.

## Ordered steps

1. Web Cosmos write: `status=done`, append staff `conversation` entry, set `response_text`, `notification_status=pending`, ticket-level `acted_by_*`, enqueue Queue message `{schema_version, id, ticket_uid, guild_id, enqueued_at}`.
2. **Lost Queue:** message never arrives or is deleted prematurely by a bug — Cosmos remains `pending`. After `BOT_SUGGESTION_SWEEP_MIN_AGE_SEC`, sweep claims `pending`→`claiming` and sends DM; no Queue dependency.
3. **Duplicate Queue:** at-least-once delivery redelivers the same message (or two bots race). First successful ETag claim wins; losers skip. Already `sent` / foreign `claiming` ⇒ ignore work. Dedup key for application logic: Cosmos `id`.
4. Claimant DMs `contact.user_id` using `locale.stored`; on success `claiming`→`sent` then delete Queue message; on failure attempts++ and return to `pending` or terminal `failed`.
5. Poison after 5 dequeues without terminal success — Cosmos remains authoritative; sweep can still heal if still `pending`.

## Durable writes

- Cosmos ticket (authoritative notification machine).
- Queue message (fast path only).
- No legacy flat JSON; no `responded`/`response_given` booleans.

## Timeouts

- Queue visibility 60s.
- Sweep interval / min-age defaults (`discord_bot.md` §3 / suggestion contract).
- Max DM attempts → `failed`.

## User-visible result

- User receives at most one successful response DM for a given respond action under single-admin v1 (multi-admin concurrency: P1.4).
- Web UI shows done + notification_status progression.

## Invariant checked

**Cosmos `notification_status` + ETag claim is authoritative; Queue is a hint.** Duplicate/lost Queue must not double-DM or permanently strand a `pending` ticket when Bot and Cosmos are healthy.
