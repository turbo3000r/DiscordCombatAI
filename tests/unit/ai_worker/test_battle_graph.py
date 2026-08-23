from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from ai_worker.graphs.battle.graph import (
    BattleGraph,
    BattleGraphRuntime,
    EpisodeOutput,
    EpisodeSkeleton,
    PredefineOutput,
    SkeletonOutput,
    StoryOutput,
    WinnerResolution,
    battle_invocation_from_envelope,
)
from ai_worker.graphs.foundation import NodeExecutionError
from ai_worker.llm import GeminiResponse, StructuredOutputError, parse_structured_output
from shared.models import BattleAiTaskEnvelope, TaskPhase

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "contracts"
PROMPTS = Path(__file__).resolve().parents[3] / "prompts"


class FakeGemini:
    def __init__(self, replies: Iterable[object]) -> None:
        self._replies = iter(replies)
        self.calls: list[dict[str, object]] = []

    def generate_structured(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        response_schema: type[BaseModel],
        max_output_tokens: int,
    ) -> GeminiResponse | str:
        self.calls.append(
            {
                "api_key": api_key,
                "model": model,
                "prompt": prompt,
                "max_output_tokens": max_output_tokens,
            }
        )
        reply = next(self._replies)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, (GeminiResponse, str))
        return reply


def _envelope(**overrides: Any) -> BattleAiTaskEnvelope:
    payload = json.loads((FIXTURES / "ai_task_battle.json").read_text(encoding="utf-8"))
    payload.update(overrides)
    return BattleAiTaskEnvelope.model_validate(payload)


def _valid() -> str:
    return json.dumps({"is_valid": True, "issues": [], "fix_request": None})


def _invalid() -> str:
    return json.dumps(
        {
            "is_valid": False,
            "issues": ["Improve continuity."],
            "fix_request": {
                "instruction": "Improve continuity.",
                "origin": "validator",
                "reason": "continuity",
            },
        }
    )


def _episode(index: int, text: str | None = None) -> str:
    return json.dumps({"episode_index": index, "text": text or f"Episode {index}: Alice fights."})


def _replies(episode_count: int, *, outcome: str = "one") -> list[str]:
    return [
        json.dumps(
            {
                "outcome_type": outcome,
                "episode_count": episode_count,
                "predetermined_winners": None,
            }
        ),
        json.dumps(
            {
                "episodes": [
                    {"episode_index": index, "summary": f"Beat {index}"}
                    for index in range(episode_count)
                ]
            }
        ),
        *[_episode(index) for index in range(episode_count)],
        _valid(),
        json.dumps({"winner_ids": ["111111111111111111"]}),
    ]


@pytest.mark.parametrize("episode_count", [2, 3, 4, 5])
def test_episode_boundaries_and_call_limits(episode_count: int) -> None:
    fake = FakeGemini(_replies(episode_count))
    phases: list[TaskPhase] = []
    graph = BattleGraph(
        BattleGraphRuntime(
            prompt_root=PROMPTS,
            llm_max_retries=0,
            client=fake,
            publish_phase=phases.append,
            sleep=lambda _: None,
        )
    )

    result = graph.invoke(battle_invocation_from_envelope(_envelope()))

    assert result["attempts_used"] == 1
    assert result["winners"] == ["111111111111111111"]
    assert phases == [TaskPhase.composing, TaskPhase.refining, TaskPhase.finishing]
    assert [call["max_output_tokens"] for call in fake.calls] == (
        [1024, 4096] + [4096] * episode_count + [2048, 1024]
    )
    assert "## FIGHTERS:" in str(fake.calls[0]["prompt"])
    assert "111111111111111111:" in str(fake.calls[0]["prompt"])
    assert "## LANGUAGE-LOCALE:" not in str(fake.calls[0]["prompt"])
    assert "## LANGUAGE-LOCALE:\nen" in str(fake.calls[2]["prompt"])
    validator_prompt = str(fake.calls[2 + episode_count]["prompt"])
    assert "## FIGHTERS:" in validator_prompt
    assert "## Environment:" in validator_prompt
    assert "## SETTING:" in validator_prompt
    assert "## OUTCOME:" in validator_prompt
    assert '"predetermined_winners":null' in validator_prompt
    assert '"random_winner_mode":false' in validator_prompt
    assert "111111111111111111:" in validator_prompt


def test_malformed_predefine_and_skeleton_mismatch_retry_then_fail() -> None:
    predefine = FakeGemini(["not-json"])
    graph = BattleGraph(
        BattleGraphRuntime(PROMPTS, llm_max_retries=0, client=predefine, sleep=lambda _: None)
    )
    with pytest.raises(NodeExecutionError, match="Predefine"):
        graph.invoke(battle_invocation_from_envelope(_envelope()))

    mismatch = FakeGemini(
        [
            json.dumps(
                {
                    "outcome_type": "one",
                    "episode_count": 2,
                    "predetermined_winners": None,
                }
            ),
            json.dumps({"episodes": [{"episode_index": 1, "summary": "wrong"}]}),
        ]
    )
    graph = BattleGraph(
        BattleGraphRuntime(PROMPTS, llm_max_retries=0, client=mismatch, sleep=lambda _: None)
    )
    with pytest.raises(NodeExecutionError, match="CreateSkeleton"):
        graph.invoke(battle_invocation_from_envelope(_envelope()))


@pytest.mark.parametrize(
    "winner_ids",
    [
        ["111111111111111111", "111111111111111111"],
        ["999999999999999999"],
        [],
    ],
)
def test_emergent_winner_ids_fail_closed(winner_ids: list[str]) -> None:
    replies = _replies(2)
    replies[-1] = json.dumps({"winner_ids": winner_ids})
    fake = FakeGemini(replies)
    graph = BattleGraph(
        BattleGraphRuntime(PROMPTS, llm_max_retries=0, client=fake, sleep=lambda _: None)
    )
    with pytest.raises(NodeExecutionError, match="ResolveWinners"):
        graph.invoke(battle_invocation_from_envelope(_envelope()))


def test_modifier_decider_and_attempt_accounting() -> None:
    fake = FakeGemini(
        [
            *_replies(2)[:4],
            _invalid(),
            json.dumps({"story": "Alice survives the storm."}),
            _invalid(),
            json.dumps({"selected_attempt_index": 1, "reason": "Most coherent."}),
            json.dumps({"winner_ids": ["111111111111111111"]}),
        ]
    )
    graph = BattleGraph(
        BattleGraphRuntime(PROMPTS, llm_max_retries=0, client=fake, sleep=lambda _: None)
    )

    result = graph.invoke(battle_invocation_from_envelope(_envelope(max_modifier_retries=1)))

    assert result["attempts_used"] == 2
    assert result["forced_selection"] is True
    assert result["story"] == "Alice survives the storm."
    assert [call["max_output_tokens"] for call in fake.calls][-4:] == [16384, 2048, 1024, 1024]
    decider_prompt = str(fake.calls[-2]["prompt"])
    assert "## FIGHTERS:" in decider_prompt
    assert "## Environment:" in decider_prompt
    assert "## SETTING:" in decider_prompt
    assert "## OUTCOME:" in decider_prompt
    assert "## ATTEMPT 0:" in decider_prompt
    assert "## ATTEMPT 1:" in decider_prompt
    assert "111111111111111111:" in decider_prompt
    assert '"predetermined_winners":null' in decider_prompt


def test_scripted_winners_are_validated_and_need_no_resolution_call() -> None:
    fake = FakeGemini(
        [
            json.dumps(
                {
                    "outcome_type": "one",
                    "episode_count": 2,
                    "predetermined_winners": ["111111111111111111"],
                }
            ),
            json.dumps(
                {
                    "episodes": [
                        {"episode_index": 0, "summary": "Opening"},
                        {"episode_index": 1, "summary": "Alice wins"},
                    ]
                }
            ),
            _episode(0),
            _episode(1, "Alice wins the final exchange."),
            _valid(),
        ]
    )
    graph = BattleGraph(
        BattleGraphRuntime(PROMPTS, llm_max_retries=0, client=fake, sleep=lambda _: None)
    )

    result = graph.invoke(battle_invocation_from_envelope(_envelope(random_winner_mode=True)))

    assert result["winners"] == ["111111111111111111"]
    assert len(fake.calls) == 5
    validator_prompt = str(fake.calls[-1]["prompt"])
    assert "## FIGHTERS:" in validator_prompt
    assert "## Environment:" in validator_prompt
    assert "## SETTING:" in validator_prompt
    assert "## OUTCOME:" in validator_prompt
    assert '"predetermined_winners":["111111111111111111"]' in validator_prompt
    assert '"random_winner_mode":true' in validator_prompt
    assert "111111111111111111:" in validator_prompt


def test_graph_input_rejects_duplicate_ids_and_setting_mismatch_without_call() -> None:
    duplicate = _envelope(
        fighters=[
            {
                "player_id": "111111111111111111",
                "player_nick": "same",
                "fighter_name": "One",
                "description": "One.",
                "strategy": None,
            },
            {
                "player_id": "111111111111111111",
                "player_nick": "same",
                "fighter_name": "Two",
                "description": "Two.",
                "strategy": None,
            },
        ]
    )
    with pytest.raises(NodeExecutionError, match="invalid_input"):
        battle_invocation_from_envelope(duplicate)

    with pytest.raises(NodeExecutionError, match="invalid_input"):
        battle_invocation_from_envelope(_envelope(setting="dreamcore"))


def test_compiled_graph_has_documented_nodes_and_middle_context() -> None:
    fake = FakeGemini(_replies(4))
    graph = BattleGraph(
        BattleGraphRuntime(PROMPTS, llm_max_retries=0, client=fake, sleep=lambda _: None)
    )

    graph.invoke(battle_invocation_from_envelope(_envelope()))

    nodes = graph.compiled.get_graph().nodes
    assert {
        "Predefine",
        "CreateSkeleton",
        "ImplementFirstEpisode",
        "ImplementNextEpisode",
        "ImplementLastEpisode",
        "Validator",
        "Modifier",
        "Decider",
        "ResolveWinners",
    } <= set(nodes)
    next_prompt = str(fake.calls[3]["prompt"])
    last_prompt = str(fake.calls[5]["prompt"])
    assert "## PRIOR EPISODES:" in next_prompt
    assert "Episode 0: Alice fights." in next_prompt
    assert "## PRIOR EPISODES:" in last_prompt
    assert "Episode 2: Alice fights." in last_prompt


def test_solo_no_victor_and_multiple_outcomes_are_exact() -> None:
    solo = _envelope(
        fighters=[
            {
                "player_id": "111111111111111111",
                "player_nick": "Alice",
                "fighter_name": "Alice",
                "description": "A fighter.",
                "strategy": None,
            }
        ]
    )
    none = _replies(2, outcome="none")
    none[-1] = json.dumps({"winner_ids": []})
    result = BattleGraph(
        BattleGraphRuntime(
            PROMPTS, llm_max_retries=0, client=FakeGemini(none), sleep=lambda _: None
        )
    ).invoke(battle_invocation_from_envelope(solo))
    assert result["winners"] == []

    multiple = _replies(2, outcome="multiple")
    multiple[-1] = json.dumps({"winner_ids": ["111111111111111111", "222222222222222222"]})
    result = BattleGraph(
        BattleGraphRuntime(
            PROMPTS, llm_max_retries=0, client=FakeGemini(multiple), sleep=lambda _: None
        )
    ).invoke(
        battle_invocation_from_envelope(
            _envelope(
                fighters=[
                    solo.fighters[0].model_dump(),
                    {
                        "player_id": "222222222222222222",
                        "player_nick": "Bob",
                        "fighter_name": "Bob",
                        "description": "Another fighter.",
                        "strategy": None,
                    },
                ]
            )
        )
    )
    assert result["winners"] == ["111111111111111111", "222222222222222222"]


def test_scripted_decider_uses_rubric_selection_and_preserves_predetermined_ids() -> None:
    fake = FakeGemini(
        [
            json.dumps(
                {
                    "outcome_type": "one",
                    "episode_count": 2,
                    "predetermined_winners": ["111111111111111111"],
                }
            ),
            json.dumps(
                {
                    "episodes": [
                        {"episode_index": 0, "summary": "Start"},
                        {"episode_index": 1, "summary": "End"},
                    ]
                }
            ),
            _episode(0, "The battle begins."),
            _episode(1, "Nobody wins."),
            _invalid(),
            json.dumps({"selected_attempt_index": 0, "reason": "best"}),
        ]
    )
    graph = BattleGraph(
        BattleGraphRuntime(PROMPTS, llm_max_retries=0, client=fake, sleep=lambda _: None)
    )
    result = graph.invoke(
        battle_invocation_from_envelope(
            _envelope(random_winner_mode=True, max_modifier_retries=0)
        )
    )
    assert result["story"] == "The battle begins.\n\nNobody wins."
    assert result["winners"] == ["111111111111111111"]
    assert result["forced_selection"] is True
    decider_prompt = str(fake.calls[-1]["prompt"])
    assert "## FIGHTERS:" in decider_prompt
    assert "## OUTCOME:" in decider_prompt
    assert '"predetermined_winners":["111111111111111111"]' in decider_prompt
    assert "## ATTEMPT 0:" in decider_prompt


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            PredefineOutput,
            {"outcome_type": "one", "episode_count": 2, "predetermined_winners": None},
        ),
        (EpisodeSkeleton, {"episode_index": 0, "summary": "Beat"}),
        (SkeletonOutput, {"episodes": [{"episode_index": 0, "summary": "Beat"}]}),
        (EpisodeOutput, {"episode_index": 0, "text": "Episode"}),
        (StoryOutput, {"story": "Story"}),
        (WinnerResolution, {"winner_ids": []}),
    ],
)
def test_every_battle_llm_output_schema_is_strict_and_forbids_unknown_fields(
    schema: type[BaseModel], payload: dict[str, object]
) -> None:
    payload["unexpected"] = True
    with pytest.raises(StructuredOutputError):
        parse_structured_output(json.dumps(payload), schema)


@pytest.mark.parametrize("episode_count", [1, 6])
def test_predefine_rejects_episode_counts_outside_documented_bounds(episode_count: int) -> None:
    fake = FakeGemini(
        [
            json.dumps(
                {
                    "outcome_type": "one",
                    "episode_count": episode_count,
                    "predetermined_winners": None,
                }
            )
        ]
    )
    graph = BattleGraph(
        BattleGraphRuntime(PROMPTS, llm_max_retries=0, client=fake, sleep=lambda _: None)
    )
    with pytest.raises(NodeExecutionError, match="Predefine"):
        graph.invoke(battle_invocation_from_envelope(_envelope()))


@pytest.mark.parametrize(
    "winner_ids",
    [
        ["Alice"],
        [""],
        ["111111111111111111", "999999999999999999"],
    ],
)
def test_winner_resolution_rejects_nicknames_empty_and_off_session_ids(
    winner_ids: list[str],
) -> None:
    replies = _replies(2)
    replies[-1] = json.dumps({"winner_ids": winner_ids})
    graph = BattleGraph(
        BattleGraphRuntime(
            PROMPTS,
            llm_max_retries=0,
            client=FakeGemini(replies),
            sleep=lambda _: None,
        )
    )
    with pytest.raises(NodeExecutionError, match="ResolveWinners"):
        graph.invoke(battle_invocation_from_envelope(_envelope()))


def test_battle_runtime_output_ceiling_fails_before_the_first_llm_call() -> None:
    fake = FakeGemini([])
    graph = BattleGraph(
        BattleGraphRuntime(
            PROMPTS,
            llm_max_retries=0,
            max_output_tokens=1023,
            client=fake,
            sleep=lambda _: None,
        )
    )
    with pytest.raises(NodeExecutionError, match="output token"):
        graph.invoke(battle_invocation_from_envelope(_envelope()))
    assert fake.calls == []


@pytest.mark.parametrize(
    ("schema", "field", "maximum"),
    [(EpisodeOutput, "text", 3500), (StoryOutput, "story", 12000)],
)
def test_story_output_character_boundaries_are_exact(
    schema: type[BaseModel], field: str, maximum: int
) -> None:
    payload: dict[str, object] = {field: "x" * maximum}
    if schema is EpisodeOutput:
        payload["episode_index"] = 0
    assert parse_structured_output(json.dumps(payload), schema)
    payload[field] = "x" * (maximum + 1)
    with pytest.raises(StructuredOutputError):
        parse_structured_output(json.dumps(payload), schema)
