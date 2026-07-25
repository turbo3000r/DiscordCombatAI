"""Phase 2 acceptance helpers — deterministic clocks and protocol simulators."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from shared.models import (
    ActivationGrant,
    ActivationGrantMode,
    BotDesiredState,
    BotDesiredStateValue,
    EnvironmentAiTaskEnvelope,
)

PHASE2_SCENARIO_OWNERSHIP: dict[str, dict[str, str]] = {
    "S03": {
        "1_grant_watchdog": "complete",
        "2_soft_stop_on_control_loss": "complete",
        "3_autonomous_hard_stop": "complete",
        "3a_discord_command_notices": "deferred",
        "4_follower_lease_race": "integration-only",
    },
    "S04": {
        "1_mqtt_disconnect_soft_stop": "complete",
        "2_head_draining_grant": "integration-only",
        "3_grant_expiry_hard_stop": "complete",
        "4_best_effort_progress": "complete",
        "slash_reject_copy": "deferred",
    },
    "S05": {
        "1_failed_publish_no_record": "complete",
        "2_gateway_unchanged": "complete",
        "3_amqp_reconnect": "complete",
        "4_stall_overall_timers": "complete",
        "5_recovery_dlq": "complete",
        "6_in_flight_workflows": "complete",
        "non_ai_commands": "deferred",
    },
    "S07": {
        "1_head_update_available": "integration-only",
        "2_draining_grant_drain_progress": "complete",
        "2a_reject_new_slash": "deferred",
        "3_zero_workflows": "complete",
        "4_8_launcher_path": "integration-only",
    },
    "S08": {
        "1_drain_progress_nonzero": "complete",
        "2_head_drain_timeout": "integration-only",
        "3_purge_revoke_gateway": "complete",
        "3a_lobby_cancel": "deferred",
        "4_no_revoke_at_drain_timeout": "complete",
        "5_demotion_launcher": "integration-only",
    },
    "S10": {
        "A1_bot_restart_loses_map": "complete",
        "A2_inactive_until_grant": "complete",
        "A3_orphan_result_discard": "complete",
        "A4_user_retry_lobby": "deferred",
        "B1_B2_worker_redelivery": "complete",
        "B3_stall_while_tracked": "complete",
        "B4_malformed_dlq": "complete",
    },
}


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._mono = start
        self._wall = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return self._mono

    def utcnow(self) -> datetime:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._wall = self._wall + timedelta(seconds=seconds)


def make_environment_envelope(*, task_id: str | None = None) -> EnvironmentAiTaskEnvelope:
    return EnvironmentAiTaskEnvelope(
        task_id=task_id or str(uuid4()),
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
        setting="realistic",
        language_locale="uk-UA",
        trace_id="trace-phase2",
        guild_id="123456789012345678",
        api_key="AIzaSyTestKey",
        model="gemini-2.5-flash",
        input_type="initial",
        raw_input=["A stormy battlefield."],
        existing_environment=None,
        max_enhancer_retries=3,
    )


def grant_bytes(
    *,
    seq: int = 1,
    mode: ActivationGrantMode = ActivationGrantMode.active,
    ttl: int = 45,
    node_id: str = "node-local",
    term: str = "cd88086a-fd6d-48d4-8446-39523af2bf70",
) -> bytes:
    grant = ActivationGrant(
        grant_id=str(uuid4()),
        node_id=node_id,
        head_instance_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        leadership_term=term,
        command_seq=seq,
        mode=mode,
        ttl_sec=ttl,
        issued_at=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
    )
    return grant.model_dump_json().encode("utf-8")


def desired_state_bytes(
    *,
    state: BotDesiredStateValue = BotDesiredStateValue.inactive,
    seq: int = 0,
    term: str | None = None,
) -> bytes:
    desired = BotDesiredState(
        state=state,
        head_instance_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        leadership_term=term,
        command_seq=seq,
        reason="acceptance",
        issued_at=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
    )
    return desired.model_dump_json().encode("utf-8")


def ownership_labels() -> set[str]:
    labels: set[str] = set()
    for scenario in PHASE2_SCENARIO_OWNERSHIP.values():
        labels.update(scenario.values())
    return labels


__all__ = [
    "PHASE2_SCENARIO_OWNERSHIP",
    "FakeClock",
    "desired_state_bytes",
    "grant_bytes",
    "make_environment_envelope",
    "ownership_labels",
]
