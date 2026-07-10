# AI Worker: Prompt System

> **Why this file exists:** `prompts/` (mounted into `AI Worker` at runtime, per `architecture.md`'s file structure) is consumed by nodes across **both** `graphs/environment.md` and `graphs/battle.md`, and by the shared `Validator`/`Decider` nodes in `nodes.md`. Before this doc existed, exact prompt filenames were independently re-cited in `architecture.md`, `environment.md`, and `battle.md` — three places that would all need to change in lockstep every time a prompt file moved or was renamed. This doc is the single source of truth; the other three now link here instead of restating filenames.

---

## 1. Status: Rewrite In Progress

**The legacy prompt system was built for a simpler, pre-LangGraph architecture** — one monolithic system prompt per graph (`environment_combiner.txt` for the whole `environment` graph, `core_simple_battle.txt` for the whole `battle` graph in a single shot). That no longer matches reality: `environment` now has 5 nodes and `battle` has 9, most of which need their own distinct instructions.

**This section documents the TARGET structure (design confirmed, project owner) — not yet migrated on disk.** As of this revision, `prompts/` on disk still reflects the legacy layout (§6 has the full legacy → target mapping). This doc fixes the **file structure and naming only** — actually authoring the ~14 new prompt files' content is a separate, larger, and still-open task (§7).

---

## 2. Injection Pattern (how a node's final prompt is assembled)

Confirmed from reading the existing legacy files (`environment_combiner.txt`, `core_simple_battle.txt`, `elements/*.txt`, `setting/realistic.txt`): a node's final prompt sent to Gemini is **not** single-file string substitution. It's a **system prompt file concatenated with one or more labelled context blocks**, each contributed by a separate file:

```
final_prompt =
    <node's own system prompt file>            # role, mission, process, output constraints
    + "\n\n" + <elements/setting/{setting}.txt>   # "## SETTING:\n..." — already a complete labelled block
    + "\n\n" + <elements/language.txt, {locale} filled in>   # "## LANGUAGE-LOCALE:\n{locale}"
    + "\n\n" + <elements/environment.txt, {env} filled in>   # "## Environment:\n{env}" (whichever node needs to show the working environment)
    + "\n\n" + <elements/fighters.txt> + <programmatically formatted fighter list>   # "## FIGHTERS:\n..." (battle graph only)
```

A node's own system prompt file references the variables it needs by name (e.g. `{SETTING}`, `{LANGUAGE-LOCALE}`) in prose, instructing the model how to use them — the actual values arrive as the separate appended blocks above, not as literal string-replacement inside the system prompt file itself.

---

## 3. Target Directory Structure

```
prompts/
├── nodes/                              # prompt fragments for the SHARED code nodes — mirrors src/ai_worker/nodes/
│   ├── validator_base.txt              # generic "judge this candidate against the supplied criteria" instructions
│   └── decider_base.txt                # generic "pick the best of N imperfect attempts" instructions
│
├── graphs/                             # mirrors src/ai_worker/graphs/
│   ├── environment/
│   │   ├── generator.txt               # = legacy core/environment_combiner.txt, moved (content largely reusable as-is)
│   │   ├── normalise.txt               # NEW
│   │   ├── enhancer.txt                # NEW
│   │   ├── validator_criteria.txt      # NEW — appended after nodes/validator_base.txt (§5)
│   │   └── decider_criteria.txt        # NEW — appended after nodes/decider_base.txt (§5)
│   └── battle/
│       ├── predefine.txt               # NEW
│       ├── create_skeleton.txt         # NEW
│       ├── implement_first_episode.txt # NEW — core_simple_battle.txt usable as a content/tone reference only, not a straight rename (single-shot vs. one episode)
│       ├── implement_next_episode.txt  # NEW
│       ├── implement_last_episode.txt  # NEW — same reference caveat as first_episode
│       ├── modifier.txt                # NEW
│       ├── validator_criteria.txt      # NEW
│       ├── decider_criteria.txt        # NEW
│       └── resolve_winners.txt         # NEW — emergent-mode only; nickname → player_id mapping instructions
│
├── elements/                           # reusable injection wrappers — same role as today
│   ├── setting/                        # moved from top-level prompts/setting/ — same 7 files/names, now grouped with other injectable-value fragments (confirmed decision)
│   │   ├── realistic.txt
│   │   ├── realistic-urban.txt
│   │   ├── realistic-nature.txt
│   │   ├── dreamcore.txt
│   │   ├── unpredictable-realistic.txt
│   │   ├── unpredictable-dreamcore.txt
│   │   └── unpredictable-funny.txt
│   ├── language.txt                    # unchanged
│   ├── environment.txt                 # renamed from custom_environment.txt — "custom" was a legacy-arch qualifier with no remaining distinct meaning
│   └── fighters.txt                    # unchanged
│
└── static/                             # renamed from core/generic_environments/ — no-AI, pre-written fallback content (graphs/environment.md §1's out-of-scope third mode)
    └── generic_environments/
        ├── generic_environment0.txt
        └── generic_environment1.txt
```

---

## 4. Node → Prompt File Mapping

### 4.1 `environment` graph (`graphs/environment.md`)

| Node | System prompt | Element blocks appended |
|---|---|---|
| `RouteInput` | — (no LLM call) | — |
| `Generator` | `graphs/environment/generator.txt` | `elements/setting/<setting>.txt`, `elements/language.txt` |
| `Normalise` | `graphs/environment/normalise.txt` | `elements/language.txt`, `elements/environment.txt` (the environment being commented on) |
| `Enhancer` | `graphs/environment/enhancer.txt` | `elements/setting/<setting>.txt`, `elements/language.txt`, `elements/environment.txt` (current working environment) |
| `Validator` (shared) | `nodes/validator_base.txt` + `graphs/environment/validator_criteria.txt` | `elements/setting/<setting>.txt`, `elements/language.txt`, `elements/environment.txt` |
| `Decider` (shared) | `nodes/decider_base.txt` + `graphs/environment/decider_criteria.txt` | `elements/environment.txt` (once per attempt, to present the candidate pool) |

### 4.2 `battle` graph (`graphs/battle.md`)

| Node | System prompt | Element blocks appended |
|---|---|---|
| `Predefine` | `graphs/battle/predefine.txt` | `elements/fighters.txt` + fighter list, `elements/environment.txt`, `elements/setting/<setting>.txt` |
| `CreateSkeleton` | `graphs/battle/create_skeleton.txt` | same as `Predefine` |
| `ImplementFirstEpisode` | `graphs/battle/implement_first_episode.txt` | `elements/fighters.txt`, `elements/environment.txt`, `elements/setting/<setting>.txt`, `elements/language.txt` |
| `ImplementNextEpisode` | `graphs/battle/implement_next_episode.txt` | same as `ImplementFirstEpisode`, plus prior episode text |
| `ImplementLastEpisode` | `graphs/battle/implement_last_episode.txt` | same as `ImplementFirstEpisode`, plus all prior episode text |
| `Validator` (shared) | `nodes/validator_base.txt` + `graphs/battle/validator_criteria.txt` | `elements/fighters.txt`, `elements/environment.txt`, `elements/setting/<setting>.txt` |
| `Modifier` | `graphs/battle/modifier.txt` | `elements/language.txt`, `elements/setting/<setting>.txt` |
| `Decider` (shared) | `nodes/decider_base.txt` + `graphs/battle/decider_criteria.txt` | `elements/environment.txt` (once per attempt) |
| `ResolveWinners` (emergent mode only) | `graphs/battle/resolve_winners.txt` | `elements/fighters.txt` + fighter list (needs both `player_nick` and `player_id` present, per `graphs/battle.md` §5's `Fighter` design note) |

---

## 5. Shared `Validator`/`Decider` Prompt Split

`Validator` and `Decider` are literally the same Python module across both graphs (`ai_worker/nodes/validation.py`, `ai_worker/nodes/decider.py` — see `nodes.md` §2, §3). Their prompts follow the same shape: **one shared "judge persona" / "picker persona," with each graph injecting only its own rubric** — not two independently-authored full prompts that happen to do the same job and can silently drift in tone/structure over time.

```
Validator's final prompt = nodes/validator_base.txt + graphs/<name>/validator_criteria.txt + <candidate presented via elements/*>
Decider's final prompt   = nodes/decider_base.txt   + graphs/<name>/decider_criteria.txt   + <all attempts presented via elements/*>
```

Neither `validator_criteria.txt` nor `decider_criteria.txt` exists yet for either graph — this is the actual content gap, tracked in both graphs' own §12 and in `nodes.md` §6.

---

## 6. Legacy → Target File Mapping

| Legacy path (current, on disk) | Target path | Migration note |
|---|---|---|
| `prompts/core/environment_combiner.txt` | `prompts/graphs/environment/generator.txt` | Content is largely reusable as-is — only the file's location changes |
| `prompts/core/core_simple_battle.txt` | *(reference only, no direct target)* | Splits conceptually across `implement_first_episode.txt` / `implement_last_episode.txt` — content needs rewriting for the episode model, not a straight move |
| `prompts/setting/*.txt` (7 files) | `prompts/elements/setting/*.txt` | Directory move only, filenames unchanged |
| `prompts/elements/language.txt` | `prompts/elements/language.txt` | Unchanged |
| `prompts/elements/custom_environment.txt` | `prompts/elements/environment.txt` | Rename only, content unchanged |
| `prompts/elements/fighters.txt` | `prompts/elements/fighters.txt` | Unchanged |
| `prompts/core/generic_environments/*.txt` (2 files) | `prompts/static/generic_environments/*.txt` | Directory move only |
| *(none — new)* | `prompts/nodes/validator_base.txt`, `prompts/nodes/decider_base.txt` | New content to author |
| *(none — new)* | Every other file in §3's target tree not listed above | New content to author |

---

## 7. Open Items

- **Actually authoring the ~14 new/adapted prompt files' content is unstarted** — this doc only fixes their target location and name, per §1. This is the single biggest concrete gap for implementing either graph beyond `Generator`.
- **The physical migration on disk (moving/renaming the legacy files per §6) hasn't happened yet** — this doc describes the target state; someone still needs to actually perform the moves and update whatever code path constructs each node's final prompt (§2).
- `validator_criteria.txt` / `decider_criteria.txt` don't exist for either graph yet (§5) — same gap already flagged in `nodes.md` §6.
- Whether `implement_first_episode.txt` and `implement_last_episode.txt` end up sharing significant text (both being "large, detailed" episodes per `graphs/battle.md` §6) or are fully independent prompts is undecided — a judgment call for whoever authors them.
