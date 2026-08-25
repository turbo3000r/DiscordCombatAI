from __future__ import annotations

from typing import Any

AI_TASKS_QUEUE = "ai_tasks"
AI_TASKS_RESULTS_QUEUE = "ai_tasks_results"
DLX_EXCHANGE = "dlx"
DEAD_LETTER_QUEUE = "dead_letter"

RABBITMQ_QUEUE_ARGS = {
    "x-dead-letter-exchange": DLX_EXCHANGE,
}


def ai_tasks_kombu_queue() -> Any:
    """Build the definitions.json queue object. Import kombu only at call time.

    ``shared.messaging`` is loaded by containers that do not install kombu
    (dev-support, Web). Keep this module importable without Celery/kombu.
    """
    from kombu import Exchange, Queue

    return Queue(
        AI_TASKS_QUEUE,
        exchange=Exchange(AI_TASKS_QUEUE, type="direct", durable=True),
        routing_key=AI_TASKS_QUEUE,
        durable=True,
        queue_arguments=dict(RABBITMQ_QUEUE_ARGS),
        no_declare=True,
    )


__all__ = [
    "AI_TASKS_QUEUE",
    "AI_TASKS_RESULTS_QUEUE",
    "DEAD_LETTER_QUEUE",
    "DLX_EXCHANGE",
    "RABBITMQ_QUEUE_ARGS",
    "ai_tasks_kombu_queue",
]
