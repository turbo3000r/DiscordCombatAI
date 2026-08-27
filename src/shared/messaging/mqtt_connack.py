"""paho-mqtt v1/v2 CONNACK success check."""

from __future__ import annotations


def mqtt_connect_accepted(reason_code: object) -> bool:
    """Return True when MQTT CONNACK succeeded.

    paho-mqtt 2 CallbackAPI VERSION2 passes a ReasonCode that is not int()-able.
    """
    is_failure = getattr(reason_code, "is_failure", None)
    if isinstance(is_failure, bool):
        return not is_failure
    value = getattr(reason_code, "value", reason_code)
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return str(reason_code) in {"0", "Success"}


__all__ = ["mqtt_connect_accepted"]
