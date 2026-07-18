from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TopicPolicy:
    topic: str
    qos: int
    retain: bool
    owner: str


MQTT_TOPIC_POLICIES = {
    "control_bot_desired_state": TopicPolicy(
        topic="control/bot/desired_state",
        qos=1,
        retain=True,
        owner="Head",
    ),
    "control_bot_activation_grant": TopicPolicy(
        topic="control/bot/activation_grant",
        qos=1,
        retain=False,
        owner="Head",
    ),
    "control_ai_worker_desired_state": TopicPolicy(
        topic="control/ai_worker/desired_state",
        qos=1,
        retain=True,
        owner="Head",
    ),
    "status_bot_control_ack": TopicPolicy(
        topic="status/bot/control_ack",
        qos=1,
        retain=False,
        owner="Bot",
    ),
    "status_bot_drain_progress": TopicPolicy(
        topic="status/bot/drain_progress",
        qos=1,
        retain=False,
        owner="Bot",
    ),
    "status_ai_worker_pause_ack": TopicPolicy(
        topic="status/ai_worker/pause_ack",
        qos=1,
        retain=False,
        owner="AI Worker",
    ),
    "progress_ai_worker": TopicPolicy(
        topic="progress/ai_worker/#",
        qos=0,
        retain=False,
        owner="AI Worker",
    ),
}


__all__ = ["MQTT_TOPIC_POLICIES", "TopicPolicy"]
