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
    validator_verdict: ValidatorVerdict
```

`candidate`'s actual type is graph-specific (`Environment` for `environment`, `str` for `battle`) — each consuming graph's own doc states it concretely in its own state schema.

---

## 2. `Validator` Node

| | |
|---|---|
| **Location** | `ai_worker/nodes/validation.py` |
| **Responsibility** | Judges a candidate against graph-supplied criteria; returns a `ValidatorVerdict`. If invalid, formulates `fix_request` (a `ModificationRequest` with `origin: "validator"`). |
| **Parameters each graph supplies** | Its own validation criteria/prompt, and however it renders its candidate as text for the LLM (e.g. `environment`'s `environment.txt` element wrapper — see `ai_worker/prompts.md` §4.1). |
| **Prompt** | `prompts/nodes/validator_base.txt` (shared) + `prompts/graphs/<name>/validator_criteria.txt` (per-graph) — see `ai_worker/prompts.md` §5 for the split rationale. Neither criteria file is authored yet for either graph. |
| **Consumers** | `environment` (`graphs/environment.md`), `battle` (`graphs/battle.md`) |

---

## 3. `Decider` Node

| | |
|---|---|
| **Location** | `ai_worker/nodes/decider.py` |
| **Responsibility** | Reached only when a `refiner` loop's retry budget is exhausted with no valid candidate. Picks the best of all recorded `AttemptRecord`s (including attempt #0) and sets `forced_selection = true` on the parent graph's output. |
| **Parameters each graph supplies** | A short criteria/prompt fragment describing what "better" means when nothing is fully valid (e.g. `environment`: closeness to setting standards; `battle`: narrative coherence + outcome-shape correctness). |
| **Prompt** | `prompts/nodes/decider_base.txt` (shared) + `prompts/graphs/<name>/decider_criteria.txt` (per-graph) — see `ai_worker/prompts.md` §5. Neither criteria file is authored yet for either graph. |
| **Consumers** | `environment`, `battle` |

> **Status note:** promoted from graph-specific to shared in this revision, once `battle` confirmed it needs the identical fallback behavior `environment` already had. No graph-specific criteria prompt has been authored for either consumer yet — see each graph's own Open Items and `ai_worker/prompts.md` §7.

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

- ~~No criteria/prompt has been authored yet for either `Validator` or `Decider`~~ — **target file structure now defined**, see `ai_worker/prompts.md` §5 (the `nodes/*_base.txt` + `graphs/<name>/*_criteria.txt` split). Content authoring for all 4 criteria files (2 graphs × `validator`/`decider`) is still fully open.
- Whether `Decider`'s underlying mechanism is a single LLM call over all candidates at once, or a smaller pairwise/tournament comparison, is undecided — flagged for whoever implements `ai_worker/nodes/decider.py`.
- ~~This file will need a real `ai_worker.md`~~ — **resolved**, `ai_worker.md` now exists. `AI_WORKER_LLM_MAX_RETRIES` (§5) is canonically defined there, §3.
