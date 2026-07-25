from __future__ import annotations

import pytest

from shared.messaging import (
    AI_TASKS_QUEUE,
    AI_TASKS_RESULTS_QUEUE,
    AI_WORKER_RUN_GRAPH_TASK,
    DEAD_LETTER_QUEUE,
    DLX_EXCHANGE,
    MQTT_TOPIC_POLICIES,
    RABBITMQ_QUEUE_ARGS,
    build_rabbitmq_broker_url,
)


def test_mqtt_retain_matrix() -> None:
    assert MQTT_TOPIC_POLICIES["control_bot_desired_state"].retain is True
    assert MQTT_TOPIC_POLICIES["control_ai_worker_desired_state"].retain is True
    assert MQTT_TOPIC_POLICIES["control_bot_activation_grant"].retain is False
    assert MQTT_TOPIC_POLICIES["status_bot_control_ack"].qos == 1
    assert MQTT_TOPIC_POLICIES["progress_ai_worker"].qos == 0
    assert "leader_heartbeat" not in MQTT_TOPIC_POLICIES
    assert "update_available" not in MQTT_TOPIC_POLICIES


def test_rabbitmq_topology_constants() -> None:
    assert AI_TASKS_QUEUE == "ai_tasks"
    assert AI_TASKS_RESULTS_QUEUE == "ai_tasks_results"
    assert DLX_EXCHANGE == "dlx"
    assert DEAD_LETTER_QUEUE == "dead_letter"
    assert RABBITMQ_QUEUE_ARGS == {"x-dead-letter-exchange": "dlx"}


def test_celery_task_name_constant() -> None:
    assert AI_WORKER_RUN_GRAPH_TASK == "ai_worker.tasks.run_graph"


def test_build_rabbitmq_broker_url_encodes_default_vhost() -> None:
    url = build_rabbitmq_broker_url(
        user="discordcombatai",
        password="change-me-in-env",
        host="rabbitmq",
        port=5672,
        vhost="/discordcombatai",
    )
    assert url == (
        "amqp://discordcombatai:change-me-in-env@rabbitmq:5672/%2Fdiscordcombatai"
    )


def test_build_rabbitmq_broker_url_encodes_special_characters() -> None:
    url = build_rabbitmq_broker_url(
        user="user@name",
        password="p@ss:word/!",
        host="broker.local",
        port=5672,
        vhost="/vhost with spaces",
    )
    assert url.startswith("amqp://user%40name:p%40ss%3Aword%2F%21@broker.local:5672/")
    assert url.endswith("/%2Fvhost%20with%20spaces")


def test_build_rabbitmq_broker_url_rejects_empty_fields_without_leaking_password() -> None:
    secret = "super-secret-value"
    with pytest.raises(ValueError, match="host must be non-empty") as exc_info:
        build_rabbitmq_broker_url(
            user="user",
            password=secret,
            host="",
            port=5672,
            vhost="/discordcombatai",
        )
    message = str(exc_info.value)
    assert secret not in message
    assert "super-secret" not in message
