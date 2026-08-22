# Bot: Shared Visual Components (UI)

> **Why this file exists:** `bot/modules/UI/` (per `architecture.md`'s file structure) holds embeds/modals/views reused across more than one command. Per the same convention already established for `web/components.md` (its exact `Web` equivalent) and `ai_worker/nodes.md` (shared LangGraph nodes): a piece used by ≥2 commands gets one definition here; each command's own doc (`commands/<cmd>.md` §4) references it instead of redefining it. UI specific to exactly one command stays documented in that command's own doc instead of being pre-emptively listed here.
>
> **Status of this file:** §1 (architecture decisions) and §2 (design system) are confirmed (project owner). §3 (the actual component catalog) lists *candidates* derived from analyzing the legacy bot's recurring UI patterns — now that `quick-battle.md`, `config.md`, and `suggest.md` are all drafted, most rows have a concrete consumer, but the catalog is still not implemented/named/API-designed. One row (`WizardView`) turned out to describe behavior its only real consumer doesn't actually implement — see the correction note on that row. Treat §3 as a working inventory, not a finished catalog.

---

## 1. Architecture Decisions (confirmed, project owner)

- **Components V2 is the default building block for new UI**, not classic `Embed` + `View`. `discord.py` shipped `ui.LayoutView`/`Container`/`Section`/`TextDisplay`/`Separator`/`MediaGallery` in 2.6 (Components V2) — under this model a message's component tree *is* the message (no separate `embed=`/`view=`), supports up to 40 components (vs. 25 for classic), and allows a per-container accent color rather than one embed-wide color. **Ripple effect applied in this revision:** `architecture.md`'s Technology Stack table now pins `discord.py ≥ 2.6` (was `≥ 2.3`), specifically to guarantee Components V2 availability.
  - **Classic `Embed` + `View` remains valid, not deprecated,** for genuinely trivial single-response cases (e.g. a one-shot ephemeral error message with no interactive components) where standing up a `LayoutView`/`Container` would be pure ceremony. Each command doc's §4 should state which model a given piece uses and why, rather than defaulting silently.
- **Reusable UI is built as a small custom internal layer under `bot/modules/UI/`, not a third-party framework dependency.** Two third-party options were evaluated and explicitly rejected as the foundation (may still be worth reading for API inspiration):
  - `pycascadeui` (CascadeUI) — a Redux-inspired framework with built-in wizard/form/pagination/state-management patterns that map closely onto this project's recurring legacy shapes (see §3). Rejected as a *dependency* specifically because of maturity risk (11 GitHub stars, 0 forks, single maintainer at time of evaluation) for something the entire Bot UI would be built on top of — not rejected because the patterns it demonstrates are wrong.
  - `dpy-layout-builder` — a narrow fluent builder purely for Components V2 nesting syntax. Rejected as unnecessary: it only addresses construction syntax, not the actual recurring problems (wizard chains, lobby state, staged settings) this project needs solved.
  - **Explicitly not pursued:** an external schema (JSON/YAML/etc.) format for defining UI structure. Rejected because interactive components still require Python callbacks bound to guild/business logic regardless of how the static shape is described, so a schema layer would only remove boilerplate from labels/colors/layout while all actual behavior stays in Python — a poor complexity/benefit trade for this project. It would also compete with the existing localization system (`contracts/localization.md`, `lang/<locale>.json`) as a second place claiming ownership of "what text appears where."

## 2. Design System (confirmed, project owner)

**Confirmed decision: use discord.py's default `Color` palette** (`discord.Color.green()`/`.red()`/`.gold()`/`.blurple()`) rather than custom hex values — no bespoke brand palette. Recorded here so every command doc's UI references the same semantic table below instead of picking colors ad hoc.

| Semantic state | Color | Used for |
|---|---|---|
| Success | Discord Green (`discord.Color.green()`) | Completed actions, successful AI task results |
| Error / Failure | Discord Red (`discord.Color.red()`) | Validation errors, permission denial, AI task failure |
| Warning | Discord Gold/Yellow (`discord.Color.gold()`) | Non-fatal issues, cooldown notices |
| Info / Neutral (default) | Discord Blurple (`discord.Color.blurple()`) | Default container accent when no other state applies; matches existing Discord-blurple branding already used informally on the Web dashboard (`web/components.md` §7) |
| Pending / In-progress | Discord Blurple, same as Info | AI task progress container (§3, `TaskProgressContainer`) while in `queued`/`launching`/`composing`/`refining` phases per `contracts/task_progress.md` §4 — deliberately not a distinct color from Info, to avoid over-fragmenting the palette; only Success/Error need to visually stand out from "still working" |

Also proposed, same confirmation status: a standard footer (bot name + version, mirroring `web/components.md` §1's footer) on any container that isn't ephemeral/transient, and no fixed emoji-prefix convention for titles yet (flagged as undecided rather than inventing one).

## 3. Shared Component Catalog (candidates — not yet implemented)

Derived from the legacy bot's recurring UI shapes (`docs/legacy/Old_arch.md`), each mapped to the Components V2 pattern (§1) it should become. **None of these exist yet** — location paths below are proposed, not actual.

| Candidate name | Replaces (legacy) | Pattern | Proposed location | Candidate consumers |
|---|---|---|---|---|
| ~~`WizardView`~~ **`SuggestionView`** (corrected, see note) | `SuggestionTypeSelect` + `SuggestionCategorySelect` + `SuggestionModal` chain | **Corrected in `commands/suggest.md` §5:** the actual pattern is a single view combining N selects (Type + Category) with one modal-trigger button, gated on all selections being made — **not** a sequential "one step visible at a time" wizard as originally guessed here. Renamed accordingly; the original "guided multi-step" framing didn't match this command's real (and only) consumer | `bot/modules/UI/views/suggestion_view.py` (command-specific — no second consumer needing a real multi-step wizard exists yet, so this stays out of the shared catalog proper; kept as a row here only to record the correction) | `commands/suggest.md` (drafted) |
| `LobbyView` | `JoinButton`/`LeaveButton`/`StartButton`/`AbortButton`/`CreatorAbortButton` + countdown | Live 1–10 participant list + countdown + role-gated Start/Abort for the current lobby owner (invoker initially; earliest available joined participant after transfer) | `bot/modules/UI/views/lobby.py` | `commands/quick-battle.md` (drafted) |
| `SequentialCollector` | `FighterCreator`/`StrategyCreator`/`EnvironmentCreator` | Send the same button+modal prompt to N participants in turn (or in parallel with a shared deadline), aggregate results | `bot/modules/UI/views/sequential_collector.py` | `commands/quick-battle.md` (drafted) |
| `StagedSettingsView` | `/config`'s `_pending_changes` dict + `LanguageSelect`/`AIConfigModal`/`WebhookConfigModal` + `Apply` button | Multiple settings staged in-memory, single explicit Apply/commit step | `bot/modules/UI/views/staged_settings.py` | `commands/config.md` (drafted — also introduces the new command-specific `ModelSelect`, live-populated from Google's model-listing API rather than free text, see `config.md` §6) |
| `ConfirmationView` | *(new — no direct legacy equivalent)* | Generic yes/no gate before a destructive/wide-blast-radius action | `bot/modules/UI/views/confirmation.py` | Any command that needs one; direct `Bot`-side analog of `web/components.md` §6's unused Confirmation Modal |
| `TaskProgressContainer` | *(new — legacy had no live status bar)* | Renders `contracts/task_progress.md` §4's phase vocabulary as a live-editing status container, using §2's Pending/Success/Error colors — **concrete design confirmed, see §3.1** | `bot/modules/UI/views/task_progress_container.py` (confirmed as a V2 `Container`, not a classic `Embed`, per §1 — naming settled in this revision) | `commands/quick-battle.md`, any future AI-backed command |

### 3.1 `TaskProgressContainer` — Concrete Design (confirmed, project owner)

The underlying plumbing (the local `task_id` map, the `progress/ai_worker/#` Mosquitto subscription, elapsed-time bookkeeping) is Bot-container-wide infrastructure and is documented in `bot/discord_bot.md` §6.3, not here — this section is purely the **rendering** side: what actually gets drawn in Discord.

**Confirmed decision:** exactly **5 fixed lines**, one per `contracts/task_progress.md` §4 phase — `Queued`, `Launching`, `Composing`, `Refining`, `Finishing` — in that fixed order, never more, never fewer. This deliberately does **not** expand into graph-specific sub-steps (e.g. individual `refiner` retry attempts) as separate lines — `task_progress.md` §6 already collapses those on purpose, specifically to cap Discord message edits at roughly 4–5 per task; a richer per-attempt checklist would silently reintroduce the edit-spam problem that design already avoids.

**Status icon per line**, driven by `TaskRecord.phase_history` vs. `TaskRecord.current_phase` (`bot/discord_bot.md` §6.3):

| Icon | Meaning | Condition |
|---|---|---|
| ✅ | Done | This phase (or a later one) already appears in `phase_history` |
| 🔄 | In progress | This phase == `current_phase`, and no later phase has been reached yet |
| ⬜ | Not yet reached | Every phase after `current_phase` |

**Mockup** (example: a `/quick-battle` task currently in `refining`):

```
Generating environment...
✅ Queued
✅ Launching
✅ Composing
🔄 Refining
⬜ Finishing
Progress: 3/5
Duration: 0m 42s
```

- **Title line** (`Generating environment...` above) is command-supplied context, not part of this component's own contract — `/quick-battle` passes something like `"Generating environment..."` or `"Generating battle..."` depending on which graph the task is for (`graphs/environment.md` vs. `graphs/battle.md`), giving the player more concrete framing than a generic "AI Task" label would.
- **`Progress: X/5`** — `X` is the count of ✅ lines only (current in-progress and not-yet-reached lines never count toward the numerator), so `X` ranges from `0` (still `queued`, nothing completed yet) to `4` (currently `finishing`, everything before it done) — it never reaches `5/5` while this container is still being shown, since the container is replaced by the actual result the moment `ai_tasks_results` arrives (`discord_bot.md` §6.3's cleanup step).
- **`Duration`** — elapsed wall-clock time since `TaskRecord.created_at` (`discord_bot.md` §6.3), formatted as `Xm Ys`. Refresh every **15 seconds**. Heartbeat ticks never render; phase changes are immediate only when the previous edit is ≥5 seconds old, otherwise coalesced. All task containers share Bot's one-edit-per-second scheduler, with terminal replacements prioritized.
- **Color:** uses §2's Pending/Info Blurple for the whole container while any phase is in progress; the container itself is replaced (not recolored to Success/Error) once the terminal `ai_tasks_results` message arrives, since at that point the actual result message takes over — this component never itself renders a terminal state.

## 4. Open Items

- ~~Design system (§2) is a proposal, not confirmed~~ — **resolved:** confirmed to use discord.py's default `Color` palette (§2). Footer format and emoji-prefix convention are still unconfirmed — no decision has been requested or made on those specifically, distinct from the color question.
- ~~**Component catalog (§3) is a candidate inventory, not an implementation plan** — names, exact APIs, and even whether some of these should merge (e.g. is `SequentialCollector` just a specialization of `WizardView`?) are undecided. This should be resolved concretely while drafting `commands/quick-battle.md` and `commands/suggest.md`, not guessed at here in the abstract.~~ — **partially resolved:** all three command docs (`quick-battle.md`, `config.md`, `suggest.md`) are now drafted. The "is `SequentialCollector` a specialization of `WizardView`?" question resolved itself the other way — `WizardView` turned out not to describe any real consumer's actual behavior (corrected to `SuggestionView` in §3's table) — so there's no merge question left, just a rename. Exact APIs/implementation for every row are still undecided; that's unchanged.
- ~~`/config` and `/ping` exist in the legacy bot but have no entry anywhere in `Readme.md`'s tree~~ — **resolved differently for each:** `/ping` is **confirmed dropped** (project owner: "not supported at new version") — not carried forward into this architecture, `StagedSettingsView` has no bearing on it. `/config` is now fully drafted (`commands/config.md`) — `StagedSettingsView`'s candidate consumer now points at a real, drafted file, and that doc also introduces the new command-specific `ModelSelect` component (§3 there).
- **`SuggestionView`** (§3) describes the real `/suggest` pattern (simultaneous selects + modal trigger), not a sequential wizard.
- **`/suggest` catalog** is owned by `contracts/status_document.md` (`StatusService.get_suggestion_catalog()`); there is **no** hardcoded Bot fallback list (`commands/suggest.md` §6).
- ~~`TaskProgressContainer`'s concrete design (icons, layout, exact fields) was undecided~~ — **resolved, see §3.1**: 5 fixed phase lines, ✅/🔄/⬜ status icons, `Progress: X/5`, `Duration` since task creation. The underlying plumbing (task map, Mosquitto subscription) is documented separately in `bot/discord_bot.md` §6.3, per that doc's own split.
- Whether `TaskProgressContainer` is generic enough to also serve `environment`'s standalone generation (if ever exposed as its own user-facing flow, vs. only as a sub-step of `battle`) is undecided — noted for whoever designs it concretely. Its 5-line design (§3.1) is graph-agnostic by construction (the phase vocabulary itself is graph-agnostic, per `task_progress.md` §4), so this is more of an invocation-flow question than a rendering one.
- No decision yet on how deeply `bot/modules/UI/` base classes should mirror CascadeUI's `build_ui()` auto-rebuild-on-state-change pattern (§1) vs. each view managing its own manual `edit_message`/`refresh` calls — an implementation question intentionally deferred past this documentation pass.
