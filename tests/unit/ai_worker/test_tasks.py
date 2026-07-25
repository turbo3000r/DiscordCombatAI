from __future__ import annotations

import json
from pathlib import Path

import pytest
from celery.exceptions import Reject

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


def test_shell_disabled_rejected_without_requeue() -> None:
    with pytest.raises(Reject) as exc_info:
        run_graph_impl(
            _envelope(),
            celery_task_id=TASK_ID,
            settings=_settings(transport_shell=False),
            results=RecordingResultPublisher(),
        )
    assert exc_info.value.requeue is False


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
