# Contract: Battle Result Archive (Blob Storage)

> **New this revision — closes P0.5.4.** Canonical storage for Discord battle story text + metadata after Scenario 2 delivery is ready. Owner: **Bot**.

---

## 1. Owner and Trigger

| | |
|---|---|
| **Owner** | `Bot`, after Discord delivery is ready (Scenario 2 / `bot/commands/quick-battle.md` step 8) |
| **Trigger** | Successful consume of `ai_tasks_results` for a `battle` graph **and** the result message is ready to post (archive may run immediately before or after the Discord send; Discord success is not rolled back if archive fails — §4) |

---

## 2. Storage Location

| | |
|---|---|
| **Container** | `AZURE_BATTLE_ARCHIVE_CONTAINER` (default `battle-results`) |
| **Story object** | `{guild_id}/{yyyy}/{mm}/{task_id}.txt` — UTF-8 plain text body = story text; `Content-Type: text/plain; charset=utf-8` |
| **Metadata object** | `{guild_id}/{yyyy}/{mm}/{task_id}.meta.json` — JSON metadata below |

`yyyy`/`mm` are UTC from `created_at`.

### Metadata schema

```json
{
  "schema_version": 1,
  "task_id": "…",
  "guild_id": "…",
  "graph": "battle",
  "winners": ["discord_player_id", "…"],
  "created_at": "2026-07-15T17:02:00Z",
  "content_type": "text/plain; charset=utf-8"
}
```

`winners` contains only exact Discord participant IDs from the battle graph's input `Fighter.player_id` values. It is empty when `outcome_type == "none"`. Nicknames, display names, invented fighter IDs, fuzzy matches, and unknown IDs are never archived.

---

## 3. Idempotency

Overwrite by `task_id` path is **idempotent** — a retry of the same archive write replaces the same two objects. Safe under at-least-once result handling.

---

## 4. Failure Behavior

**Best-effort:** if Discord delivery succeeded (or is proceeding) and the Blob archive fails, log **ERROR** and **continue**. Do not roll back or withhold the Discord result. Archive is not on the user-critical path.

---

## 5. Retention and Privacy

| | |
|---|---|
| **Retention (v1)** | **Unbounded** — no automatic purge in v1. (Explicit alternative not chosen: 365d.) |
| **Privacy** | May contain user-generated prompts/story text. Treat as user content. **Never** store `api_key`, bot tokens, webhook URLs, or other secrets in archive objects. |

---

## 6. Schema Evolution

`schema_version` on `.meta.json`; reject unknown on writers; Web ±1 if it ever reads archives (`drain_status.md` §5 / P0.5.5).

---

## 7. Related

| Concern | Canonical doc |
|---|---|
| Command flow step 8 | `bot/commands/quick-battle.md` §9 |
| Azure env / RBAC | `azure.md` §3/§4 |
| Library wrapper | `azure.md` `guild_logs.py` |
