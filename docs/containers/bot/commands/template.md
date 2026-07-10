# Command: <Name>

> **Deviation from `containers/template.md` (intentional):** this template documents one **slash command** within the `Bot` container — a paired user-facing interaction flow + the specific service logic it owns — not a standalone service. A command has no environment variables or independent health check of its own; instead it has invocation options, permissions, a visual/interaction flow, and (sometimes) an AI Worker graph behind it. This mirrors how `web/pages/*.md` pairs a dashboard page's UI with the backend endpoints it owns, and how `ai_worker/graphs/template.md` adapts the same base schema for a graph. See `bot/visuals.md` for the shared UI vocabulary (Components V2, design system, shared component catalog) this template's §4 draws from.

## 1. Purpose & Scope

What this command does, in 1–3 sentences. Who can invoke it (everyone / admin-only — see §3), guild-only vs. also usable in DMs, and whether it has subcommands (`/config set`, `/config get`, etc. — per the `app_commands.Group` pattern). If the command has more than one distinct mode of operation (e.g. a lobby that can run in "quick" vs. "custom environment" mode), name the modes here briefly — the detail belongs in §5.

## 2. File Structure

Actual on-disk layout under `bot/modules/commands/<cmd>/` (per `architecture.md`'s file structure), with a comment on each file's role — mirroring `containers/template.md` §2, adapted to the command-specific tree:

```
commands/<cmd>/
├── command.py          # Slash command registration + top-level orchestration
├── models.py            # Command-specific data models (not shared elsewhere)
├── UI/                   # Command-specific UI — only what isn't promoted to bot/visuals.md
│   ├── embeds/
│   ├── modals/
│   └── views/
└── service/              # Command's own business logic, delegates to src/shared/ where applicable
```

Note which files are actually used vs. omitted for this command (e.g. a command with no interactive follow-up may have no `UI/views/`), and call out anything that deviates from this default shape.

## 3. Invocation & Options

| | |
|---|---|
| **Command** | e.g. `/quick-battle` |
| **Subcommands** | List, or "None" |
| **Source** | e.g. `bot/modules/commands/battle/command.py` |

**Options:**

| Name | Type | Required | Choices | Description |
|---|---|---|---|---|
| | | | | |

Note which options (if any) use `@app_commands.autocomplete` or `@app_commands.choices`, and whether any option's value is itself locale-dependent (per `contracts/localization.md`).

## 4. Permissions & Access Control

Discord-level permission checks (`@app_commands.checks.has_permissions`, `@app_commands.default_permissions`, `@app_commands.guild_only()`), any custom checks (e.g. "guild must be AI-enabled," per the legacy `enabled`/`AIEnabled` guild config keys), and cooldowns (`@app_commands.checks.cooldown`). State what a non-permitted user actually sees (silent failure vs. an explicit ephemeral message) — this feeds §11.

## 5. Visuals Used

Which pieces from `bot/visuals.md`'s shared catalog this command composes (naming the specific shared component, e.g. `LobbyView`, `ConfirmationView`, `TaskProgressContainer`), and which UI is specific enough to this command that it lives in its own `commands/<cmd>/UI/` instead (per `architecture.md`'s file structure) and is described here rather than promoted to `visuals.md`. State explicitly whether each piece is built on Components V2 (`LayoutView`/`Container`) or classic `Embed`+`View` — per `visuals.md` §1, V2 is the default for anything beyond a single trivial response; note the reason if a piece deliberately stays classic.

## 6. Interaction Flow

Step-by-step description of the command from invocation to completion, including every button click, select, and modal submit round-trip, and what changes on screen at each step (an edited message vs. a new ephemeral followup vs. a DM). **A diagram is required whenever the flow is more than a single request/response** (per `visuals.md` §1's confirmed decision) — a Mermaid state/sequence diagram, in the same spirit as `graphs/template.md` §4, showing entry point(s), branches (e.g. "custom environment" vs. not), loops (e.g. a lobby's join/leave tick), and every terminal state (success, abort, timeout). A command with a genuinely single-response flow (e.g. `/ping`) should say so explicitly instead of omitting the section.

## 7. AI / Graph Integration

If this command triggers an `ai_worker` task: which graph (`graphs/environment.md` / `graphs/battle.md`), the exact input contract sent (per that graph's own §2 Input State), and how `contracts/task_progress.md`'s phase vocabulary (`queued`/`launching`/`composing`/`refining`/`finishing`) maps to visible UI changes (e.g. which `TaskProgressContainer` state each phase renders as, per `visuals.md`). If this command has **no** AI Worker dependency, state that explicitly (`N/A — no ai_tasks message is ever sent by this command`) rather than omitting the section — same convention `azure.md`/`ai_worker.md` already use for "no dependency" cases.

## 8. Backend / Service Logic

The command's own `service/` layer (per `architecture.md`'s `commands/<cmd>/service/` file structure) — what each file is responsible for, and which parts are pure Discord-side orchestration vs. delegate to `src/shared/` (e.g. `src/shared/azure/services/`).

## 9. Data Read/Written

| Destination/Source | Channel | Format | Trigger |
|---|---|---|---|

Azure resources (Cosmos/Blob/Queue/Table — link to `azure.md` §3, don't redefine variables), RabbitMQ (`ai_tasks`/`ai_tasks_results`, if §6 applies), and Mosquitto topics this command's flow touches directly (not the container-wide ones already covered by `discord_bot.md`).

## 10. Localization

Which `lang/<locale>.json` keys or key-namespace this command's UI strings live under (per `contracts/localization.md`), and whether any AI-generated content this command displays uses `language_locale` separately from the Bot UI strings (same contract, §2's "two systems" split).

## 11. Logging

Command-specific log tags beyond the project-wide format (`architecture.md`'s Mosquitto section) — at minimum note whether `command`, `guild_id`, `user_id`, and (if §6 applies) `task_id` are included, consistent with `ai_worker`'s own per-graph logging recommendations.

## 12. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|

What the user actually sees for each failure — permission denial, cooldown hit, a modal timeout, an AI task failure/timeout (§6), a Discord API error (e.g. interaction token expiry on a very long-running flow). If a failure mode is a known gap inherited from `discord_bot.md` or a contract doc rather than something new here, link back instead of re-describing it.

## 13. Dependencies

| Dependency | Used for | Notes |
|---|---|---|

Other commands this one links to or shares state with (e.g. `/suggest`'s follow-up flow), shared UI (`visuals.md`), graphs/contracts (§6), and any cross-container dependency.

## 14. Open Items / Future Work

Explicitly undecided things specific to this command. Don't restate `discord_bot.md`'s or `visuals.md`'s container-wide open items unless this command adds a command-specific angle on one of them.
