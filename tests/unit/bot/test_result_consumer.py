from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.ai_transport import AiTransport


class FakeMessage:
    def __init__(self, *, correlation_id: str | None) -> None:
        self.properties = {"correlation_id": correlation_id}
        self.acked = False
        self.rejected: bool | None = None

    def ack(self) -> None:
        self.acked = True

    def reject(self, requeue: bool = True) -> None:
        self.rejected = requeue


@pytest.mark.asyncio
async def test_valid_result_acked_immediately_and_handed_off() -> None:
    results: list[str] = []

    async def on_confirmed(_p: Any) -> None:
        return None

    async def on_result(result: Any) -> None:
        results.append(str(result.task_id))

    transport = AiTransport(
        broker_url="amqp://u:p@h:5672/%2Fv",
        admit_dispatch=lambda: True,
        on_confirmed_dispatch=on_confirmed,
        on_result=on_result,
        celery_app=object(),  # type: ignore[arg-type]
    )
    loop = __import__("asyncio").get_running_loop()
    transport._loop = loop  # noqa: SLF001
    task_id = str(uuid4())
    body = {
        "schema_version": 1,
        "task_id": task_id,
        "graph": "environment",
        "status": "success",
        "result": {
            "final_environment": {
                "description": "x",
                "tags": [],
                "setting": "realistic",
            }
        },
        "completed_at": "2026-07-20T10:00:00Z",
    }
    message = FakeMessage(correlation_id=task_id)
    transport._handle_raw_result(body, message)  # noqa: SLF001
    await __import__("asyncio").sleep(0.05)
    assert message.acked is True
    assert message.rejected is None
    assert results == [task_id]


@pytest.mark.asyncio
async def test_malformed_result_rejected_without_requeue() -> None:
    transport = AiTransport(
        broker_url="amqp://u:p@h:5672/%2Fv",
        admit_dispatch=lambda: True,
        on_confirmed_dispatch=lambda _p: _noop(),
        on_result=lambda _r: _noop(),
        celery_app=object(),  # type: ignore[arg-type]
    )
    message = FakeMessage(correlation_id="x")
    transport._handle_raw_result(b"not-json", message)  # noqa: SLF001
    assert message.acked is False
    assert message.rejected is False
    assert transport.rejected_malformed == 1


@pytest.mark.asyncio
async def test_correlation_mismatch_rejected() -> None:
    transport = AiTransport(
        broker_url="amqp://u:p@h:5672/%2Fv",
        admit_dispatch=lambda: True,
        on_confirmed_dispatch=lambda _p: _noop(),
        on_result=lambda _r: _noop(),
        celery_app=object(),  # type: ignore[arg-type]
    )
    task_id = str(uuid4())
    body = {
        "schema_version": 1,
        "task_id": task_id,
        "graph": "environment",
        "status": "failed",
        "node": "bot_stall_timeout",
        "reason": "x",
        "completed_at": "2026-07-20T10:00:00Z",
    }
    message = FakeMessage(correlation_id=str(uuid4()))
    transport._handle_raw_result(body, message)  # noqa: SLF001
    assert message.rejected is False


async def _noop(*_a: Any, **_k: Any) -> None:
    return None
