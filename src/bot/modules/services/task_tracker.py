"""Loop-owned AI task tracking, timers, and workflow accounting."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from shared.models import (
    AiTaskResult,
    AiTaskResultFailed,
    BattleAiTaskEnvelope,
    EnvironmentAiTaskEnvelope,
    TaskPhase,
    TaskProgressMessage,
)

DispatchEnvelope = EnvironmentAiTaskEnvelope | BattleAiTaskEnvelope

logger = logging.getLogger(__name__)

CompletionCallback = Callable[[AiTaskResult], Awaitable[None]]
PhaseCallback = Callable[[str, TaskPhase], Awaitable[None]]


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def utcnow(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        import time

        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(tz=UTC)


@dataclass
class TaskRecord:
    task_id: str
    command: str
    graph: str
    current_phase: TaskPhase
    phase_history: list[tuple[str, datetime]]
    created_at: datetime
    last_progress_at: datetime
    completion_callback: CompletionCallback
    stall_timeout_sec: float
    overall_timeout_sec: float
    stall_handle: asyncio.TimerHandle | None = None
    overall_handle: asyncio.TimerHandle | None = None
    discord_message_ref: Any = None


@dataclass
class TaskTracker:
    """Container-local task map. Phase 2 in_flight_workflows == len(records)."""

    stall_timeout_sec: float
    overall_timeout_sec: float
    clock: Clock = field(default_factory=SystemClock)
    on_phase_change: PhaseCallback | None = None
    records: dict[str, TaskRecord] = field(default_factory=dict)
    revoke_calls: list[str] = field(default_factory=list)
    workflows: dict[str, None] = field(default_factory=dict)

    @property
    def in_flight_workflows(self) -> int:
        """One accepted /quick-battle is one unit; nested graph tasks do not add."""
        command_tasks = any(record.command != "harness" for record in self.records.values())
        if self.workflows or command_tasks:
            harness = sum(1 for record in self.records.values() if record.command == "harness")
            return len(self.workflows) + harness
        return len(self.records)

    def start_workflow(self, workflow_id: str) -> None:
        if workflow_id in self.workflows:
            raise ValueError(f"workflow already open: {workflow_id}")
        self.workflows[workflow_id] = None

    def finish_workflow(self, workflow_id: str) -> None:
        self.workflows.pop(workflow_id, None)

    def task_ids(self) -> list[str]:
        return list(self.records)

    async def create_from_dispatch(
        self,
        *,
        envelope: DispatchEnvelope,
        completion_callback: CompletionCallback,
        command: str = "harness",
    ) -> TaskRecord:
        task_id = str(envelope.task_id)
        if task_id in self.records:
            raise ValueError(f"task already tracked: {task_id}")
        now = self.clock.utcnow()
        record = TaskRecord(
            task_id=task_id,
            command=command,
            graph=envelope.graph,
            current_phase=TaskPhase.queued,
            phase_history=[(TaskPhase.queued.value, now)],
            created_at=now,
            last_progress_at=now,
            completion_callback=completion_callback,
            stall_timeout_sec=self.stall_timeout_sec,
            overall_timeout_sec=self.overall_timeout_sec,
        )
        self.records[task_id] = record
        self._arm_timers(record)
        return record

    async def handle_progress(self, progress: TaskProgressMessage) -> None:
        task_id = str(progress.task_id)
        record = self.records.get(task_id)
        if record is None:
            return
        if progress.graph != record.graph:
            return
        record.last_progress_at = progress.timestamp
        self._reschedule_stall(record)
        if progress.phase is record.current_phase:
            return
        record.current_phase = progress.phase
        record.phase_history.append((progress.phase.value, progress.timestamp))
        if self.on_phase_change is not None:
            try:
                await self.on_phase_change(task_id, progress.phase)
            except Exception:  # noqa: BLE001 — isolate callback failures
                logger.exception("phase callback failed")

    async def handle_result(self, result: AiTaskResult) -> None:
        task_id = str(result.task_id)
        record = self.records.pop(task_id, None)
        if record is None:
            return
        self._cancel_timers(record)
        try:
            await record.completion_callback(result)
        except Exception:  # noqa: BLE001 — isolate callback failures
            logger.exception("completion callback failed")

    async def synthesize_failure(
        self,
        task_id: str,
        *,
        node: str,
        reason: str,
    ) -> None:
        record = self.records.get(task_id)
        if record is None:
            return
        result = AiTaskResultFailed(
            task_id=task_id,
            graph=record.graph,  # type: ignore[arg-type]
            node=node,
            reason=reason,
            completed_at=self.clock.utcnow(),
        )
        await self.handle_result(result)

    async def synthesize_worker_terminated(self, task_ids: list[str] | None = None) -> None:
        ids = task_ids if task_ids is not None else self.task_ids()
        for task_id in list(ids):
            await self.synthesize_failure(
                task_id,
                node="worker_terminated",
                reason="hard-stop terminated in-flight work",
            )

    def clear_all_timers(self) -> None:
        for record in self.records.values():
            self._cancel_timers(record)

    def _arm_timers(self, record: TaskRecord) -> None:
        loop = asyncio.get_running_loop()
        self._reschedule_stall(record)
        record.overall_handle = loop.call_later(
            record.overall_timeout_sec,
            lambda: asyncio.create_task(
                self.synthesize_failure(
                    record.task_id,
                    node="bot_task_timeout",
                    reason="overall AI task timeout exceeded",
                ),
                name=f"overall-{record.task_id}",
            ),
        )

    def _reschedule_stall(self, record: TaskRecord) -> None:
        loop = asyncio.get_running_loop()
        if record.stall_handle is not None:
            record.stall_handle.cancel()
        record.stall_handle = loop.call_later(
            record.stall_timeout_sec,
            lambda: asyncio.create_task(
                self.synthesize_failure(
                    record.task_id,
                    node="bot_stall_timeout",
                    reason="no progress within stall timeout",
                ),
                name=f"stall-{record.task_id}",
            ),
        )

    def _cancel_timers(self, record: TaskRecord) -> None:
        if record.stall_handle is not None:
            record.stall_handle.cancel()
            record.stall_handle = None
        if record.overall_handle is not None:
            record.overall_handle.cancel()
            record.overall_handle = None


__all__ = ["Clock", "SystemClock", "TaskRecord", "TaskTracker"]
