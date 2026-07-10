# Service: Bot

> **Scope note, mirroring `web.md`'s own framing for `web/pages/*.md`:** this doc covers only what's shared across the whole `Bot` container — Gateway lifecycle, guild onboarding, the AI task pipeline's Bot-side plumbing, background polling, health/telemetry, and update behavior. It deliberately does **not** re-describe any single command's own flow, permissions, or visuals — those live in `bot/commands/quick-battle.md`, `bot/commands/config.md`, `bot/commands/suggest.md`. Shared UI component *designs* (embeds/views/modals reused across commands) live in `bot/visuals.md`, not here. This doc is what those files point back to for anything container-level.

---

## 1. Responsibility

`Bot` is the Discord Interface (`architecture.md`'s Container Breakdown) — the **only** component in the system that connects to the Discord Gateway. Concretely, it owns:

- The Gateway connection lifecycle itself, gated by `Head`'s leader signal (§6.1) — never self-activating.
- Slash command registration/dispatch and localization setup (each command's own logic lives in its own doc, §6 here only covers the shared plumbing every command sits on top of).
- Guild onboarding and configuration persistence — one Cosmos DB document per guild (`contracts/guild_config.md`), replacing legacy's per-guild local JSON file entirely.
- The Bot-side half of the AI task pipeline: publishing `ai_tasks`, consuming `ai_tasks_results`, and tracking best-effort progress ticks to drive a live Discord status bar (§6.3, `contracts/task_progress.md`).
- Polling Azure Queue Storage for suggestion-response notifications and relaying them back to the original suggester (§6.6).
- Reporting its own liveness/latency to `Head` (Mosquitto heartbeat) and, via the shared `status.py` document, to `Web` (§6.3).

`Bot` has no AI logic of its own (that's `AI Worker`'s job entirely) and no leader-election logic of its own (that's `Head`'s) — it is purely the Discord-facing edge of the system, reacting to signals from `Head` and results from `AI Worker`/`RabbitMQ`.

---

## 2. File Structure

```
src/bot/
├── Dockerfile
├── entrypoint.sh
├── main.py                        # Entry point: waits for Head signal (§6.1), then runs the bot
├── modules/
│   ├── client.py                  # discord.py Bot subclass — setup_hook, on_ready, translator wiring
│   ├── configs/
│   ├── UI/                        # Bot-scope shared UI elements — see bot/visuals.md for design
│   │   ├── embeds/
│   │   ├── modals/
│   │   └── views/
│   │       └── task_progress_container.py   # TaskProgressContainer — plumbing lives here (§6.3), concrete render design in visuals.md §3
│   ├── events/
│   │   ├── guild_events.py        # on_guild_join / on_guild_update / on_guild_remove — writes contracts/guild_config.md's document (§6.2)
│   │   └── control_events.py      # control/bot/activate | control/bot/drain | control/bot/stop subscriber (§6.1, §6.5)
│   ├── services/                  # NEW in this revision — Bot-container-wide background infra, not owned by any single command
│   │   ├── task_tracker.py        # Local task_id map + progress/ai_worker/# subscription (§6.3)
│   │   ├── heartbeat.py           # status/bot/heartbeat publisher + periodic status.py push (§6.3)
│   │   └── guild_sync.py          # Periodic reconciliation sweep for Discord-side guild metadata (§6.2)
│   └── commands/                  # Bot commands — see each command's own doc
│       ├── suggestions/           # /suggest — bot/commands/suggest.md. queue_poller.py (§6.6) lives here, per architecture.md's existing tree
│       ├── battle/                # /quick-battle — bot/commands/quick-battle.md
│       └── config/                # /config — bot/commands/config.md
└── localization/
    ├── handler.py
    └── lang/                       # en.json, es.json, ua.json — contracts/localization.md
```

> **Correction applied in this revision:** `architecture.md`'s Project File Structure had no home for genuinely container-wide background infra (task tracking, heartbeat, guild sync) that isn't any single command's responsibility — everything under `modules/` was either `client.py`-level or per-command. `modules/services/` is added here (and should be mirrored into `architecture.md`) to close that gap, following the same "shared, not command-specific" reasoning `ai_worker/nodes.md` already used to justify its own `nodes/` folder.

---

## 3. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_BOT_TOKEN` | Yes | — | The Discord bot token used to connect to the Gateway. **Previously undocumented anywhere in the project** — surfaced and formalized here; flagged in §13 since no other doc assumed a different name for it, but this is the first place it's been written down at all. Never logged (§7). |
| `BOT_MOSQUITTO_HOST` | No | `mosquitto` | Hostname of the local Mosquitto broker. Formalizes the variable `mosquitto.md` §3 previously flagged as expected-but-open. |
| `BOT_MOSQUITTO_PORT` | No | `1883` | Mosquitto broker port. |
| `BOT_RABBITMQ_HOST` | No | `rabbitmq` | Hostname of the local RabbitMQ broker. Formalizes the variable `rabbitmq.md` §3 previously flagged as expected-but-open. |
| `BOT_RABBITMQ_PORT` | No | `5672` | RabbitMQ broker port. |
| `BOT_QUEUE_POLL_INTERVAL_SEC` | No | `300` | How often `Bot` polls Azure Queue Storage for suggestion-response notifications (§6.6). Formalizes the "every 5 minutes" already stated in `architecture.md`'s Data Storage table. |
| `BOT_GUILD_SYNC_INTERVAL_SEC` | No | `3600` | How often the periodic guild-metadata reconciliation sweep (§6.2, `contracts/guild_config.md` §4) runs, on top of the `on_guild_join`/`on_guild_update` event-driven writes. Hourly default — guild metadata (mainly `member_count`) doesn't need tighter freshness than that. |
| `BOT_HEARTBEAT_INTERVAL_SEC` | No | `30` | Cadence of the `status/bot/heartbeat` publish (§6.3) — matches the order of magnitude `head.md` §3 already uses for its own election heartbeat. |
| `BOT_STATUS_PUSH_INTERVAL_SEC` | No | `60` | Cadence of pushing the same heartbeat snapshot into the shared `status.py` cloud document (§6.3), so `Web`'s Dashboard can read it without any Mosquitto access. Deliberately slower than the Mosquitto heartbeat itself — `Head`'s own Table/Blob batching already uses this order of magnitude (`head.md` §3's `HEAD_TELEMETRY_BATCH_INTERVAL_SEC`), and this is the same class of "batched cloud write," not a live stream. |
| `BOT_SHUTDOWN_GRACE_SEC` | No | `30` | On `control/bot/stop` (§6.5), how long `Bot` waits for any still-in-flight `ai_tasks_results` before giving up on a clean response and disconnecting anyway. Formalizes `architecture.md`'s "waits a little bit" phrasing into an actual bounded value. |

> **Azure configuration lives in `azure.md`, not here.** Per that doc's §4, `Bot` depends on **Queue Storage** and **Cosmos DB** — all Azure authentication and endpoint variables are defined once in `azure.md` §3.
>
> **`GEMINI_API_KEY` is not a `Bot` environment variable.** Unlike `AI Worker` (`ai_worker.md` §3), `Bot` never holds a project-wide Gemini key — every Gemini call `Bot` itself makes (only `/config`'s model-listing call, `bot/commands/config.md` §6) uses the *guild's own* staged key, read from `contracts/guild_config.md`'s `api_key` field, never an environment variable.

---

## 4. Inbound Communication

| Source | Channel | Format | Trigger |
|---|---|---|---|
| Discord Gateway | WebSocket | Interactions (slash commands, component/modal submits) | Continuous, only once activated (§6.1) |
| `Head` (local) | Mosquitto, `control/bot/activate` \| `control/bot/drain` \| `control/bot/stop` | Plain signal | Leader election / update sequence / loss-of-internet (§6.1, §6.5 — corrected three-action model, was two in `head.md`/`mosquitto.md` before this revision) |
| `AI Worker` | RabbitMQ, `ai_tasks_results` (consume, correlated via `reply_to`) | Finished graph output — `Environment` or `story`+`winners` | On task completion (success or synthetic error, per `rabbitmq.md` §6) |
| `AI Worker` | Mosquitto, `progress/ai_worker/#` (wildcard subscribe) | Phase-update message (`contracts/task_progress.md` §4) | On each phase transition of a task `Bot` itself submitted |
| Azure Queue Storage | Polled (not pushed) | Suggestion-response notification event | Every `BOT_QUEUE_POLL_INTERVAL_SEC` (§6.6) |

---

## 5. Outbound Communication

| Destination | Channel | Format | Trigger |
|---|---|---|---|
| Discord Gateway | WebSocket | Slash command responses, DMs, guild messages, embed/view edits | Continuous |
| RabbitMQ | `ai_tasks` (publish, with `reply_to` set) | AI task payload (`graphs/environment.md` §2 / `graphs/battle.md` §2 input contract, plus the `graph` discriminator) | On `/quick-battle`'s environment/battle graph invocations (`bot/commands/quick-battle.md`) |
| Mosquitto | `status/bot/heartbeat` | `{latency_ms, guild_count}` (§6.3 — extended beyond a bare ping, confirmed decision) | Every `BOT_HEARTBEAT_INTERVAL_SEC` |
| Mosquitto | `logs/<level>/bot` | Structured log string, per §7 | On every log emission |
| Azure Cosmos DB | Upsert | Guild config document writes (`contracts/guild_config.md` §4) | `/config` Apply, `on_guild_join`/`on_guild_update`/periodic sweep (§6.2), `on_guild_remove` (`left_at`) |
| Azure Cosmos DB | Insert | Suggestion ticket document (`bot/commands/suggest.md` §9) | `/suggest` modal submit |
| Azure Queue Storage | `receive_messages` (poll, then delete on ack) | — | Every `BOT_QUEUE_POLL_INTERVAL_SEC` (§6.6) |
| Azure Blob Storage (`status.py` document) | Upsert | Bot status snapshot — extends the existing `bot.json`-style document with `{latency_ms, guild_count, updated_at}` (§6.3, confirmed decision) | Every `BOT_STATUS_PUSH_INTERVAL_SEC` |
| Discord (DM) | HTTPS (via Gateway) | Suggestion-response notification to the original suggester (§6.6, confirmed decision — not an admin channel) | On a new Queue Storage event resolving to a known suggestion |

---

## 6. Internal Logic

### 6.1 Activation Lifecycle

`Bot`'s container process starts alongside every other local container (`docker-compose up`), but **does not call `bot.run()`** (i.e. does not open the Discord Gateway connection at all) until it receives `control/bot/activate` from the local `Head` (`head.md` §5/§6). Until then, it sits idle, already subscribed to `control/bot/*` on Mosquitto.

On `activate`:
1. Connects to the Discord Gateway.
2. Sets up localization (`self.l10n`, `tree.set_translator` — carried forward from legacy as-is, per `contracts/localization.md`).
3. Syncs the slash command tree (`tree.sync()`).
4. Starts the three background services under `modules/services/` (§6.2, §6.3, §6.6) and the Queue Storage poller (§6.6).
5. Clears `bot.draining` if it was set (see §6.4 — an `activate` always follows a full container recreate, so there is no scenario where a stale `draining` flag needs to survive across it; this is an assumption, not an explicit signal from `Head`, flagged in §13).

### 6.2 Guild Lifecycle & Config Persistence

All guild state lives in the single Cosmos DB document defined by `contracts/guild_config.md` — no local files, per that contract's replacement of legacy's per-guild JSON.

- **`on_guild_join`:** creates a new document with the confirmed defaults (`contracts/guild_config.md` §5) plus the Discord-sourced fields (`name`, `icon_url`, `member_count`, `owner_id`, `created_at`). Also sends the existing legacy welcome flow (`WelcomeView`/`WelcomeLocaleSelect`, unchanged — see `bot/visuals.md`'s component catalog).
- **`on_guild_update`:** re-writes only the Discord-sourced fields that changed (name/icon/owner) — never touches the admin-configured fields.
- **`on_guild_remove`:** sets `left_at` to now. **Does not delete the document** — soft-delete, per `contracts/guild_config.md` §6, so re-inviting the bot later doesn't lose history.
- **Periodic reconciliation sweep** (`modules/services/guild_sync.py`, every `BOT_GUILD_SYNC_INTERVAL_SEC`): iterates `bot.guilds` and re-writes the Discord-sourced fields for every guild currently joined. This is the confirmed third trigger (alongside the two event handlers above) — it exists specifically because Discord has no push event for `member_count` drift on its own, and it also catches anything missed while `Bot` was offline between the two event-driven writes.

### 6.3 Task Tracking, Heartbeat, and the Status-Bar Backbone

This section is the shared **plumbing** underneath the live task-progress status bar — the concrete checklist *rendering* design (icons, layout, the exact mockup) is owned by `bot/visuals.md` §3's `TaskProgressContainer` entry, not duplicated here, per this project's "define once" convention.

**Local task map** (`modules/services/task_tracker.py`): an in-memory dict, `task_id -> TaskRecord`:

```python
class TaskRecord(TypedDict):
    command: str                              # e.g. "quick-battle" — which command owns this task
    discord_message_ref: Any                  # the message/interaction Bot needs to edit as phases progress
    graph: Literal["environment", "battle"]
    current_phase: str                        # queued | launching | composing | refining | finishing
    phase_history: list[tuple[str, str]]      # ordered (phase, ISO-8601-timestamp) pairs — every phase this task has visibly entered, used both to render each checklist line's state and to compute Duration
    created_at: str                            # ISO 8601 — set the moment Bot publishes the ai_tasks message, before AI Worker's own first "launching" tick even arrives
```

- **On publish:** `Bot` creates the `TaskRecord` immediately when it pushes the `ai_tasks` message — `current_phase` starts at `queued` locally, matching `task_progress.md` §4's phase vocabulary, even though that first tick technically originates from `Bot` itself, not `AI Worker`.
- **On a `progress/ai_worker/<task_id>` tick:** looks up `task_id` in the map (silently discards a miss, per `task_progress.md` §7's already-documented "no error" stance), appends `(phase, timestamp)` to `phase_history`, updates `current_phase`, and triggers `TaskProgressContainer` to re-render against the updated record.
- **On the terminal `ai_tasks_results` message (RabbitMQ):** the `TaskRecord` is removed from the map — this is the **only** cleanup path. A task whose result never arrives (e.g. `Bot` restarted mid-task) leaves a dangling entry until process restart — flagged as an open item (§13), the same shape of gap `task_progress.md` §7 already accepts for progress ticks, now extended to the terminal result too.

**Heartbeat / status reporting** (`modules/services/heartbeat.py`), resolving `web.md` §6.2's previously-flagged "no source for Bot gateway latency" gap in one pass:
- Every `BOT_HEARTBEAT_INTERVAL_SEC`, publishes `status/bot/heartbeat` on Mosquitto with `{latency_ms, guild_count}` — `latency_ms` from discord.py's own `bot.latency`, `guild_count` from `len(bot.guilds)`. Consumed by `Head` the same way every service's heartbeat is (`mosquitto.md` §4/§5) — this is a superset of the bare liveness ping other services use, not a different mechanism.
- Every `BOT_STATUS_PUSH_INTERVAL_SEC`, upserts the same `{latency_ms, guild_count}` pair (plus `updated_at`) into the shared `status.py` cloud document (`azure.md` §2's `src/shared/azure/services/status.py`) — this is the confirmed decision that actually gets this data somewhere `Web` can read it, since `Web` has no Mosquitto access at all. `Web`'s Dashboard page should read from here once implemented — see the corresponding update to `web/pages/dashboard.md` §9.

### 6.4 Command Availability During Drain

**Confirmed decision (project owner):** rather than a single blanket behavior, `bot.draining` is one global, container-wide flag (set by `control/bot/drain`, §6.5), but **whether a given command actually respects it is a per-command opt-in parameter**, not a hardcoded blanket rule.

`ProcessCommand` (`modules/utils.py`) gets one new parameter:

```python
def ProcessCommand(bot, ..., blocked_during_drain: bool = False):
    ...
```

- Default is `False` — **most commands keep working during a drain window**, since drain exists specifically to protect in-flight AI generation, not to freeze the whole bot.
- `/quick-battle` is the one confirmed opt-in (`blocked_during_drain=True`) — the only command with real `AI Worker`/RabbitMQ cost, matching `architecture.md`'s original framing ("ceasing new `/quick-battle` acceptance on drain"). `bot/commands/quick-battle.md` §4 should state this explicitly (cross-referenced).
- `/config` and `/suggest` stay unblocked — neither touches `AI Worker` (`/config`'s Gemini call is a direct, cheap model-listing request; `/suggest` never calls Gemini at all), so there's no reason to interrupt admin or feedback flows during a brief planned-update drain.

When a `blocked_during_drain=True` command is invoked while `bot.draining` is set, `ProcessCommand` should respond with a localized "temporarily unavailable, try again shortly" ephemeral message instead of proceeding — exact copy/localization key not yet written, flagged in §13.

### 6.5 Two Distinct `Head` → `Bot` Signals (Correction Applied This Revision)

Reading `architecture.md`'s Bot section against `head.md` §9 surfaced a real inconsistency, not just missing detail: `architecture.md` defines `control/bot/drain` as *"ceasing new `/quick-battle` acceptance"* (i.e. §6.4's soft, per-command gate) — but `head.md` §9 also reuses the exact same action name for a much stronger case: *"immediately signal local Bot to stop... with immediate effect"* when `Head` loses internet connectivity as leader. A soft per-command gate and "stop entirely, disconnect from Discord" are not the same operation, and conflating them under one topic action would mean every future reader has to guess which behavior a given `drain` message actually means.

**Resolution applied in this revision** — two distinct actions under `control/bot/<action>`:

| Action | Meaning | Used when |
|---|---|---|
| `control/bot/drain` | Soft gate — sets `bot.draining` (§6.4). `Bot` stays connected to Discord; only opted-in commands (`/quick-battle`) stop accepting *new* invocations while any already-in-flight `ai_tasks` finish. | Planned update sequence (`head.md` §6, `DRAINING` state) |
| `control/bot/stop` | Hard stop, sequence corrected and confirmed this revision: (1) `RabbitMQ`'s `ai_tasks` queue is purged and any actively-running `AI Worker` execution is terminated (`rabbitmq.md` §6, `revoke(terminate=True)`), producing a synthetic error result on `ai_tasks_results` for every affected task; (2) `Bot` waits up to `BOT_SHUTDOWN_GRACE_SEC` (§3) and, for every task still in its local task map (§6.3), sends the resulting error back to that task's **originating Discord thread/interaction** — this must happen **before** step 3, since `Bot` cannot reach Discord at all afterward; (3) only then does `Bot` disconnect from the Gateway entirely. This is a correction to the previous revision's ordering, which described the Gateway disconnect and the error-response attempt without specifying which happens first. | `Head` losing internet connectivity while leader (`head.md` §6/§9's "immediately stop `Bot`" case) |

`head.md` §5 and `mosquitto.md` §4/§5's topic tables should be read as updated to list `control/bot/stop` alongside `control/bot/activate`/`control/bot/drain` — see the corresponding edits applied to both docs in this same revision. **This split is now confirmed (project owner) as of this revision** — previously flagged in §13 as a proposed-but-unconfirmed correction; the exact hard-stop sequencing above (purge + terminate + notify-before-disconnect) is the specific confirmation.

### 6.6 Suggestion Queue Storage Polling

`modules/commands/suggestions/service/queue_poller.py` (file structure per `architecture.md` — kept under the `suggestions` command folder since this is suggestion-domain logic, even though it runs as a background task rather than reacting to an interaction) polls Azure Queue Storage every `BOT_QUEUE_POLL_INTERVAL_SEC`.

On receiving a notification event (`architecture.md` Scenario 3 / `web/pages/suggestions.md` §5 — pushed when an admin responds to a suggestion on `Web`), `Bot`:
1. Looks up the referenced suggestion's `contact` info — already embedded in the ticket document `/suggest` wrote (`bot/commands/suggest.md` §9).
2. Sends a **DM to the original suggester** via `contact.user_id` — **confirmed decision, correcting `architecture.md`'s Scenario 3 step 7**, which said *"sends an Embed to the Discord Admin Channel"*. That line is now understood to be imprecise legacy-carryover phrasing; the actual behavior (per the suggestion payload's own `contact.method: "dm"` field, already committed to in `bot/commands/suggest.md`) is a direct DM, not a guild channel post. `architecture.md` itself has been corrected to match — see that doc's own edit in this revision.
3. Localizes the DM using the guild/locale info already embedded in the suggestion record (matches `web/pages/suggestions.md` §6's auto-feedback localization note).

**On DM failure** (the user has DMs closed, left every mutual guild, etc.) — no fallback is defined. Flagged as an open item (§13), same shape of gap as every other "notification delivery can silently fail" case in this project.

---

## 7. Logging

Same shared structured format as every other service:

```
[%time%][%level%][bot][%file/module%]<trace_id, guild_id, user_id, command, task_id>: [%message%]
```

- **Recommended minimum tags:** `guild_id`, `user_id`, `command` on every command-originated log line; `task_id` additionally on anything related to the AI task pipeline (§6.3).
- **Sensitive data exclusion:** `DISCORD_BOT_TOKEN` must never be logged, at any level. A guild's `api_key` (`contracts/guild_config.md`) must never be logged either, at any level — even though it's stored plaintext in Cosmos DB (§3, that contract's §7), logging it would additionally expose it in `Head`'s aggregated Blob Storage log archive, a strictly worse exposure surface. This restates `bot/commands/config.md` §11's own logging note at the container level, since it applies to every code path that ever reads the field back, not just `/config` itself.

---

## 8. Metrics

**Candidate `Bot`-owned metrics** (not yet wired to any storage — same unresolved shape of gap `ai_worker.md` §8 already flags for its own container): `commands_invoked_total` (per command), `ai_tasks_published_total`, `suggestion_tickets_created_total`, `active_lobby_count` (`/quick-battle`-specific, per `bot/commands/quick-battle.md`).

The two metrics that **do** have a defined transport as of this revision are `latency_ms` and `guild_count` (§6.3) — everything else listed above has no path to any persistent store today, exactly the same open shape as `ai_worker.md` §8's own unresolved metrics gap.

---

## 9. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Discord Gateway disconnects unexpectedly (network blip, not a `Head`-driven stop) | discord.py's own connection-state events | Relies on discord.py's built-in automatic reconnect — no `Bot`-specific override decided. |
| Cosmos DB unreachable (guild config or suggestion read/write) | Exception from `cosmos.py` (`azure.md` §9) | Surfaced per-command — see each command's own Failure Modes section (`config.md` §12, `suggest.md` §12). No container-wide fallback beyond what each command already documents. |
| Azure Queue Storage poll fails (§6.6) | Exception from `queue.py` | Skip this poll cycle, retry at the next `BOT_QUEUE_POLL_INTERVAL_SEC` — same "skip and retry" pattern `head.md` §9 already uses for its own GitHub Releases polling. |
| A DM to a suggestion's original author fails (§6.6) | `discord.Forbidden` or similar from the DM send call | **Not handled** — no retry or fallback defined. Flagged in §13. |
| Mosquitto unreachable — affects `progress/ai_worker/#` reception, heartbeat publish, and `control/bot/*` reception | Publish/subscribe failure | Progress/heartbeat: must never block (`task_progress.md` §7) — log and continue, the status bar simply stops updating. `control/bot/*` reception: **no documented fallback** if Mosquitto is down exactly when `Head` needs to signal `Bot` — inherited, unresolved gap already flagged in `mosquitto.md` §9, restated here since it's `Bot`'s own reception side of that same gap. |
| `Bot` process crashes or restarts mid-task (after publishing `ai_tasks`, before consuming the matching `ai_tasks_results`) | N/A — no detection mechanism | The in-memory task map (§6.3) is lost entirely. On restart, if the matching `ai_tasks_results` message still arrives, it has no `TaskRecord` to update and is effectively orphaned — no reconciliation exists. Same class of gap `task_progress.md` §7 already accepts for dropped progress ticks, now extended to the terminal result too. Flagged in §13. |
| `RabbitMQ` unreachable when publishing `ai_tasks` | Publish failure | **Not specified** — inherited, unresolved gap already flagged in `rabbitmq.md` §9 (no standalone-RabbitMQ-outage behavior is documented anywhere yet). |

---

## 10. Dependencies

| Dependency | Required for | Behavior if unavailable |
|---|---|---|
| Discord Gateway | Everything — this is `Bot`'s entire reason to exist | See §9's Gateway-disconnect row. |
| `RabbitMQ` (local) | Publishing `ai_tasks`, consuming `ai_tasks_results` (`/quick-battle` only) | See §9. |
| `Mosquitto` (local) | `control/bot/*` reception, `progress/ai_worker/#` reception, `status/bot/heartbeat` publish, `logs/*/bot` publish | Best-effort only, per §9 — never blocks command handling itself. |
| Azure Cosmos DB | Guild config (`contracts/guild_config.md`) + Suggestions (`bot/commands/suggest.md`) | See §9 and each command's own doc. |
| Azure Queue Storage | Suggestion-response polling (§6.6) | See §9. |
| Azure Blob Storage (`status.py` document) | Status snapshot push (§6.3) | Not specified beyond the generic Blob Storage failure mode already in `azure.md` §9 — a failed push simply means `Web`'s Dashboard sees a stale `updated_at` until the next successful cycle. |
| Google Gemini API (direct, not via `AI Worker`) | `/config`'s model-listing call only (`bot/commands/config.md` §6) | See that doc's own §12 — unrelated to `AI Worker`'s separate Gemini usage. |
| `discord.py >= 2.6` | Gateway connection, Components V2 UI (`bot/visuals.md` §1) | Hard dependency, not something the system degrades gracefully without. |
| `Head` (local, via Mosquitto only — no direct call) | Activation/drain/stop signals (§6.1, §6.5) | Without `Head` ever signaling `activate`, `Bot` simply never connects to Discord at all — this is by design, not a failure mode to recover from. |

---

## 11. Health Check

No HTTP endpoint — unlike `Head`/`Launcher`, `Bot` exposes nothing for another service to poll directly. Its only liveness signal is the Mosquitto `status/bot/heartbeat` (§6.3), consumed by `Head` the same generic way `AI Worker`'s heartbeat is (`mosquitto.md` §4/§5) — now carrying `{latency_ms, guild_count}` rather than a bare ping, per this revision's confirmed extension.

Whether a more detailed self-check (Discord Gateway session state, RabbitMQ/Cosmos connectivity) should be folded into that same heartbeat payload, or exposed some other way, is undecided — mirrors `ai_worker.md` §11's identical open item on its own side.

---

## 12. Versioning & Update Behavior

- `Bot` shares the coordinated version tag with `Head`, `AI Worker`, and `Web` (`Launcher.md` §12) — it does not version independently.
- Participates in the `DRAINING` → `UPDATING` sequence (`head.md` §6) via `control/bot/drain` (§6.4) during a planned update, and via the separate `control/bot/stop` (§6.5) only in the unrelated loss-of-internet case — these are not the same signal, per §6.5's correction.
- No persistent state to preserve across a restart beyond what already lives in Cosmos DB (`contracts/guild_config.md`) — the in-memory task map (§6.3) and the `bot.draining` flag (§6.4) are both lost on restart by design, and a fresh instance starts clean once `Head` signals `activate` again.

---

## 13. Open Items / Future Work

*(Additive section, mirroring the same pattern already used in `ai_worker.md` §13 and every `graphs/*.md`'s own Open Items — collecting this doc's own surfaced gaps in one place.)*

- ~~The `control/bot/drain` vs. `control/bot/stop` split (§6.5) is a correction proposed in this revision, not an explicit "project owner confirmed" decision~~ — **confirmed this revision**, including the exact hard-stop sequence (purge `ai_tasks`, terminate in-flight `AI Worker` execution, notify affected threads, then disconnect).
- No fallback exists if a DM to a suggestion's original author fails (§6.6, §9).
- Dangling task-map entries after a `Bot` crash mid-task (§6.3, §9) have no cleanup or reconciliation mechanism — a task whose result arrives after `Bot` has already forgotten about it is simply dropped.
- `DISCORD_BOT_TOKEN` (§3) was a previously-undocumented gap across the entire docs tree, not specific to this revision's scope — formalized here for the first time; worth double-checking no other in-progress doc silently assumed a different variable name for it.
- Bot-owned metrics beyond `latency_ms`/`guild_count` have no transport path (§8) — same unresolved shape as `ai_worker.md` §8.
- No health signal beyond the heartbeat exists or is planned (§11) — same unresolved shape as `ai_worker.md` §11.
- The exact copy/localization key for the "temporarily unavailable during drain" response (§6.4) hasn't been written yet.
