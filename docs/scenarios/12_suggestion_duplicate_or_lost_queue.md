# S12 — Suggestion respond with duplicate or lost Queue delivery

**Id:** `S12`  
**Canonical:** `contracts/suggestion.md` §3–§4; `bot/discord_bot.md` §6.6; `web/pages/suggestions.md`; `bot/commands/suggest.md`; `contracts/local_development.md` §7  
**Phase ownership:** Phase 3 documentation gate / implementation acceptance for suggestion delivery.

## Preconditions

- Ticket exists (`schema_version` 2) with `status=pending`, seeded `conversation`, `contact.method=dm`.
- **Production:** admin authenticates (Entra + admin group — `web_auth.md`) and POSTs `/respond` with `send` or `done_auto_feedback` (+ `Idempotency-Key`).
- **Development (negative path):** local admin may CRUD; Queue enqueue and Discord DM **must not** occur.

## Ordered steps

1. **Normal response delivery:** Web Cosmos write sets `status=done`, appends staff `conversation`, `response_text`, `notification_status=pending`, `acted_by_*`; enqueues Queue `{schema_version, id, ticket_uid, guild_id, enqueued_at}` (production only). Bot claims `pending`→`claiming`, DMs user, `claiming`→`sent`, deletes Queue message.
2. **Duplicate Queue message:** at-least-once redelivery (or two Bot instances race). First successful ETag claim wins; losers skip. Already `sent` / foreign `claiming` ⇒ ignore. Dedup key: Cosmos `id`.
3. **Lost Queue message:** message never arrives or is deleted early — Cosmos remains `pending`. After `BOT_SUGGESTION_SWEEP_MIN_AGE_SEC`, sweep claims and DMs without Queue.
4. **Two Bot instances racing to claim:** only one ETag transition `pending`→`claiming` succeeds; the other skips — no double intentional send from concurrent claims.
5. **Enqueue failure:** Cosmos already `pending`; UI may show saved + pending; sweep recovers — ticket not lost.
6. **Web response retry (Idempotency-Key):** duplicate POST returns prior success; no second conversation entry; no second enqueue.
7. **Failed-notification administrator retry:** after `notification_status=failed`, admin `mode=send` with confirmed body → attempts reset to 0 → `pending` → new enqueue → claim/DM (`suggestion.md` §3a).
8. **Bot crash before DM:** claim may expire (`BOT_SUGGESTION_CLAIM_TIMEOUT_SEC`); sweep resets `claiming`→`pending` and later reclaims — user may receive zero DMs until recovery succeeds.
9. **Bot crash after Discord accepts DM but before Cosmos `sent`:** claim timeout → reclaim → **bounded duplicate DM possible**. Exactly-once is **not** claimed.
10. **Discord errors:** `Forbidden` / not-found classified; transient errors return to `pending` until max attempts then `failed`.
11. **Production vs local-development:** development suppresses Queue + DM; production executes full path. S14 covers isolation; this scenario’s delivery invariants are production (or explicit hermetic fakes of Cosmos/Queue/Discord).

## Durable writes

- Cosmos ticket (authoritative notification machine).
- Queue message (fast path only).
- No legacy flat JSON; no `responded`/`response_given`.

## Timeouts

- Queue visibility **60s**.
- Sweep interval / min-age / claim-timeout / max DM attempts — `discord_bot.md` §3 + `suggestion.md`.
- Poison after **5** dequeues without terminal success — Cosmos still authoritative.

## User-visible result

- Prefer a single successful response DM per respond/retry action under ETag claim.
- **Bounded duplicate DM** is an accepted limitation for crash-after-DM-before-Cosmos (§ step 9) — mark explicitly in reports; do not fail the scenario for documenting that bound.
- Web shows `done` + `notification_status` progression (`pending`/`claiming`/`sent`/`failed`).

## Invariant checked

**Cosmos `notification_status` + ETag claim is authoritative; Queue is a hint.** Duplicate/lost Queue must not permanently strand a healthy `pending` ticket. Concurrent claims must not both send deliberately. Crash-after-DM may duplicate once within the claim-recovery bound — not exactly-once.
