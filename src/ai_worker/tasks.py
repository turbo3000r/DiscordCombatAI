"""Celery task: ai_worker.tasks.run_graph."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from celery.exceptions import Reject
from pydantic import ValidationError

from shared.messaging import AI_WORKER_RUN_GRAPH_TASK
from shared.models import (
    AiTaskResultFailed,
    AiTaskResultSuccess,
    EnvironmentAiTaskEnvelope,
    EnvironmentState,
    TaskPhase,
    UnknownSchemaVersionError,
    parse_ai_task_envelope,
)
from shared.security.redact import redact_sensitive

from .celery_app import app
from .graphs.battle import run_battle_graph
from .graphs.environment import run_environment_graph
from .progress import (
    NoOpProgressPublisher,
    ProgressPublisher,
    safe_publish_phase,
)
from .rabbitmq import KombuResultPublisher, ResultPublisher
from .settings import AiWorkerSettings

TRANSPORT_SHELL_RESULT: dict[str, Any] = {
    "final_environment": {
        "description": "Phase 2 transport-shell canned environment.",
        "tags": ["phase2", "transport-shell"],
        "setting": "realistic",
    },
    "attempts_used": 0,
    "forced_selection": False,
}

WORKER_PHASES = (
    TaskPhase.launching,
    TaskPhase.composing,
    TaskPhase.refining,
    TaskPhase.finishing,
)


def _settings() -> AiWorkerSettings:
    return AiWorkerSettings()  # type: ignore[call-arg]


def run_graph_impl(
    envelope_payload: Any,
    *,
    celery_task_id: str,
    settings: AiWorkerSettings,
    progress: ProgressPublisher | None = None,
    results: ResultPublisher | None = None,
    journal: list[str] | None = None,
    environment_runner: Any = run_environment_graph,
    battle_runner: Any = run_battle_graph,
) -> dict[str, Any]:
    """Execute the transport shell or the selected real Phase 4 graph."""
    events = journal if journal is not None else []
    progress_publisher = progress or NoOpProgressPublisher()
    result_publisher = results or KombuResultPublisher(settings.broker_url())

    try:
        try:
            envelope = parse_ai_task_envelope(envelope_payload)
        except (ValidationError, UnknownSchemaVersionError, ValueError, TypeError) as exc:
            raise Reject(str(exc), requeue=False) from exc

        if str(envelope.task_id) != celery_task_id:
            raise Reject(
                "envelope task_id must equal Celery task id / correlation_id",
                requeue=False,
            )

        # Ensure any accidental stringification of the envelope redacts api_key.
        _ = redact_sensitive(json.dumps({"api_key": envelope.api_key}))

        if settings.transport_shell:
            if not isinstance(envelope, EnvironmentAiTaskEnvelope):
                raise Reject(
                    "transport shell supports graph=environment only",
                    requeue=False,
                )
            for phase in WORKER_PHASES:
                safe_publish_phase(
                    progress_publisher,
                    task_id=str(envelope.task_id),
                    graph=envelope.graph,
                    phase=phase,
                )
                events.append(phase.value)
            EnvironmentState.model_validate(TRANSPORT_SHELL_RESULT["final_environment"])
            result: AiTaskResultSuccess | AiTaskResultFailed = AiTaskResultSuccess(
                task_id=envelope.task_id,
                graph="environment",
                result=dict(TRANSPORT_SHELL_RESULT),
                completed_at=datetime.now(tz=UTC),
            )
        else:
            safe_publish_phase(
                progress_publisher,
                task_id=str(envelope.task_id),
                graph=envelope.graph,
                phase=TaskPhase.launching,
            )
            events.append(TaskPhase.launching.value)
            last_phase: TaskPhase | None = TaskPhase.launching

            def publish_graph_phase(phase: TaskPhase) -> None:
                nonlocal last_phase
                if phase != last_phase:
                    safe_publish_phase(
                        progress_publisher,
                        task_id=str(envelope.task_id),
                        graph=envelope.graph,
                        phase=phase,
                    )
                    events.append(phase.value)
                    last_phase = phase

            try:
                if isinstance(envelope, EnvironmentAiTaskEnvelope):
                    graph_result = environment_runner(
                        envelope,
                        llm_max_retries=settings.llm_max_retries,
                        publish_phase=publish_graph_phase,
                    )
                    EnvironmentState.model_validate(graph_result["final_environment"])
                else:
                    graph_result = battle_runner(
                        envelope,
                        llm_max_retries=settings.llm_max_retries,
                        publish_phase=publish_graph_phase,
                    )
                result = AiTaskResultSuccess(
                    task_id=envelope.task_id,
                    graph=envelope.graph,
                    result=graph_result,
                    completed_at=datetime.now(tz=UTC),
                )
            except Exception as exc:
                node = getattr(exc, "node", "invalid_input")
                result = AiTaskResultFailed(
                    task_id=envelope.task_id,
                    graph=envelope.graph,
                    node=node,
                    reason="graph execution failed",
                    completed_at=datetime.now(tz=UTC),
                )

        try:
            result_publisher.publish_result(result)
        except Exception as exc:
            raise Reject("result publish failed", requeue=True) from exc
        events.append("result_confirmed")
        events.append("task_return")
        return result.model_dump(mode="json")
    finally:
        progress_publisher.stop()


@app.task(name=AI_WORKER_RUN_GRAPH_TASK, bind=True)  # type: ignore[untyped-decorator]
def run_graph(self: Any, envelope: Any) -> dict[str, Any]:
    celery_task_id = str(self.request.id)
    return run_graph_impl(
        envelope,
        celery_task_id=celery_task_id,
        settings=_settings(),
    )


__all__ = [
    "TRANSPORT_SHELL_RESULT",
    "WORKER_PHASES",
    "run_graph",
    "run_graph_impl",
]
