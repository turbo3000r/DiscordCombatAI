# AI Worker: Shared Nodes

> **Why this file exists:** `src/ai_worker/nodes/` (per `architecture.md`'s file structure) holds LangGraph nodes reused across multiple graphs — as of this revision, by both `environment` and `battle`. Per the same convention already established for `azure.md` (env vars) and `contracts/task_progress.md` (phase enum): shared contracts get one definition, and consuming graph docs reference it instead of redefining it. This file is that source of truth for `ai_worker/nodes/`.

---

## 1. Shared Types — the "refiner" contract

Every graph that uses the shared `Validator`/`Decider` pair (§2, §3) shares this vocabulary:

```python
class ModificationRequest(TypedDict):
    instruction: str
    origin: Literal["player", "validator"]   # "player" only meaningful for graphs with a revision-style input
    reason: str | None                        # populated only when origin == "validator"

class ValidatorVerdict(TypedDict):
    is_valid: bool
    issues: list[str]
    fix_request: ModificationRequest | None   # set only if is_valid is False; origin == "validator"

class AttemptRecord(TypedDict):
    attempt_index: int              # 0 = the first candidate produced before any fix
    candidate: Any                  # graph-specific payload — an Environment, a full story string, etc.
    source: Literal["initial", "fixer"]
    validator_verdict: ValidatorVerdict | None
```

`candidate`'s actual type is graph-specific (`Environment` for `environment`, `str` for `battle`) — each consuming graph's own doc states it concretely in its own state schema.

An attempt is appended with `validator_verdict = None`. The immediately following `Validator` call fills that field exactly once. `Decider` may consume only a non-empty pool in which every record has a verdict. `attempts_used` on a real graph result is always `len(attempts)` — attempt #0 is included, so the value is `1..4` with the confirmed three-fixer-retry limit. The Phase 2 transport shell is the sole `0` exception because it creates no candidate record (`contracts/ai_task.md` §11).

### 1a. Structured-output validation and coercion

Every LLM-backed node uses a node-specific structured schema declared by its graph doc. The shared parser:

1. requires one JSON object and rejects unknown fields, wrong types, invalid enum values, duplicate IDs, non-finite numbers, and graph-specific bound violations;
2. may normalize strings to Unicode NFC and trim surrounding whitespace;
3. never invents a missing value, clamps a number, changes an enum, fuzzy-matches a name, converts a nickname into an ID, or partially accepts a malformed collection;
4. treats a structurally valid but behaviorally poor candidate as Validator work, not a parse failure;
5. treats malformed structured output as one failed API attempt under `AI_WORKER_LLM_MAX_RETRIES`.

---

## 2. `Validator` Node

| | |
|---|---|
| **Location** | `ai_worker/nodes/validation.py` |
| **Responsibility** | Judges a candidate against graph-supplied criteria; returns a `ValidatorVerdict`. If invalid, formulates `fix_request` (a `ModificationRequest` with `origin: "validator"`). |
| **Parameters each graph supplies** | Its own validation criteria/prompt, and however it renders its candidate as text for the LLM (e.g. `environment`'s `environment.txt` element wrapper — see `ai_worker/prompts.md` §4.1). |
| **Prompt** | `prompts/nodes/validator_base.txt` (shared) + `prompts/graphs/<name>/validator_criteria.txt` (per-graph) — see `ai_worker/prompts.md` §5 for the split rationale. |
| **Consumers** | `environment` (`graphs/environment.md`), `battle` (`graphs/battle.md`) |

### 2a. Normative graph rubrics

The prompt prose is authored during Phase 4; these acceptance criteria are already canonical:

- **Environment:** schema/bounds; coverage of supplied descriptions or active modification request; selected-setting consistency; requested `language_locale`; internal coherence; no prompt/meta commentary or credential leakage.
- **Battle:** schema/bounds; every narrated fighter maps to supplied fighter data; outcome cardinality; exact scripted-winner consistency when scripted mode is used; no undeclared victor; environment/setting continuity; coherent episode order and resolved ending; requested `language_locale`; no prompt/meta commentary or credential leakage.

`is_valid=true` requires no hard-gate issue. On failure, `issues` contains 1–8 concise items (each ≤240 characters), and `fix_request` is non-null with `origin="validator"`. On success, `issues=[]` and `fix_request=null`.

---

## 3. `Decider` Node

| | |
|---|---|
| **Location** | `ai_worker/nodes/decider.py` |
| **Responsibility** | Reached only when a `refiner` loop's retry budget is exhausted with no valid candidate. Picks the best of all recorded `AttemptRecord`s (including attempt #0) and sets `forced_selection = true` on the parent graph's output. |
| **Parameters each graph supplies** | A short criteria/prompt fragment describing what "better" means when nothing is fully valid (e.g. `environment`: closeness to setting standards; `battle`: narrative coherence + outcome-shape correctness). |
| **Prompt** | `prompts/nodes/decider_base.txt` (shared) + `prompts/graphs/<name>/decider_criteria.txt` (per-graph) — see `ai_worker/prompts.md` §5. |
| **Consumers** | `environment`, `battle` |

The v1 pool is strictly bounded to at most four records: attempt #0 plus three fixer retries. `Decider` makes one structured-output call over that pool and returns:

```python
class DeciderSelection(TypedDict):
    selected_attempt_index: int   # must equal one attempt_index present in the supplied pool
    reason: str                   # 1..500 chars; diagnostic, never shown as generated content
```

It selects by the following lexicographic priorities:

- **Environment:** schema/safety, requested-change coverage, setting fit, coherence, language quality, concision.
- **Battle:** outcome and winner-identity correctness, fighter fidelity, narrative coherence/completeness, environment/setting fidelity, language/prose quality.

An unknown index or malformed selection uses the shared retry budget; exhaustion fails the task at `Decider`. There is no tournament/pairwise mode in v1 because the candidate pool cannot exceed four.

Graph-specific hard postconditions still apply to the selected candidate. In `battle`, winner cardinality/identity and scripted-winner consistency are non-negotiable: if no candidate satisfies them, Decider fails rather than force-selecting contradictory prose (`graphs/battle.md` §7).

> **Status note:** promoted from graph-specific to shared once `battle` confirmed it needs the identical fallback behavior `environment` already had. Prompt wording remains Phase 4 implementation work; the schemas and behavioral rubrics above are normative.

---

## 4. The `refiner` Subgraph Pattern

Not a single literal graph class — a documented **control-flow pattern**, parameterized per consuming graph:

```
<fixer_node> ↔ Validator, bounded by <GRAPH>_MAX_<FIXER>_RETRIES
    valid                         → done
    retries exhausted, still invalid → Decider → done
```

- **Entry point:** always `Validator`.
- **Retry budget is graph-specific, not shared** (deliberately): `ENVIRONMENT_MAX_ENHANCER_RETRIES` vs `BATTLE_MAX_MODIFIER_RETRIES` are separate env vars — different graphs may warrant different cost/latency tradeoffs, unlike the LLM-call-level retry wrapper (§5), which genuinely is graph-agnostic.
- **`Validator` and `Decider` are the only literally-shared code.** The "fixer" node (`Enhancer` in `environment`, `Modifier` in `battle`) is always graph-specific — applying a fix is inherently domain content, not something to genericize.

### Consumers

| Graph | Fixer node (graph-specific) | Candidate type | Doc |
|---|---|---|---|
| `environment` | `Enhancer` | `Environment` | `graphs/environment.md` |
| `battle` | `Modifier` | Full story `str` | `graphs/battle.md` |

---

## 5. Shared LLM Call Retry Wrapper

| | |
|---|---|
| **Applies to** | Every LLM-backed node in every graph, not specific to `Validator`/`Decider` |
| **Config** | `AI_WORKER_LLM_MAX_RETRIES` (default `2`) — canonically defined in `ai_worker.md` §3, restated here since it's graph-agnostic and belongs conceptually next to the other shared pieces on this page |
| **Behavior** | Retries transient Gemini API failures and malformed/unparseable structured output with one unified budget; if exhausted, the whole task fails (`AiTaskResultFailed` via `ai_tasks_results`, `contracts/ai_task.md` §4 — `node` set to whichever node's call was retried). |

---

## 6. Open Items

- Validator/Decider prompt **wording** remains Phase 4 implementation work in the target files from `ai_worker/prompts.md` §5. Their schemas, rubrics, pool bound, and single-call Decider mechanism are resolved above.
- ~~This file will need a real `ai_worker.md`~~ — **resolved**, `ai_worker.md` now exists. `AI_WORKER_LLM_MAX_RETRIES` (§5) is canonically defined there, §3.
