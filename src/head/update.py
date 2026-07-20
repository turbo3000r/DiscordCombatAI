from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from shared.models import ControlAck, DrainProgress, UpdateAvailableMessage, UpdateReason

from .clock import Clock
from .election import ElectionMachine, ElectionState
from .launcher import LauncherClient, LauncherResult
from .mqtt import HeadMqttEvent, MqttManager
from .pubsub import ClusterPubSub


class DrainObserver(Protocol):
    async def wait_for_zero(self, leadership_term: str, timeout_sec: float) -> bool: ...

    async def wait_for_stopped(
        self, leadership_term: str, command_seq: int, timeout_sec: float
    ) -> bool: ...


class MqttDrainObserver:
    def __init__(self, mqtt: MqttManager, clock: Clock) -> None:
        self._mqtt = mqtt
        self._clock = clock

    async def wait_for_zero(self, leadership_term: str, timeout_sec: float) -> bool:
        return await self._wait(
            timeout_sec,
            lambda event: (
                isinstance(event, DrainProgress)
                and event.leadership_term == leadership_term
                and event.in_flight_workflows == 0
            ),
        )

    async def wait_for_stopped(
        self, leadership_term: str, command_seq: int, timeout_sec: float
    ) -> bool:
        return await self._wait(
            timeout_sec,
            lambda event: (
                isinstance(event, ControlAck)
                and event.leadership_term == leadership_term
                and event.command_seq == command_seq
                and event.state == "stopped"
                and not event.gateway_connected
            ),
        )

    async def _wait(
        self,
        timeout_sec: float,
        predicate: Callable[[HeadMqttEvent], bool],
    ) -> bool:
        deferred: list[HeadMqttEvent] = []
        deadline = self._clock.monotonic() + timeout_sec
        try:
            while True:
                remaining = deadline - self._clock.monotonic()
                if remaining <= 0:
                    return False
                receive = asyncio.create_task(self._mqtt.events.get())
                timeout = asyncio.create_task(self._clock.sleep(remaining))
                done, pending = await asyncio.wait(
                    {receive, timeout}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if timeout in done:
                    return False
                event = receive.result()
                if predicate(event):
                    return True
                deferred.append(event)
        finally:
            for event in deferred:
                await self._mqtt.events.put(event)


class UpdateOrchestrator:
    def __init__(
        self,
        *,
        election: ElectionMachine,
        cluster: ClusterPubSub,
        launcher: LauncherClient,
        observer: DrainObserver,
    ) -> None:
        self._election = election
        self._cluster = cluster
        self._launcher = launcher
        self._observer = observer
        self.current_target: str | None = None
        self.pending_target: str | None = None
        self._acted_on: set[str] = set()

    async def announce(self, target_version: str) -> None:
        if self._election.state != ElectionState.LEADER:
            raise RuntimeError("only the leader may announce a release")
        await self._cluster.send(UpdateAvailableMessage(target_version=target_version))

    async def handle(self, message: UpdateAvailableMessage) -> LauncherResult | None:
        target = message.target_version
        if target in self._acted_on or target == self.current_target:
            return None
        if self.current_target is not None:
            if target != self.current_target:
                self.pending_target = target
            return None

        result: LauncherResult | None = None
        next_target: str | None = target
        while next_target is not None:
            self.current_target = next_target
            self.pending_target = None
            try:
                result = await self._run_current(next_target)
            except BaseException:
                self.current_target = None
                raise
            self._acted_on.add(next_target)
            next_target = self.pending_target
            self.current_target = None
        return result

    async def _run_current(self, target_version: str) -> LauncherResult:
        if self._election.state == ElectionState.LEADER:
            term = await self._election.begin_planned_drain()
            drained = await self._observer.wait_for_zero(
                str(term), self._election.settings.drain_timeout_sec
            )
            stopped_seq = await self._election.prepare_update(
                "planned_update_drained" if drained else "planned_update_drain_timeout"
            )
            await self._observer.wait_for_stopped(
                str(term),
                stopped_seq,
                self._election.settings.bot_stop_ack_timeout_sec,
            )
            await self._election.release_for_update()
        return await self._launcher.request_update(
            target_version, reason=UpdateReason.auto_detected
        )


__all__ = ["DrainObserver", "MqttDrainObserver", "UpdateOrchestrator"]
