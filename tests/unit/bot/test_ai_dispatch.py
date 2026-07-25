from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.ai_transport import AiTransport, PendingDispatch
from shared.models import EnvironmentAiTaskEnvelope


def _envelope() -> EnvironmentAiTaskEnvelope:
    return EnvironmentAiTaskEnvelope(
        task_id=str(uuid4()),
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
        setting="realistic",
        language_locale="uk-UA",
        trace_id="trace-1",
        guild_id="123456789012345678",
        api_key="AIzaSyTestKey",
        model="gemini-2.5-flash",
        input_type="initial",
        raw_input=["storm"],
        existing_environment=None,
        max_enhancer_retries=3,
    )


class FakeCelery:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_task(self, name: str, **kwargs: Any) -> None:
        self.calls.append({"name": name, **kwargs})

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_dispatch_requires_admission() -> None:
    async def on_confirmed(_p: PendingDispatch) -> None:
        raise AssertionError("should not confirm")

    transport = AiTransport(
        broker_url="amqp://u:p@h:5672/%2Fv",
        admit_dispatch=lambda: False,
        on_confirmed_dispatch=on_confirmed,
        on_result=lambda _r: _noop(),
        celery_app=FakeCelery(),  # type: ignore[arg-type]
    )

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    with pytest.raises(PermissionError):
        await transport.dispatch_environment(_envelope(), _noop)
