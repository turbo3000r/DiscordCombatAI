from __future__ import annotations

from shared.messaging import (
    AI_TASKS_QUEUE,
    AI_TASKS_RESULTS_QUEUE,
    DEAD_LETTER_QUEUE,
    DLX_EXCHANGE,
    MQTT_TOPIC_POLICIES,
    RABBITMQ_QUEUE_ARGS,
)


def test_mqtt_retain_matrix() -> None:
    assert MQTT_TOPIC_POLICIES["control_bot_desired_state"].retain is True
    assert MQTT_TOPIC_POLICIES["control_ai_worker_desired_state"].retain is True
    assert MQTT_TOPIC_POLICIES["control_bot_activation_grant"].retain is False
    assert MQTT_TOPIC_POLICIES["status_bot_control_ack"].qos == 1
    assert MQTT_TOPIC_POLICIES["progress_ai_worker"].qos == 0


def test_rabbitmq_topology_constants() -> None:
    assert AI_TASKS_QUEUE == "ai_tasks"
    assert AI_TASKS_RESULTS_QUEUE == "ai_tasks_results"
    assert DLX_EXCHANGE == "dlx"
    assert DEAD_LETTER_QUEUE == "dead_letter"
    assert RABBITMQ_QUEUE_ARGS == {"x-dead-letter-exchange": "dlx"}
