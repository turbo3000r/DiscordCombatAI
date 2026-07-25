"""Shared Celery task identity and RabbitMQ broker URL construction."""

from __future__ import annotations

from urllib.parse import quote

AI_WORKER_RUN_GRAPH_TASK = "ai_worker.tasks.run_graph"


def build_rabbitmq_broker_url(
    *,
    user: str,
    password: str,
    host: str,
    port: int,
    vhost: str,
) -> str:
    """Build an AMQP broker URL with a fully URL-encoded vhost path.

    Credentials are never included in raised exceptions. Callers must not log
    the returned URL at INFO or above.
    """
    if not user:
        raise ValueError("RabbitMQ user must be non-empty")
    if not password:
        raise ValueError("RabbitMQ password must be non-empty")
    if not host:
        raise ValueError("RabbitMQ host must be non-empty")
    if not (1 <= port <= 65535):
        raise ValueError("RabbitMQ port must be in 1..65535")
    if not vhost:
        raise ValueError("RabbitMQ vhost must be non-empty")

    encoded_user = quote(user, safe="")
    encoded_password = quote(password, safe="")
    encoded_vhost = quote(vhost, safe="")
    return f"amqp://{encoded_user}:{encoded_password}@{host}:{port}/{encoded_vhost}"


__all__ = [
    "AI_WORKER_RUN_GRAPH_TASK",
    "build_rabbitmq_broker_url",
]
