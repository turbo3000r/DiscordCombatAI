# S11 — Quick Battle success / abort / timeout

**Id:** `S11`  
**Canonical:** `bot/commands/quick-battle.md`; `contracts/ai_task.md`; `contracts/task_progress.md`; `contracts/drain_status.md` §1; `contracts/battle_archive.md`  
**Note:** Exact lobby deadlines, approval thresholds, participant caps, and Discord delivery limits remain **P1.1 product rules** — this scenario checks architecture invariants only.

## Preconditions

- Leader Bot active with grant; guild enabled with usable API key/model per guild config (exact guard wording: P1.1).
- User invokes `/quick-battle` while `bot.draining == False`.

---

## Invariant A — Success path

### Ordered steps

1. Lobby / collection / vote phases run as command UI (Components V2); each open lobby/collector/vote increments `in_flight_workflows`.
2. Bot publishes environment and/or battle tasks via Celery `ai_tasks` (`ai_task.md`); tracks `task_id` in the task map.
3. AI Worker emits best-effort `progress/ai_worker/<task_id>` phases; Bot renders status UI (`task_progress.md`).
4. Final `ai_tasks_results` accepted for known `task_id`; Bot delivers story to Discord; best-effort battle archive write (`battle_archive.md`).
5. Workflow counters decrement on terminal success.

### Durable writes

- Optional battle archive blob; guild config unchanged.
- No suggestion/Queue involvement.

### Timeouts

- Per-phase Discord deadlines: **P1.1**.
- Stall/overall AI timeouts: `ai_task.md` / Bot env defaults (bounded once P1.1/P1.2 close worst-case cost).

### User-visible result

- Completed battle narration in the channel/thread per command doc.

### Invariant checked

**Success requires known `task_id` correlation and Discord delivery owned by Bot** — worker progress alone never marks the workflow complete.

---

## Invariant B — User abort

### Ordered steps

1. User cancels at a phase that the command documents as abortable (exact phase matrix: P1.1).
2. Bot decrements `in_flight_workflows`, cancels views, and if an AI task is open applies the cancellation matrix (`ai_task.md` §8) — Bot sole `revoke` actor.
3. Late results for that `task_id` are discarded.

### Durable writes

- None required beyond logs.

### Timeouts

- Abort is immediate relative to user action; revoke/timeout cleanup per `ai_task.md`.

### User-visible result

- Lobby/UI closed with abort acknowledgement (localized copy is implementation).

### Invariant checked

**Abort clears workflow accounting and cancels AI work without leaving orphan `in_flight_workflows` counts.**

---

## Invariant C — Timeout

### Ordered steps

1. A phase deadline or AI stall/overall timeout fires (numbers: P1.1 / `ai_task.md`).
2. Bot treats the workflow as terminal failure: notify Discord surface, cancel/revoke as specified, decrement counters.
3. Drain interaction: if Head later drains, timed-out workflows must not stick forever in `in_flight_workflows` (terminal resolution required — `drain_status.md` §1).

### Durable writes

- Logs; no success archive.

### Timeouts

- Architecture requires **some** bound; exact seconds are P1.1/P1.2.

### User-visible result

- Timeout / failure message; user may retry when not draining.

### Invariant checked

**Every timeout path terminates the workflow counter and applies the documented cancel matrix** — no silent hang that blocks planned drain forever without S08 escalation.
