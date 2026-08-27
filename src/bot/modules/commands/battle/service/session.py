"""Quick-battle workflow: lobby, environment, fighters, delivery, and termination."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from bot.modules.commands.battle.models import (
    SETTINGS,
    AdmissionError,
    FighterSubmission,
    Participant,
    Session,
    SessionPhase,
    normalize_user_text,
)
from bot.modules.commands.battle.service.delivery import (
    archive_story,
    escape_display_name,
    split_story,
    validated_winners,
)
from bot.modules.commands.battle.service.generic_arenas import pick_generic_arena
from shared.models import (
    BattleAiTaskEnvelope,
    EnvironmentAiTaskEnvelope,
    EnvironmentState,
    FighterState,
    TaskPhase,
)
from shared.models.localization import to_ai_language_locale

logger = logging.getLogger(__name__)


def _log_extra(session: Session, **more: Any) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "guild_id": session.guild_id,
        "user_id": session.invoker_id,
        "command": "quick-battle",
        "trace_id": session.session_id,
        "session_id": session.session_id,
        "phase": session.phase.value,
    }
    if session.expected_task_id:
        extra["task_id"] = session.expected_task_id
    extra.update({key: value for key, value in more.items() if value is not None})
    return extra


def _passthrough(key: str) -> str:
    return key


STALL_NODES = frozenset({"bot_stall_timeout", "bot_task_timeout"})
PHASE_MARKERS = (
    ("queued", "commands.quick-battle.phase_queued"),
    ("launching", "commands.quick-battle.phase_launching"),
    ("composing", "commands.quick-battle.phase_composing"),
    ("refining", "commands.quick-battle.phase_refining"),
    ("finishing", "commands.quick-battle.phase_finishing"),
)


class BattleHost(Protocol):
    settings: Any
    tracker: Any
    transport: Any

    async def dispatch(
        self, envelope: Any, completion_callback: Any, *, command: str = "harness"
    ) -> None: ...

    async def acquire_ai_slot(self, holder: str, timeout_sec: float) -> bool: ...

    def release_ai_slot(self, holder: str) -> None: ...


class BattleMessenger(Protocol):
    async def send(
        self,
        content: str,
        *,
        view: Any | None = None,
        mention_user_ids: list[str] | None = None,
    ) -> str: ...

    async def edit(
        self,
        message_id: str,
        content: str,
        *,
        view: Any | None = None,
        disable: bool = False,
        allowed_mentions: Any | None = None,
    ) -> None: ...

    async def send_file(self, *, filename: str, data: bytes, preview: str) -> None: ...

    def can_mention(self, user_id: str) -> bool: ...


class QuickBattleService:
    """In-memory /quick-battle orchestrator. One workflow unit per accepted lobby."""

    def __init__(
        self,
        application: BattleHost,
        *,
        clock: Callable[[], float] | None = None,
        archive_writer: Any | None = None,
    ) -> None:
        self._app = application
        self._clock = clock or time.monotonic
        self._archive_writer = archive_writer
        self.sessions: dict[str, Session] = {}
        self._guild_session: dict[str, str] = {}
        self._user_session: dict[str, str] = {}
        self._invoker_cooldown_until: dict[str, float] = {}
        self._guild_cooldown_until: dict[str, float] = {}
        self._messengers: dict[str, BattleMessenger] = {}
        self._translators: dict[str, Callable[[str], str]] = {}
        self.revoked_task_ids: list[str] = []

    def _now(self) -> float:
        return float(self._clock())

    def settings(self) -> Any:
        return self._app.settings

    def admit(
        self, *, guild_id: str, user_id: str, now: float | None = None
    ) -> AdmissionError | None:
        moment = self._now() if now is None else now
        if guild_id in self._guild_session:
            return AdmissionError.guild_busy
        if user_id in self._user_session:
            return AdmissionError.user_busy
        if self._invoker_cooldown_until.get(user_id, 0) > moment:
            return AdmissionError.invoker_cooldown
        if self._guild_cooldown_until.get(guild_id, 0) > moment:
            return AdmissionError.guild_cooldown
        return None

    async def start_lobby(
        self,
        *,
        guild_id: str,
        channel_id: str,
        owner_id: str,
        owner_name: str,
        locale: str,
        setting: str,
        custom_environment: bool,
        lobby_timeout_sec: int,
        api_key: str,
        model: str,
        messenger: BattleMessenger,
        translate: Callable[[str], str],
        now: float | None = None,
    ) -> Session:
        if setting not in SETTINGS:
            raise ValueError("unsupported setting")
        moment = self._now() if now is None else now
        denied = self.admit(guild_id=guild_id, user_id=owner_id, now=moment)
        if denied is not None:
            raise PermissionError(denied.value)
        session_id = str(uuid4())
        session = Session(
            session_id=session_id,
            guild_id=guild_id,
            channel_id=channel_id,
            owner_id=owner_id,
            invoker_id=owner_id,
            locale=locale,
            setting=setting,
            custom_environment=custom_environment,
            lobby_timeout_sec=lobby_timeout_sec,
            workflow_id=session_id,
            api_key=api_key,
            model=model,
            created_at=moment,
            phase_deadline=moment + lobby_timeout_sec,
        )
        session.participants[owner_id] = Participant(
            user_id=owner_id, display_name=owner_name, joined_at=moment
        )
        self.sessions[session_id] = session
        self._guild_session[guild_id] = session_id
        self._user_session[owner_id] = session_id
        self._messengers[session_id] = messenger
        self._translators[session_id] = translate
        self._app.tracker.start_workflow(session.workflow_id)
        self._invoker_cooldown_until[owner_id] = moment + int(
            self.settings().quickbattle_invoker_cooldown_sec
        )
        logger.info(
            "quick-battle lobby started setting=%s custom_environment=%s timeout=%s",
            setting,
            custom_environment,
            lobby_timeout_sec,
            extra=_log_extra(session, participant_count=1),
        )
        return session

    def join(self, session_id: str, user_id: str, display_name: str) -> Session:
        session = self._require_live(session_id)
        if session.phase is not SessionPhase.lobby:
            raise PermissionError("lobby_closed")
        if user_id in self._user_session and self._user_session[user_id] != session_id:
            raise PermissionError(AdmissionError.user_busy.value)
        maximum = int(self.settings().quickbattle_max_participants)
        if user_id not in session.participants and len(session.participants) >= maximum:
            raise PermissionError("lobby_full")
        session.participants[user_id] = Participant(
            user_id=user_id, display_name=display_name, joined_at=self._now()
        )
        self._user_session[user_id] = session_id
        return session

    def leave(self, session_id: str, user_id: str) -> Session:
        session = self._require_live(session_id)
        if session.phase is not SessionPhase.lobby:
            raise PermissionError("lobby_closed")
        if user_id == session.owner_id:
            remaining = [item for item in session.participants.values() if item.user_id != user_id]
            if not remaining:
                raise PermissionError("owner_cannot_leave")
            remaining.sort(key=lambda item: item.joined_at)
            session.owner_id = remaining[0].user_id
        session.participants.pop(user_id, None)
        self._user_session.pop(user_id, None)
        return session

    async def start_from_lobby(self, session_id: str, user_id: str | None = None) -> Session:
        session = self._require_live(session_id)
        if session.phase is not SessionPhase.lobby:
            raise PermissionError("lobby_closed")
        if user_id is not None and user_id != session.owner_id:
            raise PermissionError("not_owner")
        session.frozen_roster = tuple(
            sorted(session.participants.values(), key=lambda item: item.joined_at)
        )
        if session.custom_environment:
            session.phase = SessionPhase.environment_collect
            session.phase_deadline = self._now() + int(
                self.settings().quickbattle_environment_input_timeout_sec
            )
            logger.info(
                "quick-battle lobby started environment collection participant_count=%s",
                len(session.frozen_roster),
                extra=_log_extra(session, participant_count=len(session.frozen_roster)),
            )
            await self._publish_phase(session)
            return session
        session.current_environment = pick_generic_arena(
            setting=session.setting, index=random.randrange(2)
        )
        logger.info(
            "quick-battle lobby started fighter collection participant_count=%s",
            len(session.frozen_roster),
            extra=_log_extra(session, participant_count=len(session.frozen_roster)),
        )
        await self._begin_fighter_collect(session)
        return session

    async def abort(self, session_id: str, user_id: str | None = None) -> Session:
        session = self._require_live(session_id)
        if user_id is not None and user_id != session.owner_id:
            raise PermissionError("not_owner")
        if session.expected_task_id and session.revoke_on_cancel:
            await self._revoke(session.expected_task_id)
        return await self._terminate(session, SessionPhase.aborted)

    async def hard_stop_all(self) -> None:
        for session in list(self.sessions.values()):
            if session.terminal:
                continue
            if session.expected_task_id:
                await self._revoke(session.expected_task_id)
            await self._terminate(session, SessionPhase.hard_stopped)

    async def submit_environment(self, session_id: str, user_id: str, text: str) -> Session:
        session = self._require_live(session_id)
        if session.phase is not SessionPhase.environment_collect:
            raise PermissionError("wrong_phase")
        if user_id not in session.roster_ids():
            raise PermissionError("not_participant")
        session.environment_texts[user_id] = normalize_user_text(text, min_length=1, max_length=500)
        if set(session.environment_texts) >= set(session.roster_ids()):
            logger.info(
                "quick-battle environment submissions complete count=%s",
                len(session.environment_texts),
                extra=_log_extra(session, participant_count=len(session.roster_ids())),
            )
            await self._dispatch_environment(session)
        return session

    async def submit_fighter(
        self,
        session_id: str,
        user_id: str,
        *,
        fighter_name: str,
        description: str,
        strategy: str | None,
    ) -> Session:
        session = self._require_live(session_id)
        if session.phase is not SessionPhase.fighter_collect:
            raise PermissionError("wrong_phase")
        if user_id not in session.roster_ids():
            raise PermissionError("not_participant")
        session.fighter_submissions[user_id] = FighterSubmission(
            user_id=user_id,
            fighter_name=normalize_user_text(fighter_name, min_length=1, max_length=80),
            description=normalize_user_text(description, min_length=1, max_length=1000),
            strategy=(
                None
                if not (strategy or "").strip()
                else normalize_user_text(strategy or "", min_length=1, max_length=500)
            ),
        )
        if set(session.fighter_submissions) >= set(session.roster_ids()):
            logger.info(
                "quick-battle fighter submissions complete count=%s",
                len(session.fighter_submissions),
                extra=_log_extra(session, participant_count=len(session.roster_ids())),
            )
            await self._dispatch_battle(session)
        return session

    async def vote(
        self, session_id: str, user_id: str, *, approve: bool, comment: str | None = None
    ) -> Session:
        session = self._require_live(session_id)
        if session.phase is not SessionPhase.environment_ballot:
            raise PermissionError("wrong_phase")
        if user_id not in session.roster_ids():
            raise PermissionError("not_participant")
        if approve:
            session.approvals[user_id] = True
            session.decline_comments.pop(user_id, None)
        else:
            session.approvals[user_id] = False
            session.decline_comments[user_id] = normalize_user_text(
                comment or "", min_length=1, max_length=300
            )
        if set(session.approvals) >= set(session.roster_ids()):
            await self._resolve_ballot(session)
        return session

    async def handle_task_progress(self, task_id: str, phase: Any) -> None:
        session = next(
            (item for item in self.sessions.values() if item.expected_task_id == task_id),
            None,
        )
        if session is None or session.phase not in {
            SessionPhase.environment_progress,
            SessionPhase.battle_progress,
        }:
            return
        from bot.modules.commands.battle.UI.views import TaskProgressContainer

        current = phase if isinstance(phase, TaskPhase) else TaskPhase(str(phase))
        logger.info(
            "quick-battle progress phase=%s",
            current.value,
            extra=_log_extra(
                session,
                task_id=task_id,
                graph=session.expected_graph,
                phase=current.value,
            ),
        )
        messenger = self._messengers.get(session.session_id)
        translate: Callable[[str], str] = self._translators.get(session.session_id, _passthrough)
        if messenger is None or not session.progress_message_id:
            return
        view = TaskProgressContainer(
            service=self,
            session_id=session.session_id,
            translate=translate,
            timeout=None,
            current_phase=current,
        )
        try:
            await messenger.edit(
                session.progress_message_id,
                translate("commands.quick-battle.phase_queued"),
                view=view,
            )
        except Exception:
            logger.exception(
                "quick-battle progress edit failed",
                extra=_log_extra(session, task_id=task_id),
            )

    async def handle_ai_result(self, result: Any) -> None:
        task_id = str(getattr(result, "task_id", ""))
        graph = str(getattr(result, "graph", ""))
        session = next(
            (item for item in self.sessions.values() if item.expected_task_id == task_id),
            None,
        )
        if session is None or session.expected_graph != graph:
            return
        if getattr(result, "status", None) != "success":
            node = str(getattr(result, "node", "") or "")
            session.revoke_on_cancel = node not in STALL_NODES
            if session.revoke_on_cancel and session.expected_task_id:
                await self._revoke(session.expected_task_id)
            phase = SessionPhase.task_timeout if node in STALL_NODES else SessionPhase.failed
            await self._terminate(session, phase)
            return
        payload = getattr(result, "result", None) or {}
        if graph == "environment":
            session.current_environment = (
                payload.get("final_environment") or payload.get("environment") or payload
            )
            session.phase = SessionPhase.environment_ballot
            session.approvals.clear()
            session.decline_comments.clear()
            session.phase_deadline = self._now() + int(
                self.settings().quickbattle_ballot_timeout_sec
            )
            session.expected_task_id = None
            self._release_slot(session)
            await self._publish_phase(session)
            return
        await self._deliver_battle(session, payload, result)

    async def process_timeouts(self, now: float | None = None) -> None:
        moment = self._now() if now is None else now
        for session in list(self.sessions.values()):
            if (
                session.terminal
                or session.phase_deadline is None
                or moment < session.phase_deadline
            ):
                continue
            if session.phase is SessionPhase.lobby:
                await self.start_from_lobby(session.session_id)
            elif session.phase is SessionPhase.environment_collect:
                await self._shrink_or_abort(session, session.environment_texts)
                if not session.terminal and set(session.environment_texts) >= set(
                    session.roster_ids()
                ):
                    await self._dispatch_environment(session)
            elif session.phase is SessionPhase.fighter_collect:
                await self._shrink_or_abort(session, session.fighter_submissions)
                if not session.terminal and set(session.fighter_submissions) >= set(
                    session.roster_ids()
                ):
                    await self._dispatch_battle(session)
            elif session.phase is SessionPhase.environment_ballot:
                await self._terminate(session, SessionPhase.timed_out)
            elif session.phase in {SessionPhase.environment_progress, SessionPhase.battle_progress}:
                session.revoke_on_cancel = False
                await self._terminate(session, SessionPhase.timed_out)

    async def mark_unavailable(self, session_id: str, user_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None or session.frozen_roster is None or session.terminal:
            return
        session.frozen_roster = tuple(
            Participant(
                user_id=item.user_id,
                display_name=item.display_name,
                joined_at=item.joined_at,
                available=False if item.user_id == user_id else item.available,
            )
            for item in session.frozen_roster
        )
        if session.owner_id != user_id:
            return
        remaining = [item for item in session.frozen_roster if item.available]
        if not remaining:
            await self.abort(session_id)
            return
        remaining.sort(key=lambda item: item.joined_at)
        session.owner_id = remaining[0].user_id

    async def _publish_phase(self, session: Session) -> None:
        messenger = self._messengers.get(session.session_id)
        translate: Callable[[str], str] = self._translators.get(session.session_id, _passthrough)
        if messenger is None:
            return
        from bot.modules.commands.battle.UI.views import (
            EnvironmentApprovalView,
            SequentialCollectorView,
            TaskProgressContainer,
        )

        view: Any | None = None
        content = translate("commands.quick-battle.submit")
        if session.phase is SessionPhase.environment_collect:
            view = SequentialCollectorView(
                service=self,
                session_id=session.session_id,
                translate=translate,
                kind="environment",
                timeout=float(self.settings().quickbattle_environment_input_timeout_sec),
            )
        elif session.phase is SessionPhase.fighter_collect:
            view = SequentialCollectorView(
                service=self,
                session_id=session.session_id,
                translate=translate,
                kind="fighter",
                timeout=float(self.settings().quickbattle_fighter_input_timeout_sec),
            )
        elif session.phase is SessionPhase.environment_ballot:
            payload = session.current_environment or {}
            if hasattr(payload, "description"):
                description = str(payload.description)
            else:
                description = str(payload.get("description", ""))
            chunks, _attachment = split_story(description)
            for chunk in chunks[:-1]:
                await messenger.send(chunk)
            content = chunks[-1] if chunks else translate("commands.quick-battle.approve")
            view = EnvironmentApprovalView(
                service=self,
                session_id=session.session_id,
                translate=translate,
                description=content,
            )
        elif session.phase in {SessionPhase.environment_progress, SessionPhase.battle_progress}:
            content = translate("commands.quick-battle.phase_queued")
            view = TaskProgressContainer(
                service=self,
                session_id=session.session_id,
                translate=translate,
                timeout=None,
                current_phase=TaskPhase.queued,
            )
        else:
            return
        message_id = await messenger.send(content, view=view)
        session.active_message_id = message_id
        if session.phase in {SessionPhase.environment_progress, SessionPhase.battle_progress}:
            session.progress_message_id = message_id
        else:
            session.progress_message_id = None

    async def _publish_task(self, session: Session, envelope: Any) -> None:
        logger.info(
            "quick-battle dispatching graph=%s task_id=%s",
            envelope.graph,
            envelope.task_id,
            extra=_log_extra(
                session,
                graph=envelope.graph,
                task_id=str(envelope.task_id),
                participant_count=len(session.roster_ids()),
            ),
        )
        try:
            await self._app.dispatch(envelope, self.handle_ai_result, command="quick-battle")
        except Exception:
            logger.exception(
                "quick-battle dispatch failed graph=%s task_id=%s",
                envelope.graph,
                envelope.task_id,
                extra=_log_extra(
                    session,
                    graph=envelope.graph,
                    task_id=str(envelope.task_id),
                ),
            )
            await self._terminate(session, SessionPhase.dispatch_failed)
            raise PermissionError("errors.error_transient") from None
        logger.info(
            "quick-battle dispatched graph=%s task_id=%s",
            envelope.graph,
            envelope.task_id,
            extra=_log_extra(
                session,
                graph=envelope.graph,
                task_id=str(envelope.task_id),
            ),
        )

    def _require_live(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None or session.terminal:
            raise PermissionError("session_expired")
        return session

    async def _shrink_or_abort(self, session: Session, submitted: dict[str, Any]) -> None:
        remaining = [item for item in session.roster() if item.user_id in submitted]
        if not remaining:
            await self._terminate(session, SessionPhase.timed_out)
            return
        session.frozen_roster = tuple(remaining)

    async def _begin_fighter_collect(self, session: Session) -> None:
        session.phase = SessionPhase.fighter_collect
        session.phase_deadline = self._now() + int(
            self.settings().quickbattle_fighter_input_timeout_sec
        )
        await self._publish_phase(session)

    def _environment_state(self, session: Session) -> EnvironmentState:
        payload = session.current_environment or {}
        if isinstance(payload, EnvironmentState):
            return payload
        return EnvironmentState.model_validate(
            {
                "description": payload.get("description", "arena"),
                "tags": payload.get("tags", ["generic"]),
                "setting": payload.get("setting", session.setting),
            }
        )

    async def _acquire_slot(self, session: Session) -> None:
        if session.holding_ai_slot:
            return
        acquired = await self._app.acquire_ai_slot(
            session.session_id,
            float(self.settings().quickbattle_ai_admission_timeout_sec),
        )
        if not acquired:
            raise PermissionError(AdmissionError.ai_busy.value)
        session.holding_ai_slot = True

    def _release_slot(self, session: Session) -> None:
        if session.holding_ai_slot:
            self._app.release_ai_slot(session.session_id)
            session.holding_ai_slot = False

    async def _dispatch_environment(self, session: Session) -> None:
        try:
            await self._acquire_slot(session)
        except PermissionError:
            await self._terminate(session, SessionPhase.timed_out)
            return
        if session.environment_attempts == 0:
            input_type: Literal["initial", "revision"] = "initial"
            existing = None
            raw_input = [session.environment_texts[user_id] for user_id in session.roster_ids()]
        else:
            input_type = "revision"
            existing = self._environment_state(session)
            raw_input = [
                session.decline_comments[user_id]
                for user_id in session.roster_ids()
                if user_id in session.decline_comments
            ]
        session.phase = SessionPhase.environment_progress
        session.phase_deadline = None
        session.revoke_on_cancel = True
        envelope = EnvironmentAiTaskEnvelope(
            task_id=str(uuid4()),
            created_at=datetime.now(tz=UTC),
            setting=session.setting,
            language_locale=to_ai_language_locale(session.locale),
            trace_id=session.session_id,
            guild_id=session.guild_id,
            api_key=session.api_key,
            model=session.model,
            input_type=input_type,
            raw_input=raw_input,
            existing_environment=existing,
            max_enhancer_retries=3,
        )
        session.expected_task_id = str(envelope.task_id)
        session.expected_graph = "environment"
        session.expected_revision = session.environment_attempts
        session.environment_attempts += 1
        await self._publish_phase(session)
        await self._publish_task(session, envelope)

    async def _resolve_ballot(self, session: Session) -> None:
        approvals = sum(1 for value in session.approvals.values() if value)
        required = session.required_approvals()
        logger.info(
            "quick-battle ballot complete approvals=%s required=%s",
            approvals,
            required,
            extra=_log_extra(
                session,
                participant_count=len(session.roster_ids()),
                approval_round=session.environment_attempts,
                approve_ratio=f"{approvals}/{len(session.roster_ids())}",
            ),
        )
        if approvals >= required:
            await self._begin_fighter_collect(session)
            return
        maximum = int(self.settings().quickbattle_max_environment_revision_rounds)
        if session.environment_attempts > maximum:
            await self._terminate(session, SessionPhase.aborted)
            return
        await self._dispatch_environment(session)

    async def _dispatch_battle(self, session: Session) -> None:
        try:
            await self._acquire_slot(session)
        except PermissionError:
            await self._terminate(session, SessionPhase.timed_out)
            return
        session.phase = SessionPhase.battle_progress
        session.phase_deadline = None
        session.revoke_on_cancel = True
        fighters = [
            FighterState(
                player_id=item.user_id,
                player_nick=item.display_name,
                fighter_name=session.fighter_submissions[item.user_id].fighter_name,
                description=session.fighter_submissions[item.user_id].description,
                strategy=session.fighter_submissions[item.user_id].strategy,
            )
            for item in session.roster()
        ]
        envelope = BattleAiTaskEnvelope(
            task_id=str(uuid4()),
            created_at=datetime.now(tz=UTC),
            setting=session.setting,
            language_locale=to_ai_language_locale(session.locale),
            trace_id=session.session_id,
            guild_id=session.guild_id,
            api_key=session.api_key,
            model=session.model,
            fighters=fighters,
            environment=self._environment_state(session),
            random_winner_mode=False,
            max_modifier_retries=3,
        )
        session.expected_task_id = str(envelope.task_id)
        session.expected_graph = "battle"
        await self._publish_phase(session)
        await self._publish_task(session, envelope)

    async def _deliver_battle(self, session: Session, payload: dict[str, Any], result: Any) -> None:
        story = str(payload.get("story") or "")
        roster_ids = {item.user_id for item in (session.frozen_roster or session.roster())}
        raw_winners = payload.get("winners")
        winners = validated_winners(raw_winners, roster_ids)
        if isinstance(raw_winners, list) and raw_winners and not winners:
            logger.warning(
                "quick-battle winners not in roster count=%s",
                len(raw_winners),
                extra=_log_extra(session),
            )
        session.last_story = story
        session.last_winners = winners
        chunks, attachment = split_story(story)
        messenger = self._messengers.get(session.session_id)
        translate = self._translators.get(session.session_id, lambda key: key)
        if messenger is not None:
            if attachment is None:
                for chunk in chunks:
                    await messenger.send(chunk)
            else:
                await messenger.send_file(
                    filename="quick-battle.txt",
                    data=attachment,
                    preview=chunks[0] if chunks else "",
                )
            mention_ids = [winner for winner in winners if messenger.can_mention(winner)]
            await messenger.send(
                self._winner_line(session, winners, translate),
                mention_user_ids=mention_ids or None,
            )
        await archive_story(
            self._archive_writer,
            guild_id=session.guild_id,
            task_id=str(getattr(result, "task_id", session.expected_task_id or "")),
            created_at=datetime.now(tz=UTC).isoformat(),
            story_text=story,
            winners=winners,
        )
        session.revoke_on_cancel = False
        await self._terminate(session, SessionPhase.delivered)

    def _winner_line(
        self,
        session: Session,
        winners: list[str],
        translate: Callable[[str], str],
    ) -> str:
        if not winners:
            return translate("commands.quick-battle.no_victor")
        lookup = {
            item.user_id: item.display_name for item in (session.frozen_roster or session.roster())
        }
        messenger = self._messengers.get(session.session_id)
        rendered: list[str] = []
        for winner in winners:
            if messenger is not None and messenger.can_mention(winner):
                rendered.append(f"<@{winner}>")
            else:
                rendered.append(escape_display_name(lookup.get(winner, winner)))
        return f"{translate('commands.quick-battle.winners')} {', '.join(rendered)}"

    async def _revoke(self, task_id: str) -> None:
        self.revoked_task_ids.append(task_id)
        transport = getattr(self._app, "transport", None)
        if transport is None:
            return
        try:
            await transport.revoke_tasks([task_id])
        except Exception:
            logger.exception("quick-battle revoke failed")

    async def _terminate(self, session: Session, phase: SessionPhase) -> Session:
        if session.cleanup_done:
            return session
        session.cleanup_done = True
        session.terminal = True
        session.phase = phase
        session.phase_deadline = None
        session.expected_task_id = None
        self._release_slot(session)
        logger.info(
            "quick-battle session terminated phase=%s",
            phase.value,
            extra=_log_extra(session),
        )
        try:
            self._app.tracker.finish_workflow(session.workflow_id)
        except Exception:
            logger.exception("quick-battle workflow finish failed")
        self._guild_cooldown_until[session.guild_id] = self._now() + int(
            self.settings().quickbattle_guild_cooldown_sec
        )
        self._guild_session.pop(session.guild_id, None)
        for user_id, mapped in list(self._user_session.items()):
            if mapped == session.session_id:
                self._user_session.pop(user_id, None)
        messenger = self._messengers.get(session.session_id)
        translate: Callable[[str], str] = self._translators.get(session.session_id, _passthrough)
        key = {
            SessionPhase.aborted: "commands.quick-battle.aborted",
            SessionPhase.timed_out: "commands.quick-battle.timed_out",
            SessionPhase.failed: "commands.quick-battle.generation_failed",
            SessionPhase.dispatch_failed: "commands.quick-battle.dispatch_failed",
            SessionPhase.task_timeout: "commands.quick-battle.task_timeout",
            SessionPhase.hard_stopped: "commands.quick-battle.update_in_progress",
        }.get(phase)
        if messenger is not None and key is not None:
            for message_id in {session.active_message_id, session.lobby_message_id}:
                if message_id:
                    try:
                        await messenger.edit(message_id, translate(key), disable=True)
                    except Exception:
                        logger.exception("quick-battle terminal edit failed")
        self.sessions.pop(session.session_id, None)
        self._messengers.pop(session.session_id, None)
        self._translators.pop(session.session_id, None)
        return session


__all__ = ["BattleHost", "BattleMessenger", "PHASE_MARKERS", "QuickBattleService"]
