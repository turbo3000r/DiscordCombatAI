from __future__ import annotations

import json
from pathlib import Path

import pytest
from celery.exceptions import Reject

from ai_worker.graphs.foundation import NodeExecutionError
from ai_worker.progress import RecordingProgressPublisher
from ai_worker.rabbitmq import RecordingResultPublisher
from ai_worker.settings import AiWorkerSettings
from ai_worker.tasks import TRANSPORT_SHELL_RESULT, run_graph_impl
from shared.models import TaskPhase, parse_ai_task_result
from shared.security.redact import redact_sensitive

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "contracts"
TASK_ID = "6b44781e-40f8-4807-9b4b-9087430c14b6"


def _envelope(**overrides: object) -> dict[str, object]:
    payload = json.loads((FIXTURES / "ai_task_environment.json").read_text(encoding="utf-8"))
    payload.update(overrides)
    return payload


def _settings(*, transport_shell: bool = True) -> AiWorkerSettings:
    return AiWorkerSettings(
        node_id="node-local",
        application_version="v0.1.0",
        rabbitmq_user="discordcombatai",
        rabbitmq_pass="change-me-in-env",
        transport_shell=transport_shell,
    )


def test_transport_shell_progress_before_result_before_return() -> None:
    progress = RecordingProgressPublisher()
    results = RecordingResultPublisher()
    journal: list[str] = []

    payload = run_graph_impl(
        _envelope(),
        celery_task_id=TASK_ID,
        settings=_settings(),
        progress=progress,
        results=results,
        journal=journal,
    )

    assert journal == [
        "launching",
        "composing",
        "refining",
        "finishing",
        "result_confirmed",
        "task_return",
    ]
    assert progress.phases == [
        TaskPhase.launching,
        TaskPhase.composing,
        TaskPhase.refining,
        TaskPhase.finishing,
    ]
    assert progress.stopped is True
    assert len(results.published) == 1
    result = parse_ai_task_result(payload)
    assert result.status == "success"
    assert result.task_id == TASK_ID
    assert result.result == TRANSPORT_SHELL_RESULT


def test_progress_failure_does_not_block_result() -> None:
    progress = RecordingProgressPublisher(fail=True)
    results = RecordingResultPublisher()
    journal: list[str] = []

    run_graph_impl(
        _envelope(),
        celery_task_id=TASK_ID,
        settings=_settings(),
        progress=progress,
        results=results,
        journal=journal,
    )

    assert journal[-2:] == ["result_confirmed", "task_return"]
    assert len(results.published) == 1
    assert progress.stopped is True


def test_result_publish_failure_requeues() -> None:
    with pytest.raises(Reject) as exc_info:
        run_graph_impl(
            _envelope(),
            celery_task_id=TASK_ID,
            settings=_settings(),
            progress=RecordingProgressPublisher(),
            results=RecordingResultPublisher(fail=True),
        )
    assert exc_info.value.requeue is True


def test_unknown_schema_version_rejected_without_requeue() -> None:
    with pytest.raises(Reject) as exc_info:
        run_graph_impl(
            _envelope(schema_version=99),
            celery_task_id=TASK_ID,
            settings=_settings(),
            results=RecordingResultPublisher(),
        )
    assert exc_info.value.requeue is False


def test_task_id_mismatch_rejected_without_requeue() -> None:
    with pytest.raises(Reject) as exc_info:
        run_graph_impl(
            _envelope(),
            celery_task_id="11111111-1111-4111-8111-111111111111",
            settings=_settings(),
            results=RecordingResultPublisher(),
        )
    assert exc_info.value.requeue is False


def test_shell_disabled_runs_real_environment_graph() -> None:
    results = RecordingResultPublisher()
    payload = run_graph_impl(
        _envelope(),
        celery_task_id=TASK_ID,
        settings=_settings(transport_shell=False),
        results=results,
        environment_runner=lambda *_args, **_kwargs: {
            "final_environment": TRANSPORT_SHELL_RESULT["final_environment"],
            "attempts_used": 1,
            "forced_selection": False,
        },
    )
    result = parse_ai_task_result(payload)
    assert result.status == "success"
    assert result.result["attempts_used"] == 1


def test_real_graph_receives_validated_worker_bounds() -> None:
    received: dict[str, object] = {}

    def environment_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        received.update(kwargs)
        return {
            "final_environment": TRANSPORT_SHELL_RESULT["final_environment"],
            "attempts_used": 1,
            "forced_selection": False,
        }

    settings = _settings(transport_shell=False).model_copy(
        update={
            "environment_max_enhancer_retries": 2,
            "environment_task_deadline_sec": 601,
            "environment_max_input_tokens": 120_001,
            "environment_max_output_tokens": 30_001,
        }
    )
    run_graph_impl(
        _envelope(),
        celery_task_id=TASK_ID,
        settings=settings,
        results=RecordingResultPublisher(),
        environment_runner=environment_runner,
    )

    assert received["llm_max_retries"] == 2
    assert callable(received["publish_phase"])
    assert received["max_enhancer_retries"] == 2
    assert received["deadline_sec"] == 601
    assert received["max_input_tokens"] == 120_001
    assert received["max_output_tokens"] == 30_001


def test_real_graph_progress_order_and_publish_failures_do_not_block_result() -> None:
    progress = RecordingProgressPublisher(fail=True)
    journal: list[str] = []

    def graph_with_phases(
        _envelope: object, *, publish_phase: object, **_kwargs: object
    ) -> dict[str, object]:
        assert callable(publish_phase)
        publish_phase(TaskPhase.composing)
        publish_phase(TaskPhase.refining)
        publish_phase(TaskPhase.finishing)
        return {
            "final_environment": TRANSPORT_SHELL_RESULT["final_environment"],
            "attempts_used": 1,
            "forced_selection": False,
        }

    results = RecordingResultPublisher()
    payload = run_graph_impl(
        _envelope(),
        celery_task_id=TASK_ID,
        settings=_settings(transport_shell=False),
        progress=progress,
        results=results,
        journal=journal,
        environment_runner=graph_with_phases,
    )
    assert parse_ai_task_result(payload).status == "success"
    assert journal == [
        "launching",
        "composing",
        "refining",
        "finishing",
        "result_confirmed",
        "task_return",
    ]
    assert len(results.published) == 1
    assert progress.stopped is True


def test_battle_graph_rejected_in_transport_shell() -> None:
    battle = json.loads((FIXTURES / "ai_task_battle.json").read_text(encoding="utf-8"))
    with pytest.raises(Reject) as exc_info:
        run_graph_impl(
            battle,
            celery_task_id=str(battle["task_id"]),
            settings=_settings(),
            results=RecordingResultPublisher(),
        )
    assert exc_info.value.requeue is False


def test_real_battle_graph_receives_validated_worker_bounds() -> None:
    battle = json.loads((FIXTURES / "ai_task_battle.json").read_text(encoding="utf-8"))
    received: dict[str, object] = {}

    def battle_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        received.update(kwargs)
        return {
            "story": "A finished battle.",
            "winners": [],
            "attempts_used": 1,
            "forced_selection": False,
        }

    settings = _settings(transport_shell=False).model_copy(
        update={
            "llm_max_retries": 2,
            "battle_max_modifier_retries": 2,
            "battle_task_deadline_sec": 841,
            "battle_max_input_tokens": 350_001,
            "battle_max_output_tokens": 90_001,
        }
    )
    run_graph_impl(
        battle,
        celery_task_id=str(battle["task_id"]),
        settings=settings,
        results=RecordingResultPublisher(),
        battle_runner=battle_runner,
    )

    assert received["llm_max_retries"] == 2
    assert callable(received["publish_phase"])
    assert received["max_modifier_retries"] == 2
    assert received["deadline_sec"] == 841
    assert received["max_input_tokens"] == 350_001
    assert received["max_output_tokens"] == 90_001


def test_battle_graph_dispatches_real_runner_without_transport_shell() -> None:
    battle = json.loads((FIXTURES / "ai_task_battle.json").read_text(encoding="utf-8"))
    results = RecordingResultPublisher()
    payload = run_graph_impl(
        battle,
        celery_task_id=str(battle["task_id"]),
        settings=_settings(transport_shell=False),
        results=results,
        battle_runner=lambda *_args, **_kwargs: {
            "story": "A finished battle.",
            "winners": [],
            "attempts_used": 1,
            "forced_selection": False,
        },
    )
    result = parse_ai_task_result(payload)
    assert result.status == "success"
    assert result.graph == "battle"
    assert result.result["story"] == "A finished battle."


def test_graph_failure_publishes_actual_failing_node() -> None:
    results = RecordingResultPublisher()

    def fail_graph(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise NodeExecutionError("Enhancer", "retry budget exhausted")

    payload = run_graph_impl(
        _envelope(),
        celery_task_id=TASK_ID,
        settings=_settings(transport_shell=False),
        results=results,
        environment_runner=fail_graph,
    )
    result = parse_ai_task_result(payload)
    assert result.status == "failed"
    assert result.node == "Enhancer"
    assert len(results.published) == 1


def test_redelivery_same_task_id_is_idempotent_shape() -> None:
    results = RecordingResultPublisher()
    first = run_graph_impl(
        _envelope(),
        celery_task_id=TASK_ID,
        settings=_settings(),
        results=results,
    )
    second = run_graph_impl(
        _envelope(),
        celery_task_id=TASK_ID,
        settings=_settings(),
        results=results,
    )
    assert first["task_id"] == second["task_id"] == TASK_ID
    assert len(results.published) == 2


def test_api_key_redacted_from_string_form() -> None:
    envelope = _envelope()
    redacted = redact_sensitive(f"api_key={envelope['api_key']}")
    assert "AIzaSyTestKey" not in redacted
    assert "[REDACTED]" in redacted


def test_live_progress_connect_failure_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_worker.progress import NoOpProgressPublisher
    from ai_worker.tasks import _live_progress_publisher

    closed = {"n": 0}

    class FakeTransport:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def connect(self) -> None:
            raise ConnectionError("mosquitto down")

        def close(self) -> None:
            closed["n"] += 1

    monkeypatch.setattr("ai_worker.tasks.SyncPahoMqttTransport", FakeTransport)
    publisher = _live_progress_publisher(_settings())
    assert isinstance(publisher, NoOpProgressPublisher)
    assert closed["n"] == 1


def test_live_progress_success_uses_heartbeat_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_worker.progress import HeartbeatProgressPublisher
    from ai_worker.tasks import _live_progress_publisher

    closed = {"n": 0}

    class FakeTransport:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def connect(self) -> None:
            return None

        def close(self) -> None:
            closed["n"] += 1

        def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
            return None

    monkeypatch.setattr("ai_worker.tasks.SyncPahoMqttTransport", FakeTransport)
    publisher = _live_progress_publisher(_settings(transport_shell=False))
    assert isinstance(publisher, HeartbeatProgressPublisher)
    publisher.publish_phase(task_id=TASK_ID, graph="environment", phase=TaskPhase.launching)
    publisher.stop()
    assert closed["n"] == 1


def test_celery_entry_wires_live_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_worker.tasks import run_graph

    seen: dict[str, object] = {}
    live = object()

    def fake_impl(_envelope: object, **kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr("ai_worker.tasks.run_graph_impl", fake_impl)
    monkeypatch.setattr("ai_worker.tasks._live_progress_publisher", lambda _settings: live)
    monkeypatch.setattr("ai_worker.tasks._settings", lambda: _settings())
    run_graph.push_request(id=TASK_ID)
    try:
        run_graph.run(_envelope())
    finally:
        run_graph.pop_request()
    assert seen["progress"] is live
    assert seen["celery_task_id"] == TASK_ID
