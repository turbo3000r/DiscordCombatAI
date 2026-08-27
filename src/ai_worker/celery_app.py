"""Celery application for the AI Worker container."""

from __future__ import annotations

from typing import Any

from celery import Celery
from pydantic import ValidationError

from shared.messaging import AI_TASKS_QUEUE, AI_WORKER_RUN_GRAPH_TASK, ai_tasks_kombu_queue

from .settings import AiWorkerSettings

# Module-level app object must exist before task modules decorate onto it.
app = Celery("ai_worker")


def _runtime_celery_conf(settings: AiWorkerSettings) -> dict[str, Any]:
    return {
        "task_serializer": "json",
        "accept_content": ["json"],
        "result_serializer": "json",
        "result_backend": None,
        "task_ignore_result": True,
        "task_track_started": False,
        "worker_prefetch_multiplier": 1,
        "task_acks_late": True,
        "task_acks_on_failure_or_timeout": False,
        "task_reject_on_worker_lost": True,
        "task_default_queue": AI_TASKS_QUEUE,
        "task_queues": (ai_tasks_kombu_queue(),),
        "task_create_missing_queues": False,
        "broker_connection_retry_on_startup": True,
        "broker_transport_options": {"confirm_publish": True},
        "imports": ("ai_worker.tasks",),
        "task_routes": {AI_WORKER_RUN_GRAPH_TASK: {"queue": AI_TASKS_QUEUE}},
    }


def build_celery_app(settings: AiWorkerSettings) -> Celery:
    celery_app = Celery("ai_worker", broker=settings.broker_url())
    celery_app.conf.update(**_runtime_celery_conf(settings))
    return celery_app


def _apply_runtime_config(celery_app: Celery, settings: AiWorkerSettings) -> None:
    celery_app.conf.update(
        broker_url=settings.broker_url(),
        **_runtime_celery_conf(settings),
    )


def _apply_smoke_config(celery_app: Celery) -> None:
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_ignore_result=True,
        task_create_missing_queues=False,
        imports=("ai_worker.tasks",),
        task_routes={AI_WORKER_RUN_GRAPH_TASK: {"queue": AI_TASKS_QUEUE}},
    )


def _configure_app() -> None:
    try:
        settings = AiWorkerSettings()  # type: ignore[call-arg]
    except ValidationError:
        _apply_smoke_config(app)
    else:
        _apply_runtime_config(app, settings)
        from .bootsteps import register_bootsteps

        register_bootsteps(app)
    # Import after config so @app.task binds to this module's app.
    from . import tasks as _tasks  # noqa: F401


_configure_app()

__all__ = ["app", "build_celery_app"]
