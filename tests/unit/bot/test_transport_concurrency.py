from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.ai_transport import AiTransport, PendingDispatch
from shared.models import EnvironmentAiTaskEnvelope


class FakeCelery:
    def send_task(self, *_a: Any, **_k: Any) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_dispatch_runs_publish_off_loop_thread() -> None:
    loop_id = threading.get_ident()
    publish_threads: list[int] = []

    class ThreadAwareCelery(FakeCelery):
        def send_task(self, *_a: Any, **_k: Any) -> None:
            publish_threads.append(threading.get_ident())

    async def on_confirmed(_p: PendingDispatch) -> None:
        assert threading.get_ident() == loop_id

    transport = AiTransport(
        broker_url="amqp://u:p@h:5672/%2Fv",
        admit_dispatch=lambda: True,
        on_confirmed_dispatch=on_confirmed,
        on_result=lambda _r: _noop(),
        celery_app=ThreadAwareCelery(),  # type: ignore[arg-type]
    )
    env = EnvironmentAiTaskEnvelope(
        task_id=str(uuid4()),
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
        setting="realistic",
        language_locale="uk-UA",
        trace_id="t",
        guild_id="123456789012345678",
        api_key="k",
        model="m",
        input_type="initial",
        raw_input=["x"],
        existing_environment=None,
        max_enhancer_retries=1,
    )
    await transport.dispatch_environment(env, lambda _r: _noop())
    assert publish_threads
    assert publish_threads[0] != loop_id


async def _noop(*_a: Any, **_k: Any) -> None:
    return None
