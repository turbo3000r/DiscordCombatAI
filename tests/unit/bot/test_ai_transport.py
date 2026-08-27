from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.ai_transport import (
    PUBLISH_RETRY_POLICY,
    AiTransport,
    PendingDispatch,
    build_bot_celery_app,
    next_reconnect_delay,
)
from shared.messaging import AI_TASKS_QUEUE, AI_WORKER_RUN_GRAPH_TASK
from shared.models import EnvironmentAiTaskEnvelope, TaskPhase, TaskProgressMessage


def _envelope(task_id: str | None = None) -> EnvironmentAiTaskEnvelope:
    return EnvironmentAiTaskEnvelope(
        task_id=task_id or str(uuid4()),
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
        setting="realistic",
        language_locale="uk-UA",
        trace_id="trace-1",
        guild_id="123456789012345678",
        api_key="AIzaSyTestKey",
        model="gemini-2.5-flash",
        input_type="initial",
        raw_input=["A stormy battlefield."],
        existing_environment=None,
        max_enhancer_retries=3,
    )


class FakeCelery:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.revokes: list[tuple[str, bool]] = []

    def send_task(self, name: str, **kwargs: Any) -> None:
        if self.fail:
            raise ConnectionError("publish confirm failed")
        self.calls.append({"name": name, **kwargs})

    @property
    def control(self) -> FakeCelery:
        return self

    def revoke(self, task_id: str, terminate: bool = False) -> None:
        self.revokes.append((task_id, terminate))

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_dispatch_send_task_kwargs_and_confirm_creates_pending_handoff() -> None:
    celery = FakeCelery()
    confirmed: list[str] = []

    async def on_confirmed(pending: PendingDispatch) -> None:
        confirmed.append(pending.task_id)

    async def on_result(_result: Any) -> None:
        return None

    transport = AiTransport(
        broker_url="amqp://user:pass@localhost:5672/%2Fvhost",
        admit_dispatch=lambda: True,
        on_confirmed_dispatch=on_confirmed,
        on_result=on_result,
        celery_app=celery,  # type: ignore[arg-type]
    )
    env = _envelope()
    completions: list[str] = []

    async def done(_r: Any) -> None:
        completions.append("done")

    await transport.dispatch_environment(env, done)
    assert len(celery.calls) == 1
    call = celery.calls[0]
    assert call["name"] == AI_WORKER_RUN_GRAPH_TASK
    assert call["task_id"] == str(env.task_id)
    assert call["queue"] == AI_TASKS_QUEUE
    assert call["retry"] is True
    assert "envelope" in call["kwargs"]
    assert confirmed == [str(env.task_id)]
    assert transport.pending_ids == set()
    assert "pass" not in repr(transport)


@pytest.mark.asyncio
async def test_publish_failure_clears_reservation() -> None:
    celery = FakeCelery(fail=True)
    created = {"n": 0}

    async def on_confirmed(_pending: PendingDispatch) -> None:
        created["n"] += 1

    transport = AiTransport(
        broker_url="amqp://user:pass@localhost:5672/%2Fvhost",
        admit_dispatch=lambda: True,
        on_confirmed_dispatch=on_confirmed,
        on_result=lambda _r: _async_noop(),
        celery_app=celery,  # type: ignore[arg-type]
    )
    with pytest.raises(ConnectionError):
        await transport.dispatch_environment(_envelope(), lambda _r: _async_noop())
    assert created["n"] == 0
    assert transport.pending_ids == set()


async def _async_noop(*_a: Any, **_k: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_early_progress_buffered_until_confirm() -> None:
    celery = FakeCelery()
    progress_seen: list[str] = []

    async def on_confirmed(_pending: PendingDispatch) -> None:
        return None

    async def on_progress(msg: TaskProgressMessage) -> None:
        progress_seen.append(msg.phase.value)

    transport = AiTransport(
        broker_url="amqp://user:pass@localhost:5672/%2Fvhost",
        admit_dispatch=lambda: True,
        on_confirmed_dispatch=on_confirmed,
        on_result=_async_noop,
        on_progress=on_progress,
        celery_app=celery,  # type: ignore[arg-type]
    )
    env = _envelope()
    task_id = str(env.task_id)
    # Simulate reservation before confirm by injecting pending.
    from bot.modules.services.ai_transport import PendingDispatch as PD

    transport._pending[task_id] = PD(  # noqa: SLF001
        task_id=task_id,
        envelope=env,
        completion_callback=_async_noop,
        confirmed=False,
    )
    early = TaskProgressMessage(
        task_id=task_id,
        graph="environment",
        phase=TaskPhase.launching,
        timestamp=datetime(2026, 7, 20, tzinfo=UTC),
    )
    await transport.handle_progress(early)
    assert progress_seen == []
    assert len(transport._pending[task_id].buffered_progress) == 1  # noqa: SLF001

    # Confirm path replays
    transport._pending[task_id].confirmed = True  # noqa: SLF001
    buffered = list(transport._pending[task_id].buffered_progress)  # noqa: SLF001
    transport._pending.pop(task_id)
    for item in buffered:
        await transport._on_progress_safe(item)  # noqa: SLF001
    assert progress_seen == ["launching"]


def test_reconnect_schedule_bounds() -> None:
    delays = [next_reconnect_delay(i) for i in range(1, 12)]
    assert delays[0] <= 1.2
    assert max(delays) <= 72.0  # 60 + 20%


def test_build_bot_celery_app_no_backend() -> None:
    app = build_bot_celery_app("amqp://user:pass@localhost:5672/%2Fvhost")
    assert app.conf.result_backend is None or app.conf.result_backend is False
    assert app.conf.broker_transport_options.get("confirm_publish") is True
    assert app.conf.task_publish_retry is True
    assert app.conf.task_publish_retry_policy["max_retries"] == PUBLISH_RETRY_POLICY["max_retries"]
    assert app.conf.task_publish_retry_policy["interval_start"] == 1.0
    assert app.conf.task_publish_retry_policy["interval_max"] == 60.0
    assert app.conf.broker_connection_retry is True
    assert app.conf.task_create_missing_queues is False
    queues = list(app.conf.task_queues or ())
    assert len(queues) == 1
    assert queues[0].name == AI_TASKS_QUEUE
    assert queues[0].no_declare is True
    assert queues[0].queue_arguments == {"x-dead-letter-exchange": "dlx"}
