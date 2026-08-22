# S11 — Quick Battle success / abort / timeout

**Id:** `S11`  
**Canonical:** `bot/commands/quick-battle.md`; `contracts/ai_task.md`; `contracts/task_progress.md`; `contracts/drain_status.md` §1; `contracts/battle_archive.md`  
**Status:** Concrete Phase 5 acceptance scenario. P1.1/P1.2 values are resolved in the canonical command/graph docs.

## Preconditions

- Leader Bot active with grant; active guild config has `enabled=true`, nonblank previously validated API key, and nonblank model.
- User invokes `/quick-battle` while `bot.draining == False`.
- No existing lobby in the guild; invoker is not already in a lobby there; invoker/guild cooldowns are clear.

---

## Invariant A — Success and delivery

### Ordered steps

1. Bot acknowledges the interaction, opens one mention-free 30–600-second lobby (default 60), and increments `in_flight_workflows` exactly once.
2. Start/expiry freezes `1..10` participants. For custom environment, parallel descriptions complete within 120 seconds.
3. Bot acquires the node's sole AI slot within 60 seconds, publishes one expected environment task, and stores graph/revision plus stable Discord IDs.
4. Every participant casts Approve/Decline within 120 seconds. Required approvals are `ceil(N*0.70)`. At most three revision tasks follow the initial candidate.
5. Step 6 fighter collection completes within 180 seconds with `1..10` valid fighters.
6. Bot publishes one expected battle task. Worker heartbeat is 30s; graph deadlines are environment 600s/battle 840s; Bot timers are stall 120s/overall 900s.
7. Bot accepts only the expected result. Ordinary UI has no mentions; winner allowed-mentions contains only exact validated winner IDs.
8. Story ≤5,700 characters is delivered in ≤3 paragraph-bound messages of ≤1,900 chars. A longer story uses a ≤1,900-char preview plus full UTF-8 attachment. The full story is archived best-effort.
9. Bot releases the AI slot, disables terminal UI, and decrements the workflow exactly once.

### Durable writes

- Optional full-story battle archive blob + metadata; guild config unchanged.
- No suggestion/Queue involvement.

### Timeouts

- Lobby/default 60, environment 120, ballot 120, fighters 180, AI admission 60, worker 600/840, Bot 120/900 as above.

### User-visible result

- Completed narration and exact winner mentions, or localized no-victor text when `winners=[]`.

### Invariant checked

**Success requires expected task/graph/revision correlation and Bot-authenticated delivery.** Worker progress never completes a workflow; interaction tokens are not delivery references.

---

## Invariant B — User abort

### Ordered steps

1. Current owner presses Abort during lobby, collection, ballot, environment task, or battle task.
2. Bot acknowledges via its authenticated message surface, disables components, and marks the session terminal.
3. If an AI task is expected, Bot calls `revoke(task_id, terminate=True)`, removes expected/map entries, and releases the node slot.
4. Bot decrements `in_flight_workflows` exactly once. Any later progress/result for the removed ID is discarded.

### Durable writes

- None required beyond logs.

### Timeouts

- Immediate relative to user action; no synthetic worker result is required.

### User-visible result

- Lobby/UI closed with abort acknowledgement (localized copy is implementation).

### Invariant checked

**Owner Abort is available in every nonterminal phase and never leaves workflow/slot ownership behind.**

---

## Invariant C — Human-phase timeout

### Ordered steps

1. Lobby expiry auto-starts the current valid roster.
2. Environment/fighter deadline removes missing submitters when at least one remains; if none remains, abort.
3. One missing ballot response at 120 seconds aborts; it is never inferred as Approve/Decline.
4. AI-slot wait exceeding 60 seconds aborts without publishing.
5. Every terminal path disables active UI and decrements the workflow once.

### Durable writes

- Logs; no success archive.

### Timeouts

- Exact values are the ordered steps above.

### User-visible result

- Timeout / failure message; user may retry when not draining.

### Invariant checked

**Human inactivity cannot hang drain indefinitely and never creates an AI task after the phase has expired.**

---

## Invariant D — AI stall, overall timeout, and late result

1. If no progress/heartbeat arrives for 120 seconds, Bot synthesizes local `bot_stall_timeout`, removes ownership, releases the slot, notifies Discord, and **does not revoke**.
2. If total time reaches 900 seconds, apply the same local cleanup with `bot_task_timeout`, also without revoke.
3. A real result arriving afterward is unknown and silently discarded; it cannot archive, mention, or edit Discord.
4. Hard-stop is different: it purges/revokes and reports `worker_terminated`.

**Invariant:** local timeout is effectively-once user outcome despite a possible still-running worker.

---

## Invariant E — Restart expiry

1. Restart Bot during lobby, collection, ballot, or an AI task.
2. Fresh Bot reconstructs none of the in-memory session/task state and starts with no recovered workflow unit.
3. A stale component interaction receives localized ephemeral session-expired.
4. Late task progress/result is discarded; no archive or Discord delivery occurs.
5. User may create a new lobby once normal admission/cooldown rules permit.

**Invariant:** v1 explicitly abandons sessions on restart; it never guesses state from Discord messages or interaction tokens.

---

## Invariant F — Solo participant

1. Start/expire a lobby with only its owner.
2. Custom environment, ballot (`ceil(1*0.70)=1`), fighter collection, and battle task all accept one participant.
3. Battle result may contain one exact winner ID or an empty list. Empty means the solo fighter died/no victor; Bot does not insert a winner.

**Invariant:** participant minimum is one, but winner minimum is zero.

---

## Invariant G — Unavailable participant

1. Freeze a multi-participant snapshot.
2. If a participant misses environment/fighter collection, remove them if at least one submitter remains; otherwise abort.
3. If a participant already submitted a fighter and later leaves the guild, retain their exact ID/snapshot name in graph input; if they win but cannot be mentioned, render escaped snapshot name.
4. If any active voter misses the ballot, abort rather than shrinking the completed-ballot denominator.
5. If the owner becomes unavailable, transfer Start/Abort ownership to the earliest available joined participant; abort if none exists.

**Invariant:** active roster only shrinks under explicit collector rules; IDs are stable and unavailable users never trigger nickname guessing.

---

## Invariant H — Revision exhaustion

1. Produce an initial candidate and three revision candidates.
2. Complete all four ballots below `ceil(N*0.70)`.
3. Abort after the fourth failure with the final environment visible; do not collect fighters or publish battle.
4. Decrement once and apply cooldown/terminal cleanup.

**Invariant:** threshold exhaustion never bypasses participant rejection.
