from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from bot.modules.events.control_events import BotControlState, SystemClock
from shared.models import ActivationGrantMode, BotDesiredStateValue, ControlAck


class FakeClock:
    def __init__(self) -> None:
        self._mono = 1000.0
        self._wall = datetime(2026, 7, 20, tzinfo=UTC)

    def monotonic(self) -> float:
        return self._mono

    def utcnow(self) -> datetime:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._mono += seconds


async def _noop() -> None:
    return None


def _grant(
    *,
    seq: int = 1,
    mode: str = "active",
    ttl: int = 45,
    node_id: str = "node-local",
    term: str = "cd88086a-fd6d-48d4-8446-39523af2bf70",
) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "grant_id": "366ed38e-1e52-427f-a8c9-726a69628f69",
            "node_id": node_id,
            "head_instance_id": "6b44781e-40f8-4807-9b4b-9087430c14b6",
            "leadership_term": term,
            "command_seq": seq,
            "mode": mode,
            "ttl_sec": ttl,
            "issued_at": "2026-07-20T10:00:00Z",
        }
    ).encode()


@pytest.mark.asyncio
async def test_grant_activates_and_seq_rejects_stale() -> None:
    clock = FakeClock()
    activations: list[str] = []
    acks: list[ControlAck] = []

    async def on_activate() -> None:
        activations.append("active")

    async def publish_ack(ack: ControlAck) -> None:
        acks.append(ack)

    control = BotControlState(
        node_id="node-local",
        max_ttl_sec=60,
        control_drain_timeout_sec=45,
        clock=clock,
        on_activate=on_activate,
        publish_ack=publish_ack,
    )
    await control.on_mqtt_connected()
    await control.handle_message("control/bot/activation_grant", _grant(seq=1))
    assert control.admit_dispatch() is True
    assert activations == ["active"]

    await control.handle_message("control/bot/activation_grant", _grant(seq=1))
    assert len(activations) == 1

    await control.handle_message("control/bot/activation_grant", _grant(seq=2))
    assert len(activations) == 2


@pytest.mark.asyncio
async def test_oversized_ttl_and_wrong_node_rejected() -> None:
    control = BotControlState(
        node_id="node-local",
        max_ttl_sec=60,
        control_drain_timeout_sec=45,
        clock=FakeClock(),
    )
    await control.on_mqtt_connected()
    await control.handle_message("control/bot/activation_grant", _grant(ttl=120))
    assert control.current_grant is None
    await control.handle_message(
        "control/bot/activation_grant", _grant(node_id="other-node")
    )
    assert control.current_grant is None


@pytest.mark.asyncio
async def test_disconnect_soft_stop_and_expiry_hard_stop() -> None:
    clock = FakeClock()
    hard: list[str] = []
    soft: list[str] = []

    async def on_soft() -> None:
        soft.append("soft")

    async def on_hard() -> None:
        hard.append("hard")

    control = BotControlState(
        node_id="node-local",
        max_ttl_sec=60,
        control_drain_timeout_sec=45,
        clock=clock,
        on_activate=_noop,
        on_soft_stop=on_soft,
        on_hard_stop=on_hard,
    )
    await control.on_mqtt_connected()
    await control.handle_message("control/bot/activation_grant", _grant(ttl=45))
    await control.on_mqtt_disconnected()
    assert control.draining is True
    assert soft == ["soft"]
    assert control.admit_dispatch() is False

    clock.advance(45)
    await control.tick()
    assert hard == ["hard"]
    assert control.gateway_connected is False


@pytest.mark.asyncio
async def test_retained_desired_state_cannot_activate() -> None:
    control = BotControlState(
        node_id="node-local",
        max_ttl_sec=60,
        control_drain_timeout_sec=45,
        clock=FakeClock(),
    )
    await control.on_mqtt_connected()
    payload = json.dumps(
        {
            "schema_version": 1,
            "state": "inactive",
            "head_instance_id": "6b44781e-40f8-4807-9b4b-9087430c14b6",
            "leadership_term": None,
            "command_seq": 0,
            "reason": "startup",
            "issued_at": "2026-07-20T10:00:00Z",
        }
    ).encode()
    await control.handle_message("control/bot/desired_state", payload)
    assert control.admit_dispatch() is False
    assert control.desired_state is BotDesiredStateValue.inactive


@pytest.mark.asyncio
async def test_new_term_resets_sequence() -> None:
    clock = FakeClock()
    control = BotControlState(
        node_id="node-local",
        max_ttl_sec=60,
        control_drain_timeout_sec=45,
        clock=clock,
        on_activate=_noop,
    )
    await control.on_mqtt_connected()
    await control.handle_message("control/bot/activation_grant", _grant(seq=5))
    await control.handle_message(
        "control/bot/activation_grant",
        _grant(seq=1, term="11111111-1111-4111-8111-111111111111"),
    )
    assert control.current_grant is not None
    assert control.current_grant.command_seq == 1
    assert control.current_grant.mode is ActivationGrantMode.active


def test_system_clock_exists() -> None:
    clock = SystemClock()
    assert clock.monotonic() >= 0
    assert clock.utcnow().tzinfo is not None
