"""Phase 2 failure-injection and faultable-worker helpers (integration)."""

from __future__ import annotations

from typing import Any

import pytest

from ai_worker.tasks import run_graph_impl
from shared.models import EnvironmentAiTaskEnvelope

pytestmark = pytest.mark.integration


class BarrierResults:
    """Faultable publisher: first publish fails, second succeeds."""

    def __init__(self) -> None:
        self.attempts = 0
        self.published: list[Any] = []

    def publish_result(self, result: Any) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionError("forced pre-ack publish failure")
        self.published.append(result)


@pytest.mark.usefixtures("phase2_brokers")
def test_result_publish_failure_rejects_for_redelivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from celery.exceptions import Reject

    from ai_worker.settings import AiWorkerSettings

    monkeypatch.setenv("APPLICATION_VERSION", "v0.1.0")
    monkeypatch.setenv("AI_WORKER_NODE_ID", "node-local")
    monkeypatch.setenv("AI_WORKER_RABBITMQ_USER", "discordcombatai")
    monkeypatch.setenv("AI_WORKER_RABBITMQ_PASS", "change-me-in-env")
    monkeypatch.setenv("AI_WORKER_TRANSPORT_SHELL", "true")

    settings = AiWorkerSettings()  # type: ignore[call-arg]
    task_id = str(uuid4())
    env = EnvironmentAiTaskEnvelope(
        task_id=task_id,
        created_at=datetime.now(tz=UTC),
        setting="realistic",
        language_locale="uk-UA",
        trace_id="fault",
        guild_id="123456789012345678",
        api_key="AIzaSyTestKey",
        model="gemini-2.5-flash",
        input_type="initial",
        raw_input=["x"],
        existing_environment=None,
        max_enhancer_retries=1,
    )
    barrier = BarrierResults()
    with pytest.raises(Reject) as exc:
        run_graph_impl(
            env.model_dump(mode="json"),
            celery_task_id=task_id,
            settings=settings,
            results=barrier,
        )
    assert exc.value.requeue is True
    assert barrier.attempts == 1
    # Second attempt succeeds (redelivery path).
    run_graph_impl(
        env.model_dump(mode="json"),
        celery_task_id=task_id,
        settings=settings,
        results=barrier,
    )
    assert len(barrier.published) == 1
