# Contract: Drain Status and Update Coordination

> **New this revision — closes P0.3.** Follows the same style/schema-versioning convention as `contracts/leadership_control.md` and `contracts/task_progress.md`. Per this project's "define once, link elsewhere" convention (`contracts/ai_task.md`'s own opening note), this is the single canonical source for what "drained" means, how `Head` observes drain completion, cancellation behavior on drain timeout, pause acknowledgement, release-broadcast deduplication, and mixed-version compatibility. `containers/head.md`, `containers/bot/discord_bot.md`, and `containers/ai_worker/ai_worker.md` point back here rather than redefining any of it.

---

## 1. Scope: What "Drained" Means

**Core choice (owner-approved): `Head` defines "drained" as the full user-workflow, not just RabbitMQ `ai_tasks`.** Waiting only for RabbitMQ work to empty does not drain the command workflow — an active `/quick-battle` can be sitting in lobby, collection, or voting state with no `ai_tasks` entry in RabbitMQ at all (`bot/commands/quick-battle.md` §6).

`Bot` maintains one authoritative local counter, **`in_flight_workflows`**:

- Every `/quick-battle` lobby, collector, or vote currently open counts as one unit, **and** every `ai_tasks` entry currently open (the existing task map, `discord_bot.md` §6.3) counts as one unit. `in_flight_workflows` is a strict superset of the task map — it must also cover lobby/collector/vote states that never touch RabbitMQ at all.
- Incremented on creation (lobby opened, collector/vote started, or `ai_tasks` published).
- Decremented on terminal resolution: success, user cancel, error, or a user-visible timeout.
- One workflow unit may span multiple sub-stages (e.g. a `/quick-battle` lobby that later opens an environment-approval vote) — it is still counted once per top-level command invocation, not once per sub-stage, since the goal is "is there still a user-visible in-progress command," not a stage-by-stage tally.

On receiving a `draining` grant (`contracts/leadership_control.md` §3.2), `Bot`:

1. Immediately sets `bot.draining = True` (existing behavior — blocks new `/quick-battle` acceptance per `discord_bot.md` §6.4's opt-in mechanism).
2. Begins publishing `status/bot/drain_progress` on Mosquitto (QoS 1, not retained) every `BOT_DRAIN_PROGRESS_INTERVAL_SEC` (default `5`, `discord_bot.md` §3).

### Message: `status/bot/drain_progress`

QoS: `1`. Retain: `false`.

```json
{
  "schema_version": 1,
  "node_id": "node-a",
  "leadership_term": "cd88086a-fd6d-48d4-8446-39523af2bf70",
  "in_flight_workflows": 3,
  "observed_at": "2026-07-15T17:02:00Z"
}
```

`Head`, while in its `DRAINING` state (`head.md` §6), subscribes to this topic instead of inferring drain completion from RabbitMQ queue depth, and transitions to `UPDATING` when `in_flight_workflows == 0` **or** `HEAD_DRAIN_TIMEOUT_SEC` elapses, whichever comes first. This directly resolves the backlog's "waiting only for RabbitMQ work does not drain the command workflow" finding.

---

## 2. Timeout Behavior

On `HEAD_DRAIN_TIMEOUT_SEC` elapsing with `in_flight_workflows > 0`, `Head` proceeds to command the hard-stop sequence — **not** silent abandonment. This resolves the previously-flagged internal inconsistency between "proceed and abandon work" text and "hard-stop purge/terminate only defined for loss-of-internet" text: drain timeout now explicitly escalates into the same hard-stop path already canonical in `contracts/leadership_control.md` §5.3 / `discord_bot.md` §6.5.

**New addition for lobby/collector/vote states specifically (not just `ai_tasks`):** as part of that same hard-stop sequence, `Bot` must also cancel any open component views/collectors it is tracking and edit their messages to a localized "update in progress, please retry" notice, before Gateway disconnect.

Consistent with `contracts/ai_task.md` §8's cancellation matrix: **no `revoke` happens at drain-timeout itself** — that only happens once hard-stop is actually triggered. Drain timeout is the trigger for hard-stop, not a cancellation action in its own right.

---

## 3. AI Worker Pause Acknowledgement

On receiving `control/ai_worker/desired_state = paused` (existing topic, `mosquitto.md` §4), `AI Worker` finishes its current task claim (does not abandon mid-graph) and then stops consuming new claims (`ai_worker.md` §6). Once idle, it publishes a new topic, `status/ai_worker/pause_ack`:

QoS: `1`. Retain: `false`.

```json
{
  "schema_version": 1,
  "node_id": "node-a",
  "paused_at": "2026-07-15T17:02:30Z"
}
```

**This ack is informational/diagnostic only — it does NOT gate `Head`'s drain-complete decision.** `in_flight_workflows == 0` (§1) already covers whether `AI Worker`'s current task has finished, since every `ai_tasks` entry is counted there too. Adding `pause_ack` as a second blocking condition would risk stalling drain if the ack itself is lost over best-effort Mosquitto — deliberately avoided.

**Recovery when an update is abandoned/superseded mid-drain:** `Head` simply re-publishes an `active` grant and a resume (`control/ai_worker/desired_state = running`) — no teardown/unwind needed, since nothing was torn down mid-drain (`PAUSED` never aborts an in-flight claim, `ai_worker.md` §6).

---

## 4. Release-Broadcast Dedup / Concurrent Initiators

### 4a. `update_available` payload (cluster PubSub)

Published by the leader `Head` into `HEAD_PUBSUB_CLUSTER_GROUP` (`cluster`). Canonical schema:

```json
{
  "schema_version": 1,
  "type": "update_available",
  "target_version": "v1.5.0"
}
```

| Field | Rule |
|---|---|
| `schema_version` | Current = `1`. Receivers reject unknown versions. |
| `type` | Literal `"update_available"` — discriminates from `leader_heartbeat` (`contracts/leadership_control.md` §4). |
| `target_version` | Exact Docker-safe SemVer-compatible release tag from `contracts/launcher_ipc.md` §4. **Not** a field named `version`. Invalid tags are ignored/rejected before update admission. |

### 4b. Dedup / concurrent initiators

`update_available` broadcasts become idempotent by `target_version` string: `Head` tracks the last version string it has already acted on (started draining for) and ignores a repeated broadcast for the same version.

- The broadcasting leader emits only tags accepted by the canonical grammar. Automatic GitHub polling always ignores drafts and ignores prereleases by default; parsed SemVer precedence, never lexical ordering, determines whether a release is newer than `APPLICATION_VERSION`.
- Manual `launcher update --version` bypasses `Head` entirely and goes straight to `Launcher`'s already-resolved idempotent `POST /v1/update` (`contracts/launcher_ipc.md`) — no new rule is needed for that path.
- New rule needed only for the `Head`-broadcast path: if `Head` is already `DRAINING`/`UPDATING` for version X and receives a broadcast for a different version Y, it **queues Y as the pending next target** and only begins acting on Y after fully completing the current X cycle (no interruption of an in-progress drain/update).

---

## 5. Mixed-Version Compatibility and Web

All wire contracts (`contracts/ai_task.md`, `contracts/leadership_control.md`, `contracts/task_progress.md`, `contracts/launcher_ipc.md`, `contracts/suggestion.md`, `contracts/telemetry.md`, `contracts/status_document.md`, `contracts/battle_archive.md`, `contracts/log_archive.md`, `contracts/pubsub_live.md`, `contracts/web_auth.md`, `contracts/guild_config.md`, and this contract — including `update_available` and `control/ai_worker/desired_state`) are versioned via their own `schema_version` field (or an equivalent documented format version for plain-text log lines); a receiver must reject/ignore an unknown `schema_version` rather than guess. This rule is stated uniformly and referenced from `architecture.md`'s overview.

For `Web` specifically — independently deployed, with no direct peer protocol to `Bot`/`Head`, only shared Cosmos/Blob/Table/Queue documents and Web PubSub — it must tolerate reading **one prior and one following** `schema_version`/shape of any document it consumes (`guild_config`, `suggestion`, status document, telemetry entities/payloads, queue messages). Additive-only changes are allowed within that compatibility window; a breaking change must introduce a new field name rather than reusing or retyping an existing one.

**Deliberately not decided here:** a cluster-wide update barrier or lockstep mechanism. This tolerant-schema approach is the deliberately chosen, proportionate answer for a single-admin-operated deployment — do not add a barrier on top of it; a future reader should not assume one is still needed.

---

## 6. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `status/bot/drain_progress` is lost/delayed over Mosquitto | `Head` sees no update within its own polling/read cadence | Not treated as an error — `Head` simply keeps waiting up to `HEAD_DRAIN_TIMEOUT_SEC`, then escalates per §2 regardless of whether the last-known count was zero. A single missed tick does not falsely trigger early escalation, since `Head` only acts on timeout expiry or an explicit `in_flight_workflows == 0` observation. |
| `status/ai_worker/pause_ack` is lost | N/A — informational only | No recovery needed; never blocks drain completion (§3). |
| `Bot` itself crashes mid-drain | Grant/watchdog expiry (`contracts/leadership_control.md` §5.4) | Same as any other `Bot` crash — `Head` proceeds through its own failure transitions; the in-memory `in_flight_workflows` counter is lost along with the rest of `Bot`'s in-memory state, consistent with `discord_bot.md` §6.3's existing accepted restart-loses-in-memory-state limitation. |
| Two different `update_available` broadcasts arrive for the same version in quick succession | `target_version` string comparison (§4) | Second (and any further) broadcast for an already-acted-on version is ignored — idempotent by design. |

---

## 7. Open Items

- No persistence for `in_flight_workflows` across a `Bot` restart — consistent with `discord_bot.md` §6.3's existing accepted limitation for the task map, not a new gap introduced by this contract.
- Whether `Head` should log/alert specifically when a drain timeout escalates to hard-stop with `in_flight_workflows > 0` (vs. a clean `== 0` completion) is recommended but not formally specified — an implementation detail, not a contract gap.
