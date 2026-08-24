"""Bot battle dispatch integration against a recording worker result path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.ai_transport import AiTransport, PendingDispatch
from shared.models import (
    AiTaskResultSuccess,
    BattleAiTaskEnvelope,
    EnvironmentState,
    FighterState,
)

pytestmark = pytest.mark.integration


class FakeCelery:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_task(self, name: str, **kwargs: Any) -> None:
        self.calls.append({"name": name, **kwargs})

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_bot_battle_dispatch_result_path() -> None:
    results: list[str] = []

    async def on_confirmed(_pending: PendingDispatch) -> None:
        return None

    async def on_result(result: Any) -> None:
        results.append(str(result.task_id))

    transport = AiTransport(
        broker_url="amqp://user:pass@localhost:5672/%2Fv",
        admit_dispatch=lambda: True,
        on_confirmed_dispatch=on_confirmed,
        on_result=on_result,
        celery_app=FakeCelery(),  # type: ignore[arg-type]
    )
    envelope = BattleAiTaskEnvelope(
        task_id=str(uuid4()),
        created_at=datetime.now(tz=UTC),
        setting="unpredictable-funny",
        language_locale="en",
        trace_id="int",
        guild_id="123456789012345678",
        api_key="k",
        model="m",
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
            tags=["generic", "arena"],
            setting="unpredictable-funny",
        ),
        random_winner_mode=False,
        max_modifier_retries=1,
    )

    async def done(result: Any) -> None:
        results.append(f"done:{result.task_id}")

    await transport.dispatch(envelope, done, command="quick-battle")
    await transport.handle_result_event(
        AiTaskResultSuccess(
            task_id=envelope.task_id,
            graph="battle",
            result={"story": "Done.", "winners": ["u1"]},
            completed_at=datetime.now(tz=UTC),
        )
    )
    assert results
