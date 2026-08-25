"""S11 acceptance: /quick-battle success, abort, timeout, and restart expiry."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from bot.modules.commands.battle.models import SETTINGS, SessionPhase
from bot.modules.commands.battle.service.session import QuickBattleService
from bot.modules.services.task_tracker import TaskTracker
from shared.models import AiTaskResultFailed, AiTaskResultSuccess

pytestmark = pytest.mark.acceptance


class FakeClock:
    def __init__(self, value: float = 10.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edited: list[str] = []
        self.mentionable: set[str] = {"u1"}
        self._seq = 0

    async def send(
        self, content: str, *, view: Any = None, mention_user_ids: list[str] | None = None
    ) -> str:
        self._seq += 1
        self.sent.append(content)
        return str(self._seq)

    async def edit(
        self,
        message_id: str,
        content: str,
        *,
        view: Any = None,
        disable: bool = False,
        allowed_mentions: Any = None,
        mention_user_ids: list[str] | None = None,
    ) -> None:
        self.edited.append(content)

    async def send_file(self, *, filename: str, data: bytes, preview: str) -> None:
        self.sent.append(preview)

    def can_mention(self, user_id: str) -> bool:
        return user_id in self.mentionable


class FakeTransport:
    def __init__(self) -> None:
        self.revoked: list[str] = []

    async def revoke_tasks(self, task_ids: list[str]) -> None:
        self.revoked.extend(task_ids)


class FakeHost:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            quickbattle_max_participants=10,
            quickbattle_environment_input_timeout_sec=120,
            quickbattle_ballot_timeout_sec=120,
            quickbattle_fighter_input_timeout_sec=180,
            quickbattle_max_environment_revision_rounds=3,
            quickbattle_ai_admission_timeout_sec=60,
            quickbattle_invoker_cooldown_sec=600,
            quickbattle_guild_cooldown_sec=300,
        )
        self.tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)
        self.transport = FakeTransport()
        self.dispatched: list[Any] = []
        self.callbacks: dict[str, Any] = {}

    async def dispatch(
        self, envelope: Any, completion_callback: Any, *, command: str = "harness"
    ) -> None:
        self.dispatched.append(envelope)
        self.callbacks[str(envelope.task_id)] = completion_callback
        await self.tracker.create_from_dispatch(
            envelope=envelope,
            completion_callback=completion_callback,
            command=command,
        )

    async def acquire_ai_slot(self, holder: str, timeout_sec: float) -> bool:
        return True

    def release_ai_slot(self, holder: str) -> None:
        return None


def _translate(key: str) -> str:
    return key


async def _start(service: QuickBattleService, messenger: FakeMessenger, **kwargs: Any):
    params = {
        "guild_id": "g1",
        "channel_id": "c1",
        "owner_id": "u1",
        "owner_name": "Owner",
        "locale": "en",
        "setting": SETTINGS[-1],
        "custom_environment": False,
        "lobby_timeout_sec": 60,
        "api_key": "k",
        "model": "m",
        "messenger": messenger,
        "translate": _translate,
    }
    params.update(kwargs)
    return await service.start_lobby(**params)


@pytest.mark.asyncio
async def test_s11_a_success_and_delivery() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _start(service, messenger)
    assert host.tracker.in_flight_workflows == 1
    await service.start_from_lobby(session.session_id)
    await service.submit_fighter(
        session.session_id,
        "u1",
        fighter_name="Hero",
        description="A brave duelist from the docks.",
        strategy=None,
    )
    envelope = host.dispatched[-1]
    result = AiTaskResultSuccess(
        task_id=envelope.task_id,
        graph="battle",
        result={"story": "Hero stands alone after the clash.", "winners": ["u1"]},
        completed_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    await service.handle_ai_result(result)
    assert session.phase is SessionPhase.delivered
    assert host.tracker.in_flight_workflows == 0
    assert any("Hero stands alone" in item for item in messenger.sent)


@pytest.mark.asyncio
async def test_s11_b_owner_abort_revokes() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _start(service, messenger, custom_environment=True)
    await service.start_from_lobby(session.session_id)
    await service.submit_environment(session.session_id, "u1", "A lantern-lit canal market.")
    assert host.dispatched
    await service.abort(session.session_id, "u1")
    assert session.phase is SessionPhase.aborted
    assert host.transport.revoked
    assert host.tracker.in_flight_workflows == 0


@pytest.mark.asyncio
async def test_s11_c_missing_ballot_aborts() -> None:
    host = FakeHost()
    clock = FakeClock()
    service = QuickBattleService(application=host, clock=clock)
    messenger = FakeMessenger()
    session = await _start(service, messenger, custom_environment=True)
    service.join(session.session_id, "u2", "Two")
    await service.start_from_lobby(session.session_id)
    await service.submit_environment(session.session_id, "u1", "A wind-carved mesa at dusk.")
    await service.submit_environment(session.session_id, "u2", "A flooded marble forum.")
    envelope = host.dispatched[-1]
    await service.handle_ai_result(
        AiTaskResultSuccess(
            task_id=envelope.task_id,
            graph="environment",
            result={
                "final_environment": {
                    "description": "A wind-carved mesa at dusk.",
                    "tags": ["custom"],
                    "setting": SETTINGS[-1],
                }
            },
            completed_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
    )
    await service.vote(session.session_id, "u1", approve=True)
    clock.value += 200
    await service.process_timeouts(now=clock.value)
    assert session.phase is SessionPhase.timed_out
    assert host.tracker.in_flight_workflows == 0


@pytest.mark.asyncio
async def test_s11_d_local_timeout_discards_late_result() -> None:
    host = FakeHost()
    clock = FakeClock()
    service = QuickBattleService(application=host, clock=clock)
    messenger = FakeMessenger()
    session = await _start(service, messenger)
    await service.start_from_lobby(session.session_id)
    await service.submit_fighter(
        session.session_id,
        "u1",
        fighter_name="Hero",
        description="A brave duelist from the docks.",
        strategy=None,
    )
    envelope = host.dispatched[-1]
    await service.handle_ai_result(
        AiTaskResultFailed(
            task_id=envelope.task_id,
            graph="battle",
            node="bot_task_timeout",
            reason="overall timeout",
            completed_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
    )
    assert session.phase is SessionPhase.task_timeout
    assert host.transport.revoked == []
    before = list(messenger.sent)
    await service.handle_ai_result(
        AiTaskResultSuccess(
            task_id=envelope.task_id,
            graph="battle",
            result={"story": "Late story should be discarded.", "winners": ["u1"]},
            completed_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
    )
    assert "Late story should be discarded." not in "\n".join(messenger.sent)
    assert messenger.sent == before


@pytest.mark.asyncio
async def test_s11_e_restart_expiry() -> None:
    host = FakeHost()
    first = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _start(first, messenger)
    stale_id = session.session_id
    restarted = QuickBattleService(application=FakeHost(), clock=FakeClock())
    assert restarted.sessions == {}
    with pytest.raises(PermissionError):
        restarted.join(stale_id, "u2", "Two")


@pytest.mark.asyncio
async def test_s11_f_solo_empty_winners() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _start(service, messenger)
    await service.start_from_lobby(session.session_id)
    await service.submit_fighter(
        session.session_id,
        "u1",
        fighter_name="Hero",
        description="A brave duelist from the docks.",
        strategy=None,
    )
    envelope = host.dispatched[-1]
    await service.handle_ai_result(
        AiTaskResultSuccess(
            task_id=envelope.task_id,
            graph="battle",
            result={"story": "The solo fighter falls.", "winners": []},
            completed_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
    )
    assert session.phase is SessionPhase.delivered
    assert any(
        "no_victor" in item or "No victor" in item or "commands.quick-battle.no_victor" in item
        for item in messenger.sent
    )


@pytest.mark.asyncio
async def test_s11_g_unavailable_winner_uses_escaped_name() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    messenger.mentionable.clear()
    session = await _start(service, messenger)
    await service.start_from_lobby(session.session_id)
    await service.submit_fighter(
        session.session_id,
        "u1",
        fighter_name="Hero",
        description="A brave duelist from the docks.",
        strategy=None,
    )
    envelope = host.dispatched[-1]
    await service.handle_ai_result(
        AiTaskResultSuccess(
            task_id=envelope.task_id,
            graph="battle",
            result={"story": "Hero wins in absentia.", "winners": ["u1"]},
            completed_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
    )
    joined = "\n".join(messenger.sent)
    assert "<@u1>" not in joined


@pytest.mark.asyncio
async def test_s11_h_revision_exhaustion() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _start(service, messenger, custom_environment=True)
    await service.start_from_lobby(session.session_id)
    await service.submit_environment(session.session_id, "u1", "A quiet orchard in fog.")
    for _ in range(4):
        envelope = host.dispatched[-1]
        await service.handle_ai_result(
            AiTaskResultSuccess(
                task_id=envelope.task_id,
                graph="environment",
                result={
                    "final_environment": {
                        "description": "Candidate arena.",
                        "tags": ["custom"],
                        "setting": SETTINGS[-1],
                    }
                },
                completed_at=datetime(2026, 8, 23, tzinfo=UTC),
            )
        )
        if session.phase is SessionPhase.environment_ballot:
            await service.vote(
                session.session_id, "u1", approve=False, comment="Make it darker and stranger."
            )
    assert session.phase is SessionPhase.aborted
    assert host.tracker.in_flight_workflows == 0
