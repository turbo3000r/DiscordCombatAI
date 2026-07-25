"""Phase 2 integration — real-broker transport shell path."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest
from kombu import Connection, Consumer, Queue

from ai_worker.tasks import TRANSPORT_SHELL_RESULT, run_graph_impl
from shared.messaging import AI_TASKS_RESULTS_QUEUE, RABBITMQ_QUEUE_ARGS
from shared.models import EnvironmentAiTaskEnvelope, parse_ai_task_result

pytestmark = pytest.mark.integration


def _broker_url() -> str:
    user = os.getenv("RABBITMQ_DEFAULT_USER", "discordcombatai")
    password = os.getenv("RABBITMQ_DEFAULT_PASS", "change-me-in-env")
    vhost = os.getenv("RABBITMQ_DEFAULT_VHOST", "/discordcombatai")
    return f"amqp://{user}:{password}@127.0.0.1:5672/{quote(vhost, safe='')}"


class RecordingProgress:
    def __init__(self) -> None:
        self.phases: list[str] = []

    def publish_phase(self, *, task_id: str, graph: str, phase: Any) -> None:
        self.phases.append(phase.value)

    def stop(self) -> None:
        return None


@pytest.mark.usefixtures("phase2_brokers")
def test_transport_shell_result_on_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLICATION_VERSION", "v0.1.0")
    monkeypatch.setenv("AI_WORKER_NODE_ID", "node-local")
    rabbit_user = os.getenv("RABBITMQ_DEFAULT_USER", "discordcombatai")
    rabbit_pass = os.getenv("RABBITMQ_DEFAULT_PASS", "change-me-in-env")
    rabbit_vhost = os.getenv("RABBITMQ_DEFAULT_VHOST", "/discordcombatai")
    monkeypatch.setenv("AI_WORKER_RABBITMQ_USER", rabbit_user)
    monkeypatch.setenv("AI_WORKER_RABBITMQ_PASS", rabbit_pass)
    monkeypatch.setenv("AI_WORKER_RABBITMQ_HOST", "127.0.0.1")
    monkeypatch.setenv("AI_WORKER_TRANSPORT_SHELL", "true")
    monkeypatch.setenv("RABBITMQ_DEFAULT_VHOST", rabbit_vhost)

    from ai_worker.rabbitmq import KombuResultPublisher
    from ai_worker.settings import AiWorkerSettings

    settings = AiWorkerSettings()  # type: ignore[call-arg]
    url = _broker_url()

    task_id = str(uuid4())
    env = EnvironmentAiTaskEnvelope(
        task_id=task_id,
        created_at=datetime.now(tz=UTC),
        setting="realistic",
        language_locale="uk-UA",
        trace_id="integration",
        guild_id="123456789012345678",
        api_key="AIzaSyTestKey",
        model="gemini-2.5-flash",
        input_type="initial",
        raw_input=["storm"],
        existing_environment=None,
        max_enhancer_retries=1,
    )
    progress = RecordingProgress()
    result = run_graph_impl(
        env.model_dump(mode="json"),
        celery_task_id=task_id,
        settings=settings,
        progress=progress,
        results=KombuResultPublisher(url),
    )
    assert result["status"] == "success"
    assert progress.phases == ["launching", "composing", "refining", "finishing"]
    assert result["result"]["final_environment"] == TRANSPORT_SHELL_RESULT["final_environment"]

    queue = Queue(
        AI_TASKS_RESULTS_QUEUE,
        durable=True,
        queue_arguments=dict(RABBITMQ_QUEUE_ARGS),
    )
    seen: list[Any] = []

    def _on_message(body: Any, message: Any) -> None:
        data = body if isinstance(body, dict) else json.loads(body)
        parsed = parse_ai_task_result(data)
        if str(parsed.task_id) == task_id:
            seen.append(parsed)
            message.ack()
            raise StopIteration
        message.ack()

    with Connection(url, connect_timeout=5) as connection:
        connection.ensure_connection(max_retries=3)
        with Consumer(connection, queues=[queue], callbacks=[_on_message], accept=["json"]):
            deadline = datetime.now(tz=UTC).timestamp() + 15
            while datetime.now(tz=UTC).timestamp() < deadline and not seen:
                with suppress(TimeoutError, StopIteration):
                    connection.drain_events(timeout=1)
    assert seen, "expected transport-shell result on ai_tasks_results"
