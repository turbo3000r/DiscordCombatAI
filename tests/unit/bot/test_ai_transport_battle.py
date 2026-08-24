"""Battle envelope dispatch through Bot transport."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.ai_transport import AiTransport, PendingDispatch
from shared.messaging import AI_TASKS_QUEUE, AI_WORKER_RUN_GRAPH_TASK
from shared.models import BattleAiTaskEnvelope, EnvironmentState, FighterState


def _battle_envelope() -> BattleAiTaskEnvelope:
    return BattleAiTaskEnvelope(
        task_id=str(uuid4()),
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
        setting="unpredictable-funny",
        language_locale="en",
        trace_id="trace-b",
        guild_id="123456789012345678",
        api_key="AIzaSyTestKey",
        model="gemini-2.5-flash",
        fighters=[
            FighterState(
                player_id="u1",
                player_nick="Owner",
                fighter_name="Hero",
                description="A brave duelist from the docks.",
                strategy=None,
            )
        ],
        environment=EnvironmentState(
            description="A lantern-lit canal market.",
            tags=["generic", "generic_environment0"],
            setting="unpredictable-funny",
        ),
        random_winner_mode=False,
        max_modifier_retries=3,
    )


class FakeCelery:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_task(self, name: str, **kwargs: Any) -> None:
        self.calls.append({"name": name, **kwargs})

    @property
    def control(self) -> FakeCelery:
        return self

    def revoke(self, task_id: str, terminate: bool = False) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_dispatch_battle_envelope_uses_run_graph_task() -> None:
    celery = FakeCelery()
    confirmed: list[str] = []

    async def on_confirmed(pending: PendingDispatch) -> None:
        confirmed.append(pending.command)

    async def on_result(_result: Any) -> None:
        return None

    transport = AiTransport(
        broker_url="amqp://user:pass@localhost:5672/%2Fv",
        admit_dispatch=lambda: True,
        on_confirmed_dispatch=on_confirmed,
        on_result=on_result,
        celery_app=celery,  # type: ignore[arg-type]
    )
    envelope = _battle_envelope()

    async def done(_r: Any) -> None:
        return None

    await transport.dispatch(envelope, done, command="quick-battle")
    assert celery.calls[0]["name"] == AI_WORKER_RUN_GRAPH_TASK
    assert celery.calls[0]["queue"] == AI_TASKS_QUEUE
    assert confirmed == ["quick-battle"]
    payload = celery.calls[0]["kwargs"]["envelope"]
    assert payload["graph"] == "battle"
    assert payload["random_winner_mode"] is False
