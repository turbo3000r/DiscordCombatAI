from __future__ import annotations

from ai_worker.rabbitmq import CONFIRM_WAIT_SEC, RecordingResultPublisher
from shared.messaging import AI_TASKS_RESULTS_QUEUE
from shared.models import AiTaskResultSuccess


def test_recording_publisher_and_confirm_timeout_constant() -> None:
    publisher = RecordingResultPublisher()
    result = AiTaskResultSuccess(
        task_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        graph="environment",
        result={"ok": True},
        completed_at="2026-07-15T17:20:00Z",
    )
    publisher.publish_result(result)
    assert publisher.published[0].task_id == result.task_id
    assert CONFIRM_WAIT_SEC == 5.0
    assert AI_TASKS_RESULTS_QUEUE == "ai_tasks_results"


def test_recording_publisher_failure() -> None:
    publisher = RecordingResultPublisher(fail=True)
    result = AiTaskResultSuccess(
        task_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        graph="environment",
        result={"ok": True},
        completed_at="2026-07-15T17:20:00Z",
    )
    try:
        publisher.publish_result(result)
        raise AssertionError("expected ConnectionError")
    except ConnectionError:
        assert publisher.published == []
