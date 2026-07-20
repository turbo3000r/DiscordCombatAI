from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from shared.models import (
    ActivationGrant,
    ActivationGrantMode,
    AiWorkerDesiredState,
    AiWorkerDesiredStateValue,
    BotDesiredState,
    BotDesiredStateValue,
    LeaderHeartbeat,
    LeaseBlobRef,
)

from .clock import Clock
from .lease import LeaseCoordinator, LeaseLostError
from .mqtt import MqttManager
from .pubsub import ClusterPubSub
from .settings import HeadSettings


class ElectionState(StrEnum):
    FOLLOWER = "FOLLOWER"
    CLAIMING = "CLAIMING"
    LEADER = "LEADER"
    DRAINING = "DRAINING"
    UPDATING = "UPDATING"


class ElectionMachine:
    def __init__(
        self,
        *,
        settings: HeadSettings,
        clock: Clock,
        mqtt: MqttManager,
        lease: LeaseCoordinator,
        pubsub: ClusterPubSub,
        instance_id: UUID | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.mqtt = mqtt
        self.lease = lease
        self.pubsub = pubsub
        self.instance_id = instance_id or uuid_factory()
        self._uuid_factory = uuid_factory
        self.state = ElectionState.FOLLOWER
        self.leadership_term: UUID | None = None
        self.command_seq = 0
        self.last_heartbeat_received = clock.monotonic()
        self._last_lease_renewal = clock.monotonic()
        self._last_grant = float("-inf")
        self._soft_stop_deadline: float | None = None
        self._planned_drain = False

    @property
    def is_leader(self) -> bool:
        return self.state in {
            ElectionState.LEADER,
            ElectionState.DRAINING,
            ElectionState.UPDATING,
        } and self.lease.held

    @property
    def can_restore_same_term(self) -> bool:
        return (
            self.state == ElectionState.DRAINING
            and not self._planned_drain
            and self.leadership_term is not None
            and self.lease.held
            and self.pubsub.connected
            and self.mqtt.connected
            and self.mqtt.safe_state_confirmed
            and self.clock.monotonic() - self._last_lease_renewal
            < self.settings.election_heartbeat_sec
        )

    async def start(self) -> None:
        startup = BotDesiredState(
            state=BotDesiredStateValue.inactive,
            head_instance_id=str(self.instance_id),
            leadership_term=None,
            command_seq=0,
            reason="head_startup",
            issued_at=self.clock.utcnow(),
        )
        await self.mqtt.start(startup)
        await self.pubsub.start()
        self.last_heartbeat_received = self.clock.monotonic()

    def observe_heartbeat(self, heartbeat: LeaderHeartbeat) -> None:
        if heartbeat.head_instance_id != str(self.instance_id):
            self.last_heartbeat_received = self.clock.monotonic()

    async def tick(self) -> None:
        now = self.clock.monotonic()
        if self.state == ElectionState.FOLLOWER:
            if now - self.last_heartbeat_received >= self.settings.election_heartbeat_timeout_sec:
                await self._claim()
            return
        if self.state not in {
            ElectionState.LEADER,
            ElectionState.DRAINING,
            ElectionState.UPDATING,
        }:
            return
        if self._soft_stop_deadline is not None and now >= self._soft_stop_deadline:
            await self.hard_stop("coordination_recovery_timeout")
            return
        if now - self._last_lease_renewal >= self.settings.election_heartbeat_sec:
            try:
                await self.lease.renew()
            except LeaseLostError:
                await self.hard_stop("lease_lost")
                return
            except Exception:
                if self.pubsub.connected:
                    await self.soft_stop("lease_renewal_uncertain")
                else:
                    await self.hard_stop("coordination_planes_unavailable")
                return
            self._last_lease_renewal = now
            try:
                await self._publish_heartbeat()
            except Exception:
                await self.pubsub.mark_disconnected()
                await self.soft_stop("pubsub_unavailable")
                return
        if (
            self.state in {ElectionState.LEADER, ElectionState.DRAINING}
            and now - self._last_grant >= self.settings.bot_grant_renew_sec
        ):
            mode = (
                ActivationGrantMode.active
                if self.state == ElectionState.LEADER
                else ActivationGrantMode.draining
            )
            try:
                await self._issue_grant(mode)
            except Exception:
                await self.handle_mqtt_loss("grant_publish_failed")

    async def _claim(self) -> None:
        if not (
            self.mqtt.connected
            and self.mqtt.safe_state_confirmed
            and self.pubsub.connected
        ):
            return
        self.state = ElectionState.CLAIMING
        try:
            acquired = await self.lease.acquire()
        except Exception:
            self.state = ElectionState.FOLLOWER
            return
        if not acquired:
            self.state = ElectionState.FOLLOWER
            self.last_heartbeat_received = self.clock.monotonic()
            return
        self.leadership_term = self._uuid_factory()
        self.command_seq = 0
        self.state = ElectionState.LEADER
        self._planned_drain = False
        self._soft_stop_deadline = None
        self._last_lease_renewal = self.clock.monotonic()
        self.mqtt.set_authoritative(True)
        try:
            await self._issue_grant(ActivationGrantMode.active)
        except Exception:
            await self.handle_mqtt_loss("promotion_grant_publish_failed")
            return
        try:
            await self._publish_heartbeat()
        except Exception:
            await self.handle_pubsub_loss()

    async def _publish_heartbeat(self) -> None:
        if self.leadership_term is None or not self.lease.held:
            raise RuntimeError("heartbeat denied without a held lease")
        now = self.clock.utcnow()
        await self.pubsub.send(
            LeaderHeartbeat(
                node_id=self.settings.node_id,
                head_instance_id=str(self.instance_id),
                leadership_term=str(self.leadership_term),
                lease_blob=LeaseBlobRef(
                    container=self.settings.lease_blob_container,
                    name=self.settings.lease_blob_name,
                ),
                issued_at=now,
                lease_expires_at=now + timedelta(seconds=self.settings.lease_duration_sec),
                application_version=self.settings.application_version,
            )
        )

    async def _issue_grant(self, mode: ActivationGrantMode) -> ActivationGrant:
        if self.leadership_term is None or not self.lease.held:
            raise RuntimeError("grant denied without current lease authority")
        self.command_seq += 1
        ttl = self.settings.bot_grant_ttl_sec
        if self._soft_stop_deadline is not None:
            remaining = max(1, int(self._soft_stop_deadline - self.clock.monotonic()))
            ttl = min(ttl, remaining)
        grant = ActivationGrant(
            grant_id=str(self._uuid_factory()),
            node_id=self.settings.node_id,
            head_instance_id=str(self.instance_id),
            leadership_term=str(self.leadership_term),
            command_seq=self.command_seq,
            mode=mode,
            ttl_sec=ttl,
            issued_at=self.clock.utcnow(),
        )
        await self.mqtt.publish_grant(grant)
        self._last_grant = self.clock.monotonic()
        return grant

    async def _desired(
        self, state: BotDesiredStateValue, reason: str
    ) -> BotDesiredState:
        if self.leadership_term is None:
            raise RuntimeError("term-scoped desired state requires a leadership term")
        self.command_seq += 1
        desired = BotDesiredState(
            state=state,
            head_instance_id=str(self.instance_id),
            leadership_term=str(self.leadership_term),
            command_seq=self.command_seq,
            reason=reason,
            issued_at=self.clock.utcnow(),
        )
        await self.mqtt.publish_desired_state(desired)
        return desired

    async def soft_stop(self, reason: str) -> None:
        if self.state not in {ElectionState.LEADER, ElectionState.DRAINING}:
            return
        self.state = ElectionState.DRAINING
        if self._soft_stop_deadline is None:
            self._soft_stop_deadline = (
                self.clock.monotonic() + self.settings.bot_grant_ttl_sec
            )
        if self.mqtt.connected:
            await self._desired(BotDesiredStateValue.draining, reason)
            if self.lease.held:
                await self._issue_grant(ActivationGrantMode.draining)

    async def restore_same_term(self) -> None:
        if not self.can_restore_same_term:
            raise RuntimeError("same-term authority is not fully restored")
        self._soft_stop_deadline = None
        self.state = ElectionState.LEADER
        self.mqtt.set_authoritative(True)
        await self._issue_grant(ActivationGrantMode.active)

    async def handle_pubsub_loss(self) -> None:
        await self.pubsub.mark_disconnected()
        if self.lease.held:
            await self.soft_stop("pubsub_unavailable")
        else:
            await self.hard_stop("coordination_planes_unavailable")

    async def handle_mqtt_loss(self, reason: str) -> None:
        await self.mqtt.mark_disconnected(reason)
        if self.state in {ElectionState.LEADER, ElectionState.DRAINING}:
            self.state = ElectionState.DRAINING
            self._soft_stop_deadline = (
                self.clock.monotonic() + self.settings.bot_grant_ttl_sec
            )

    async def hard_stop(self, reason: str) -> int | None:
        stopped_seq: int | None = None
        if self.leadership_term is not None and self.mqtt.connected:
            try:
                stopped_seq = (
                    await self._desired(BotDesiredStateValue.stopped, reason)
                ).command_seq
            except Exception:
                stopped_seq = None
        self.mqtt.set_authoritative(False)
        if stopped_seq is not None and self.lease.held:
            try:
                await self.lease.release()
            except Exception:
                self.lease.abandon()
        elif self.lease.held:
            self.lease.abandon()
        self._soft_stop_deadline = None
        self.state = ElectionState.FOLLOWER
        self.leadership_term = None
        self.command_seq = 0
        self.last_heartbeat_received = self.clock.monotonic()
        return stopped_seq

    async def begin_planned_drain(self) -> UUID:
        if self.state != ElectionState.LEADER or self.leadership_term is None:
            raise RuntimeError("only the leader can begin a planned drain")
        self.state = ElectionState.DRAINING
        self._planned_drain = True
        await self._desired(BotDesiredStateValue.draining, "planned_update")
        await self.mqtt.publish_worker_state(
            AiWorkerDesiredState(state=AiWorkerDesiredStateValue.paused)
        )
        await self._issue_grant(ActivationGrantMode.draining)
        return self.leadership_term

    async def prepare_update(self, reason: str) -> int:
        if self.state != ElectionState.DRAINING or self.leadership_term is None:
            raise RuntimeError("update preparation requires a draining leader")
        desired = await self._desired(BotDesiredStateValue.stopped, reason)
        self.mqtt.set_authoritative(False)
        self.state = ElectionState.UPDATING
        return desired.command_seq

    async def release_for_update(self) -> None:
        if self.state != ElectionState.UPDATING:
            raise RuntimeError("lease release is allowed only after stopped command")
        await self.lease.release()

    async def close(self) -> None:
        if self.leadership_term is not None and self.lease.held:
            await self.hard_stop("head_shutdown")
        await self.pubsub.close()
        await self.mqtt.close()


__all__ = ["ElectionMachine", "ElectionState"]
