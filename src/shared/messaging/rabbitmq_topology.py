from __future__ import annotations

AI_TASKS_QUEUE = "ai_tasks"
AI_TASKS_RESULTS_QUEUE = "ai_tasks_results"
DLX_EXCHANGE = "dlx"
DEAD_LETTER_QUEUE = "dead_letter"

RABBITMQ_QUEUE_ARGS = {
    "x-dead-letter-exchange": DLX_EXCHANGE,
}


__all__ = [
    "AI_TASKS_QUEUE",
    "AI_TASKS_RESULTS_QUEUE",
    "DEAD_LETTER_QUEUE",
    "DLX_EXCHANGE",
    "RABBITMQ_QUEUE_ARGS",
]
