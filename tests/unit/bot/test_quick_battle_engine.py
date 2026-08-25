"""Quick-battle engine tests: admission, workflow accounting, and roster rules."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bot.modules.commands.battle.models import SETTINGS, AdmissionError, SessionPhase
from bot.modules.commands.battle.service.session import QuickBattleService
from bot.modules.services.task_tracker import TaskTracker


class FakeClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edited: list[tuple[str, str]] = []
        self.files: list[str] = []
        self.mentionable: set[str] = set()
        self.last_view: Any = None
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
        self.last_view = view
        self.edited.append((message_id, content))

    async def send_file(self, *, filename: str, data: bytes, preview: str) -> None:
        self.files.append(filename)

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
        self.slot_ok = True
        self.holder: str | None = None
        self.fail_dispatch: BaseException | None = None

    async def dispatch(
        self, envelope: Any, completion_callback: Any, *, command: str = "harness"
    ) -> None:
        if self.fail_dispatch is not None:
            raise self.fail_dispatch
        self.dispatched.append((envelope, command))
        await self.tracker.create_from_dispatch(
            envelope=envelope,
            completion_callback=completion_callback,
            command=command,
        )

    async def acquire_ai_slot(self, holder: str, timeout_sec: float) -> bool:
        if not self.slot_ok:
            return False
        self.holder = holder
        return True

    def release_ai_slot(self, holder: str) -> None:
        if self.holder == holder:
            self.holder = None


def _translate(key: str) -> str:
    return key


async def _open_lobby(
    service: QuickBattleService,
    messenger: FakeMessenger,
    *,
    guild_id: str = "g1",
    owner_id: str = "u1",
    custom_environment: bool = False,
) -> Any:
    return await service.start_lobby(
        guild_id=guild_id,
        channel_id="c1",
        owner_id=owner_id,
        owner_name="Owner",
        locale="en",
        setting=SETTINGS[-1],
        custom_environment=custom_environment,
        lobby_timeout_sec=60,
        api_key="key",
        model="model",
        messenger=messenger,
        translate=_translate,
    )


@pytest.mark.asyncio
async def test_admit_rejects_busy_guild_and_does_not_consume_cooldown() -> None:
    host = FakeHost()
    clock = FakeClock()
    service = QuickBattleService(application=host, clock=clock)
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger)
    assert host.tracker.in_flight_workflows == 1
    with pytest.raises(PermissionError) as exc:
        await _open_lobby(service, messenger, owner_id="u2")
    assert str(exc.value) == AdmissionError.guild_busy.value
    await service.abort(session.session_id, "u1")
    assert host.tracker.in_flight_workflows == 0
    clock.value += 1
    denied = service.admit(guild_id="g1", user_id="u2", now=clock.value)
    assert denied is AdmissionError.guild_cooldown
    assert (
        service.admit(guild_id="g1", user_id="u1", now=clock.value)
        is AdmissionError.invoker_cooldown
    )


@pytest.mark.asyncio
async def test_nested_tasks_count_as_one_workflow() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger, custom_environment=True)
    assert host.tracker.in_flight_workflows == 1
    service.join(session.session_id, "u2", "Two")
    await service.start_from_lobby(session.session_id)
    await service.submit_environment(session.session_id, "u1", "A misty courtyard.")
    await service.submit_environment(session.session_id, "u2", "A stone coliseum.")
    assert len(host.dispatched) == 1
    assert host.dispatched[0][1] == "quick-battle"
    assert host.tracker.in_flight_workflows == 1
    await service.abort(session.session_id, "u1")
    assert host.tracker.in_flight_workflows == 0


@pytest.mark.asyncio
async def test_ai_slot_timeout_does_not_publish() -> None:
    host = FakeHost()
    host.slot_ok = False
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger, custom_environment=True)
    await service.start_from_lobby(session.session_id)
    await service.submit_environment(session.session_id, "u1", "A quiet glade with lanterns.")
    assert host.dispatched == []
    assert session.phase is SessionPhase.timed_out
    assert host.tracker.in_flight_workflows == 0


@pytest.mark.asyncio
async def test_lobby_caps_at_ten_and_transfers_owner() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger)
    for index in range(2, 11):
        service.join(session.session_id, f"u{index}", f"P{index}")
    assert len(session.participants) == 10
    with pytest.raises(PermissionError):
        service.join(session.session_id, "u11", "Overflow")
    service.leave(session.session_id, "u1")
    assert session.owner_id == "u2"
    assert "u1" not in session.participants


@pytest.mark.asyncio
async def test_generic_arena_skips_environment_graph() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger, custom_environment=False)
    await service.start_from_lobby(session.session_id)
    assert session.phase is SessionPhase.fighter_collect
    assert host.dispatched == []
    environment = session.current_environment or {}
    tags = environment["tags"] if isinstance(environment, dict) else environment.tags
    assert tags[0] == "generic"
    assert messenger.sent


@pytest.mark.asyncio
async def test_environment_dispatch_payload_and_late_discard() -> None:
    from datetime import UTC, datetime

    from shared.models.ai_task import AiTaskResultSuccess

    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger, custom_environment=True)
    await service.start_from_lobby(session.session_id)
    await service.submit_environment(session.session_id, "u1", "A lantern-lit canal market.")
    envelope = host.dispatched[0][0]
    assert envelope.graph == "environment"
    assert envelope.input_type == "initial"
    assert envelope.raw_input == ["A lantern-lit canal market."]
    await service.abort(session.session_id, "u1")
    before = list(messenger.sent)
    await service.handle_ai_result(
        AiTaskResultSuccess(
            task_id=envelope.task_id,
            graph="environment",
            result={
                "final_environment": {
                    "description": "late",
                    "tags": ["custom"],
                    "setting": SETTINGS[-1],
                }
            },
            completed_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
    )
    assert messenger.sent == before


@pytest.mark.asyncio
async def test_hard_stop_lobby_without_task_disables_ui() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger)
    session.active_message_id = "9"
    session.lobby_message_id = "9"
    await service.hard_stop_all()
    assert session.phase is SessionPhase.hard_stopped
    assert host.tracker.in_flight_workflows == 0
    assert messenger.edited


@pytest.mark.asyncio
async def test_unavailable_submitted_fighter_stays_in_roster() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger)
    service.join(session.session_id, "u2", "Two")
    await service.start_from_lobby(session.session_id)
    await service.submit_fighter(
        session.session_id,
        "u1",
        fighter_name="Hero",
        description="A brave duelist from the docks.",
        strategy=None,
    )
    await service.mark_unavailable(session.session_id, "u2")
    ids = [item.user_id for item in (session.frozen_roster or ())]
    assert ids == ["u1", "u2"] or set(ids) == {"u1", "u2"}
    assert "u2" in session.roster_ids()
    assert session.owner_id == "u1"


@pytest.mark.asyncio
async def test_archive_failure_does_not_rollback_delivery() -> None:
    from datetime import UTC, datetime

    from shared.models.ai_task import AiTaskResultSuccess

    host = FakeHost()

    async def boom(**kwargs: Any) -> None:
        raise RuntimeError("archive down")

    service = QuickBattleService(application=host, clock=FakeClock(), archive_writer=boom)
    messenger = FakeMessenger()
    messenger.mentionable.add("u1")
    session = await _open_lobby(service, messenger)
    await service.start_from_lobby(session.session_id)
    await service.submit_fighter(
        session.session_id,
        "u1",
        fighter_name="Hero",
        description="A brave duelist from the docks.",
        strategy=None,
    )
    envelope = host.dispatched[-1][0]
    assert envelope.graph == "battle"
    assert envelope.random_winner_mode is False
    await service.handle_ai_result(
        AiTaskResultSuccess(
            task_id=envelope.task_id,
            graph="battle",
            result={"story": "Hero stands alone after the clash.", "winners": ["u1"]},
            completed_at=datetime(2026, 8, 23, tzinfo=UTC),
        )
    )
    assert session.phase is SessionPhase.delivered
    assert any("Hero stands alone" in item for item in messenger.sent)


@pytest.mark.asyncio
async def test_failed_battle_result_clears_busy_state() -> None:
    from datetime import UTC, datetime

    from shared.models.ai_task import AiTaskResultFailed

    host = FakeHost()
    clock = FakeClock()
    service = QuickBattleService(application=host, clock=clock)
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger, custom_environment=False)
    await service.start_from_lobby(session.session_id)
    await service.submit_fighter(
        session.session_id,
        "u1",
        fighter_name="Hero",
        description="A brave duelist from the docks.",
        strategy=None,
    )
    session.active_message_id = "1"
    assert session.phase is SessionPhase.battle_progress
    assert session.phase_deadline is None
    clock.value += 200
    await service.process_timeouts()
    assert session.phase is SessionPhase.battle_progress
    await service.handle_ai_result(
        AiTaskResultFailed(
            task_id=str(session.expected_task_id),
            graph="battle",
            node="ImplementFirstEpisode",
            reason="episode index does not match request",
            completed_at=datetime(2026, 8, 24, tzinfo=UTC),
        )
    )
    assert session.phase is SessionPhase.failed
    assert session.session_id not in service.sessions
    assert (
        service.admit(guild_id="g1", user_id="u1", now=clock.value)
        is AdmissionError.invoker_cooldown
    )
    assert (
        service.admit(guild_id="g1", user_id="u2", now=clock.value) is AdmissionError.guild_cooldown
    )
    assert messenger.edited[-1][1] == "commands.quick-battle.generation_failed"


@pytest.mark.asyncio
async def test_dispatch_failure_terminates_session_and_releases_slot() -> None:
    host = FakeHost()
    host.fail_dispatch = ConnectionError("publish confirm failed")
    clock = FakeClock()
    service = QuickBattleService(application=host, clock=clock)
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger, custom_environment=False)
    await service.start_from_lobby(session.session_id)
    session.active_message_id = "1"
    with pytest.raises(PermissionError) as exc:
        await service.submit_fighter(
            session.session_id,
            "u1",
            fighter_name="Hero",
            description="A brave duelist from the docks.",
            strategy=None,
        )
    assert str(exc.value) == "errors.error_transient"
    assert session.phase is SessionPhase.dispatch_failed
    assert host.holder is None
    assert host.dispatched == []
    assert session.session_id not in service.sessions
    assert (
        service.admit(guild_id="g1", user_id="u2", now=clock.value) is AdmissionError.guild_cooldown
    )
    assert messenger.edited[-1][1] == "commands.quick-battle.dispatch_failed"


@pytest.mark.asyncio
async def test_task_progress_edits_checklist() -> None:
    from shared.models import TaskPhase

    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger, custom_environment=False)
    await service.start_from_lobby(session.session_id)
    await service.submit_fighter(
        session.session_id,
        "u1",
        fighter_name="Hero",
        description="A brave duelist from the docks.",
        strategy=None,
    )
    assert session.progress_message_id is not None
    await service.handle_task_progress(str(session.expected_task_id), TaskPhase.launching)
    assert messenger.edited[-1][0] == session.progress_message_id
    assert session.phase is SessionPhase.battle_progress
    from bot.modules.commands.battle.UI.views import TaskProgressContainer

    assert isinstance(messenger.last_view, TaskProgressContainer)


@pytest.mark.asyncio
async def test_progress_container_is_posted_before_ai_dispatch() -> None:
    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    sent_at_dispatch: list[int] = []
    original = host.dispatch

    async def wrapped(envelope: Any, completion_callback: Any, *, command: str = "harness") -> None:
        sent_at_dispatch.append(len(messenger.sent))
        await original(envelope, completion_callback, command=command)

    host.dispatch = wrapped  # type: ignore[method-assign]
    session = await _open_lobby(service, messenger, custom_environment=False)
    await service.start_from_lobby(session.session_id)
    await service.submit_fighter(
        session.session_id,
        "u1",
        fighter_name="Hero",
        description="A brave duelist from the docks.",
        strategy=None,
    )
    assert sent_at_dispatch
    assert messenger.sent[sent_at_dispatch[0] - 1] == "commands.quick-battle.phase_queued"
    assert session.progress_message_id is not None


@pytest.mark.asyncio
async def test_progress_ticks_ignored_until_checklist_posted() -> None:
    from shared.models import TaskPhase

    host = FakeHost()
    service = QuickBattleService(application=host, clock=FakeClock())
    messenger = FakeMessenger()
    session = await _open_lobby(service, messenger, custom_environment=False)
    await service.start_from_lobby(session.session_id)
    session.phase = SessionPhase.battle_progress
    session.expected_task_id = "08b2f245-8955-4834-966a-7629247fee0c"
    edited_before = len(messenger.edited)
    await service.handle_task_progress(session.expected_task_id, TaskPhase.launching)
    assert len(messenger.edited) == edited_before


def test_normalize_user_text_rejects_mentions_and_bounds() -> None:
    from bot.modules.commands.battle.models import normalize_user_text

    assert normalize_user_text("  Hello  ", min_length=1, max_length=80) == "Hello"
    with pytest.raises(ValueError):
        normalize_user_text("@everyone", min_length=1, max_length=80)
    with pytest.raises(ValueError):
        normalize_user_text("x" * 81, min_length=1, max_length=80)
