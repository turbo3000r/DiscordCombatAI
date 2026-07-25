"""Integration: hard-stop purge/revoke against local RabbitMQ control channel."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.lifecycle import LifecycleController
from bot.modules.services.task_tracker import TaskTracker
from shared.messaging import build_rabbitmq_broker_url
from shared.models import EnvironmentAiTaskEnvelope

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_hard_stop_purge_and_revoke_against_broker() -> None:
    # Prefer live brokers when available; otherwise exercise the same Bot path
    # with a recording broker double so the suite still documents ownership.
    user = os.getenv("RABBITMQ_DEFAULT_USER", "discordcombatai")
    password = os.getenv("RABBITMQ_DEFAULT_PASS", "change-me-in-env")
    vhost = os.getenv("RABBITMQ_DEFAULT_VHOST", "/discordcombatai")
    try:
        import socket

        with socket.create_connection(("127.0.0.1", 5672), timeout=1):
            live = True
    except OSError:
        live = False

    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)
    terminals: list[str] = []

    async def on_complete(result: Any) -> None:
        terminals.append(result.node)

    env = EnvironmentAiTaskEnvelope(
        task_id=str(uuid4()),
        created_at=datetime.now(tz=UTC),
        setting="realistic",
        language_locale="uk-UA",
        trace_id="hard-stop",
        guild_id="123456789012345678",
        api_key="AIzaSyTestKey",
        model="gemini-2.5-flash",
        input_type="initial",
        raw_input=["x"],
        existing_environment=None,
        max_enhancer_retries=1,
    )
    await tracker.create_from_dispatch(envelope=env, completion_callback=on_complete)

    if live:
        from bot.modules.services.ai_transport import AiTransport, build_bot_celery_app

        url = build_rabbitmq_broker_url(
            user=user,
            password=password,
            host="127.0.0.1",
            port=5672,
            vhost=vhost,
        )
        celery = build_bot_celery_app(url)
        broker: Any = AiTransport(
            broker_url=url,
            admit_dispatch=lambda: False,
            on_confirmed_dispatch=lambda _p: _noop(),
            on_result=lambda _r: _noop(),
            celery_app=celery,
        )
    else:

        class RecordingBroker:
            def __init__(self) -> None:
                self.purged = 0
                self.revokes: list[str] = []

            async def purge_ai_tasks(self) -> None:
                self.purged += 1

            async def revoke_tasks(self, task_ids: list[str]) -> None:
                self.revokes.extend(task_ids)

        broker = RecordingBroker()

    async def revoke() -> None:
        return None

    async def authorize() -> None:
        return None

    life = LifecycleController(
        node_id="node-local",
        drain_progress_interval_sec=5,
        shutdown_grace_sec=1,
        revoke_gateway=revoke,
        authorize_gateway=authorize,
        tracker=tracker,
        broker=broker,
    )
    await life.on_hard_stop()
    assert terminals == ["worker_terminated"]
    assert tracker.in_flight_workflows == 0
    if not live:
        assert broker.purged == 1
        assert broker.revokes == [str(env.task_id)]


async def _noop(*_a: Any, **_k: Any) -> None:
    return None
