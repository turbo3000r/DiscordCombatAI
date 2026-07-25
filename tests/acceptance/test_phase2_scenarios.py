"""Phase 2 acceptance — hermetic Bot/Worker transport and fencing coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from helpers_phase2 import (
    PHASE2_SCENARIO_OWNERSHIP,
    FakeClock,
    desired_state_bytes,
    grant_bytes,
    make_environment_envelope,
    ownership_labels,
)

from ai_worker.tasks import TRANSPORT_SHELL_RESULT, run_graph_impl
from bot.modules.events.control_events import GRANT_TOPIC, BotControlState
from bot.modules.services.ai_transport import AiTransport, PendingDispatch
from bot.modules.services.lifecycle import LifecycleController
from bot.modules.services.task_tracker import TaskTracker
from shared.models import (
    AiTaskResultSuccess,
    BotDesiredStateValue,
    TaskPhase,
    TaskProgressMessage,
)
from shared.models.ai_task import EnvironmentState


class FakeCelery:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def send_task(self, name: str, **kwargs: Any) -> None:
        if self.fail:
            raise ConnectionError("confirm failed")
        self.calls.append({"name": name, **kwargs})

    def close(self) -> None:
        return None


class FakeBroker:
    def __init__(self) -> None:
        self.purged = 0
        self.revokes: list[str] = []

    async def purge_ai_tasks(self) -> None:
        self.purged += 1

    async def revoke_tasks(self, task_ids: list[str]) -> None:
        self.revokes.extend(task_ids)


@pytest.mark.acceptance()
def test_phase2_ownership_matrix_is_honest() -> None:
    labels = ownership_labels()
    assert "complete" in labels
    assert "deferred" in labels
    assert "integration-only" in labels
    deferred = [
        f"{scenario}.{step}"
        for scenario, steps in PHASE2_SCENARIO_OWNERSHIP.items()
        for step, status in steps.items()
        if status == "deferred"
    ]
    assert deferred, "expected deferred command/lobby steps to remain labeled"
    assert all(
        status in {"complete", "integration-only", "deferred"}
        for steps in PHASE2_SCENARIO_OWNERSHIP.values()
        for status in steps.values()
    )


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_full_path_grant_dispatch_progress_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_WORKER_TRANSPORT_SHELL", "true")
    clock = FakeClock()
    activated: list[str] = []
    completions: list[Any] = []

    async def on_activate() -> None:
        activated.append("ok")

    control = BotControlState(
        node_id="node-local",
        max_ttl_sec=60,
        control_drain_timeout_sec=45,
        clock=clock,
        on_activate=on_activate,
    )
    await control.on_mqtt_connected()
    await control.handle_message(
        "control/bot/desired_state",
        desired_state_bytes(state=BotDesiredStateValue.inactive),
    )
    assert control.admit_dispatch() is False

    await control.handle_message(GRANT_TOPIC, grant_bytes(seq=1))
    assert activated == ["ok"]
    assert control.admit_dispatch() is True

    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900, clock=clock)
    created: list[str] = []

    async def on_confirmed(pending: PendingDispatch) -> None:
        created.append(pending.task_id)
        await tracker.create_from_dispatch(
            envelope=pending.envelope,
            completion_callback=pending.completion_callback,
        )

    transport = AiTransport(
        broker_url="amqp://u:p@h:5672/%2Fv",
        admit_dispatch=control.admit_dispatch,
        on_confirmed_dispatch=on_confirmed,
        on_result=tracker.handle_result,
        on_progress=tracker.handle_progress,
        celery_app=FakeCelery(),  # type: ignore[arg-type]
    )
    env = make_environment_envelope()

    async def on_complete(result: Any) -> None:
        completions.append(result)

    await transport.dispatch_environment(env, on_complete)
    assert created == [str(env.task_id)]
    assert tracker.in_flight_workflows == 1
    assert tracker.records[str(env.task_id)].current_phase is TaskPhase.queued

    for phase in (
        TaskPhase.launching,
        TaskPhase.composing,
        TaskPhase.refining,
        TaskPhase.finishing,
    ):
        await tracker.handle_progress(
            TaskProgressMessage(
                task_id=env.task_id,
                graph="environment",
                phase=phase,
                timestamp=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
            )
        )
    assert tracker.records[str(env.task_id)].current_phase is TaskPhase.finishing

    from ai_worker.settings import AiWorkerSettings

    settings = AiWorkerSettings()  # type: ignore[call-arg]
    shell = run_graph_impl(
        env.model_dump(mode="json"),
        celery_task_id=str(env.task_id),
        settings=settings,
        progress=None,
        results=_RecordingResults(),
    )
    assert shell["status"] == "success"
    final = EnvironmentState.model_validate(TRANSPORT_SHELL_RESULT["final_environment"])

    result = AiTaskResultSuccess(
        task_id=env.task_id,
        graph="environment",
        result={
            "final_environment": final.model_dump(mode="json"),
            "attempts_used": 0,
            "forced_selection": False,
        },
        completed_at=datetime(2026, 7, 20, 10, 5, tzinfo=UTC),
    )
    await tracker.handle_result(result)
    assert tracker.in_flight_workflows == 0
    assert len(completions) == 1
    assert completions[0].status == "success"
    await tracker.handle_result(result)
    assert len(completions) == 1


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s05_publish_failure_no_task_record_gateway_unchanged() -> None:
    gateway = {"up": True}

    async def authorize() -> None:
        gateway["up"] = True

    async def revoke() -> None:
        gateway["up"] = False

    life = LifecycleController(
        node_id="node-local",
        drain_progress_interval_sec=5,
        shutdown_grace_sec=1,
        revoke_gateway=revoke,
        authorize_gateway=authorize,
    )
    await life.on_activate()

    control = BotControlState(
        node_id="node-local",
        max_ttl_sec=60,
        control_drain_timeout_sec=45,
        clock=FakeClock(),
        on_activate=life.on_activate,
    )
    await control.on_mqtt_connected()
    await control.handle_message(GRANT_TOPIC, grant_bytes())

    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)
    created = {"n": 0}

    async def on_confirmed(_p: PendingDispatch) -> None:
        created["n"] += 1

    transport = AiTransport(
        broker_url="amqp://u:p@h:5672/%2Fv",
        admit_dispatch=lambda: life.accepting_ai_work and control.admit_dispatch(),
        on_confirmed_dispatch=on_confirmed,
        on_result=tracker.handle_result,
        celery_app=FakeCelery(fail=True),  # type: ignore[arg-type]
    )
    with pytest.raises(ConnectionError):
        await transport.dispatch_environment(
            make_environment_envelope(),
            lambda _r: _noop(),
        )
    assert created["n"] == 0
    assert tracker.in_flight_workflows == 0
    assert gateway["up"] is True
    assert control.admit_dispatch() is True


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s03_s04_disconnect_and_expiry_hard_stop() -> None:
    clock = FakeClock()
    hard: list[str] = []

    async def on_hard() -> None:
        hard.append("hard")

    control = BotControlState(
        node_id="node-local",
        max_ttl_sec=60,
        control_drain_timeout_sec=45,
        clock=clock,
        on_activate=_noop,
        on_hard_stop=on_hard,
    )
    await control.on_mqtt_connected()
    await control.handle_message(GRANT_TOPIC, grant_bytes(ttl=45))
    await control.on_mqtt_disconnected()
    assert control.draining is True
    assert control.admit_dispatch() is False
    clock.advance(45)
    await control.tick()
    assert hard == ["hard"]


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s07_s08_drain_and_hard_stop_revoke_matrix() -> None:
    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)
    env = make_environment_envelope()
    terminals: list[str] = []

    async def on_complete(result: Any) -> None:
        terminals.append(result.node)

    await tracker.create_from_dispatch(envelope=env, completion_callback=on_complete)
    broker = FakeBroker()
    mqtt_topics: list[str] = []

    class FakeMqtt:
        async def publish(
            self, topic: str, payload: bytes, *, qos: int, retain: bool
        ) -> None:
            mqtt_topics.append(topic)

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
        mqtt=FakeMqtt(),
        tracker=tracker,
        broker=broker,
        leadership_term_provider=lambda: "cd88086a-fd6d-48d4-8446-39523af2bf70",
    )
    await life.on_soft_stop()
    progress = await life.publish_drain_progress_once()
    assert progress is not None
    assert progress.in_flight_workflows == 1
    assert "status/bot/drain_progress" in mqtt_topics
    assert broker.revokes == []
    await life.on_hard_stop()
    assert broker.purged == 1
    assert broker.revokes == [str(env.task_id)]
    assert terminals == ["worker_terminated"]


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s10_restart_inactive_orphan_discard() -> None:
    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)
    orphan = AiTaskResultSuccess(
        task_id=str(uuid4()),
        graph="environment",
        result={
            "final_environment": {
                "description": "orphan",
                "tags": [],
                "setting": "realistic",
            }
        },
        completed_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    await tracker.handle_result(orphan)
    assert tracker.in_flight_workflows == 0

    control = BotControlState(
        node_id="node-local",
        max_ttl_sec=60,
        control_drain_timeout_sec=45,
        clock=FakeClock(),
    )
    await control.on_mqtt_connected()
    assert control.admit_dispatch() is False


class _RecordingResults:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish_result(self, result: Any) -> None:
        self.published.append(result)


async def _noop(*_a: Any, **_k: Any) -> None:
    return None
