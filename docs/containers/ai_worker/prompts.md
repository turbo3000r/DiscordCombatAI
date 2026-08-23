# AI Worker: Prompt System

> **Why this file exists:** `prompts/` (mounted into `AI Worker` at runtime, per `architecture.md`'s file structure) is consumed by nodes across **both** `graphs/environment.md` and `graphs/battle.md`, and by the shared `Validator`/`Decider` nodes in `nodes.md`. Before this doc existed, exact prompt filenames were independently re-cited in `architecture.md`, `environment.md`, and `battle.md` — three places that would all need to change in lockstep every time a prompt file moved or was renamed. This doc is the single source of truth; the other three now link here instead of restating filenames.

---

## 1. Status: Target Layout Authored

**The legacy prompt system was built for a simpler, pre-LangGraph architecture** — one monolithic system prompt per graph (`environment_combiner.txt` for the whole `environment` graph, `core_simple_battle.txt` for the whole `battle` graph in a single shot). That no longer matches reality: `environment` now has 5 nodes and `battle` has 9, most of which need their own distinct instructions.

**The target graph/node prompt layout is now authored on disk and is mounted as a runtime
input.** Legacy files remain reference material where noted in §6; the graph code loads only
the target paths from §3. This document fixes the load-bearing structure and injection
wrappers; prompt prose implements the already-canonical graph schemas and rubrics.

**Scope note, confirmed this revision (project owner):** the ~14 per-node system prompt files (`graphs/environment/*.txt`, `graphs/battle/*.txt`, `nodes/*_base.txt`) are expected to be almost entirely rewritten during implementation to actually support LangGraph's per-node structure — legacy content is a tone/logic *reference* at most (§6 already says this for `core_simple_battle.txt` specifically), never a dependency to preserve. This doc does not need to specify their content any further than it already does (§4's mapping table) — that's implementation work, not a documentation gap. **What genuinely is load-bearing and must stay nailed down here:** the `elements/` injection wrappers (`setting/*.txt`, `language.txt`, `environment.txt`, `fighters.txt`, §2/§3) — every new per-node system prompt is written *against* these blocks' exact labelled format (`## SETTING:`, `## LANGUAGE-LOCALE:`, etc.), so whoever authors new prompt content is depending on this doc's §2/§3 being accurate, even though the system prompts themselves are free to be rewritten from scratch.

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
│       └── resolve_winners.txt         # NEW — emergent-mode only; exact player_id selection instructions
│
└── elements/                           # reusable injection wrappers — same role as today
    ├── setting/                        # moved from top-level prompts/setting/ — same 7 files/names, now grouped with other injectable-value fragments (confirmed decision)
    │   ├── realistic.txt
    │   ├── realistic-urban.txt
    │   ├── realistic-nature.txt
    │   ├── dreamcore.txt
    │   ├── unpredictable-realistic.txt
    │   ├── unpredictable-dreamcore.txt
    │   └── unpredictable-funny.txt
    ├── language.txt                    # unchanged
    ├── environment.txt                 # renamed from custom_environment.txt — "custom" was a legacy-arch qualifier with no remaining distinct meaning
    └── fighters.txt                    # unchanged
```

Generic no-AI arenas are not prompts and are not mounted into AI Worker. Their target is Bot-owned packaged data under `src/bot/resources/generic_environments/*.txt`; ownership and `.txt` → `Environment` conversion are canonical in `graphs/environment.md` §1 and `bot/commands/quick-battle.md`.

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
| `ResolveWinners` (emergent mode only) | `graphs/battle/resolve_winners.txt` | `elements/fighters.txt` + fighter list; output must contain exact supplied `player_id` values and is never derived by nickname matching (`graphs/battle.md` §5/§6) |

---

## 5. Shared `Validator`/`Decider` Prompt Split

`Validator` and `Decider` are literally the same Python module across both graphs (`ai_worker/nodes/validation.py`, `ai_worker/nodes/decider.py` — see `nodes.md` §2, §3). Their prompts follow the same shape: **one shared "judge persona" / "picker persona," with each graph injecting only its own rubric** — not two independently-authored full prompts that happen to do the same job and can silently drift in tone/structure over time.

```
Validator's final prompt = nodes/validator_base.txt + graphs/<name>/validator_criteria.txt + <candidate presented via elements/*>
Decider's final prompt   = nodes/decider_base.txt   + graphs/<name>/decider_criteria.txt   + <all attempts presented via elements/*>
```

The graph-specific criteria files are authored at the paths in §3. Their normative behavioral
rubrics remain fixed in `nodes.md` §2a/§3; prompt prose implements those rubrics without
changing them.

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
| `prompts/core/generic_environments/*.txt` (2 files) | `src/bot/resources/generic_environments/*.txt` | Move into the Bot image; these are static product data, not AI prompts |
| *(none — new)* | `prompts/nodes/validator_base.txt`, `prompts/nodes/decider_base.txt` | New content to author |
| *(none — new)* | Every other file in §3's target tree not listed above | New content to author |

---

## 7. Open Items

- Legacy prompt files may remain as non-runtime references during compatibility cleanup, but
  graph nodes must continue to load only the target paths in §3.
- Prompt tuning may improve prose quality, but it must not alter the canonical schemas, bounds,
  identity rules, or semantic Validator/Decider rubrics.
