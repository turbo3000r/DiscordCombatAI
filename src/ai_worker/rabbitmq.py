"""Manual result publishing onto ai_tasks_results."""

from __future__ import annotations

import json
from typing import Protocol

from kombu import Connection, Producer, Queue
from kombu.exceptions import KombuError

from shared.messaging import AI_TASKS_RESULTS_QUEUE, RABBITMQ_QUEUE_ARGS
from shared.models import AiTaskResult

CONFIRM_WAIT_SEC = 5.0


class ResultPublisher(Protocol):
    def publish_result(self, result: AiTaskResult) -> None: ...


class KombuResultPublisher:
    """Publish persistent JSON results with publisher confirms."""

    def __init__(self, broker_url: str) -> None:
        self._broker_url = broker_url
        self._queue = Queue(
            AI_TASKS_RESULTS_QUEUE,
            durable=True,
            queue_arguments=dict(RABBITMQ_QUEUE_ARGS),
        )

    def publish_result(self, result: AiTaskResult) -> None:
        payload = result.model_dump(mode="json")
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        try:
            with Connection(self._broker_url, connect_timeout=CONFIRM_WAIT_SEC) as connection:
                connection.ensure_connection(max_retries=1)
                # Passive verify only — definitions.json owns topology.
                self._queue(connection).queue_declare(passive=True)
                producer = Producer(connection)
                producer.publish(
                    body,
                    exchange="",
                    routing_key=AI_TASKS_RESULTS_QUEUE,
                    correlation_id=str(result.task_id),
                    content_type="application/json",
                    content_encoding="utf-8",
                    delivery_mode=2,
                    retry=False,
                    timeout=CONFIRM_WAIT_SEC,
                )
        except (KombuError, OSError, TimeoutError) as exc:
            raise ConnectionError("failed to publish ai_tasks_results") from exc


class RecordingResultPublisher:
    """Test double that records published results."""

    def __init__(self, *, fail: bool = False) -> None:
        self.published: list[AiTaskResult] = []
        self.fail = fail

    def publish_result(self, result: AiTaskResult) -> None:
        if self.fail:
            raise ConnectionError("forced result publish failure")
        self.published.append(result)


__all__ = [
    "CONFIRM_WAIT_SEC",
    "KombuResultPublisher",
    "RecordingResultPublisher",
    "ResultPublisher",
]
