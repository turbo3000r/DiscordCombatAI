from __future__ import annotations

import pytest

from ai_worker.celery_app import app, build_celery_app
from ai_worker.settings import AiWorkerSettings
from ai_worker.tasks import run_graph
from shared.messaging import AI_TASKS_QUEUE, AI_WORKER_RUN_GRAPH_TASK


def test_celery_app_configuration() -> None:
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.result_backend is None
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_acks_late is True
    assert app.conf.task_acks_on_failure_or_timeout is False
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.task_default_queue == AI_TASKS_QUEUE
    assert AI_WORKER_RUN_GRAPH_TASK in app.tasks
    assert run_graph.name == AI_WORKER_RUN_GRAPH_TASK


def test_build_celery_app_uses_encoded_vhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPLICATION_VERSION", "v0.1.0")
    monkeypatch.setenv("AI_WORKER_NODE_ID", "node-local")
    monkeypatch.setenv("AI_WORKER_RABBITMQ_USER", "user")
    monkeypatch.setenv("AI_WORKER_RABBITMQ_PASS", "pass")
    monkeypatch.setenv("RABBITMQ_DEFAULT_VHOST", "/discordcombatai")
    monkeypatch.delenv("NODE_ID", raising=False)
    settings = AiWorkerSettings()  # type: ignore[call-arg]
    built = build_celery_app(settings)
    assert "/%2Fdiscordcombatai" in str(built.conf.broker_url)
