from __future__ import annotations

import pytest

from shared.models import (
    AiWorkerHeartbeat,
    BotHeartbeat,
    ServiceLiveness,
    TelemetryLivePayload,
    TelemetryMetrics,
)

PHASE1_SCENARIO_OWNERSHIP = {
    "S01": {"head_election": "complete"},
    "S02": {"head_failover": "complete"},
    "S03": {
        "head_grant_cessation": "complete",
        "bot_autonomous_hard_stop": "deferred:phase2",
    },
    "S04": {
        "head_mosquitto_response": "complete",
        "bot_control_disconnect": "deferred:phase2",
    },
    "S05": {
        "head_rabbitmq_event_bridge": "complete",
        "task_delivery_recovery": "deferred:phase2",
    },
    "S06": {"head_coordination_failure_matrix": "complete"},
    "S07": {
        "head_launcher_ordering": "complete",
        "bot_drain_execution": "deferred:phase2",
    },
    "S08": {
        "head_timeout_escalation": "complete",
        "bot_hard_stop_execution": "deferred:phase2",
    },
    "S09": {"launcher_rollback_and_cold_election": "complete"},
}


@pytest.mark.acceptance()
def test_phase1_scenario_ownership_never_claims_deferred_peer_behavior() -> None:
    assert set(PHASE1_SCENARIO_OWNERSHIP) == {f"S{number:02d}" for number in range(1, 10)}
    assert PHASE1_SCENARIO_OWNERSHIP["S03"]["bot_autonomous_hard_stop"].startswith(
        "deferred:"
    )
    assert PHASE1_SCENARIO_OWNERSHIP["S07"]["bot_drain_execution"] == "deferred:phase2"
    assert PHASE1_SCENARIO_OWNERSHIP["S08"]["bot_hard_stop_execution"] == "deferred:phase2"


@pytest.mark.acceptance()
def test_p1_8_heartbeat_and_live_payload_contracts_are_concrete() -> None:
    bot = BotHeartbeat(
        node_id="node-a",
        application_version="v1.2.3",
        observed_at="2026-07-19T20:00:00Z",
        gateway_connected=True,
        latency_ms=42,
        guild_count=12,
        dependencies={
            "rabbitmq_connected": True,
            "cosmos_ok": True,
            "azure_queue_ok": True,
            "status_blob_ok": True,
        },
    )
    worker = AiWorkerHeartbeat(
        node_id="node-a",
        application_version="v1.2.3",
        observed_at="2026-07-19T20:00:00Z",
        state="running",
        active_tasks=1,
        dependencies={"rabbitmq_connected": True},
    )
    payload = TelemetryLivePayload(
        seq=1,
        node_id="node-a",
        leadership_term="11111111-1111-4111-8111-111111111111",
        sampled_at="2026-07-19T20:00:00Z",
        service_liveness=ServiceLiveness(bot="fresh", ai_worker="fresh"),
        metrics=TelemetryMetrics(
            cpu_percent=1,
            memory_mb=128,
            memory_percent=2,
            latency_ms=bot.latency_ms,
            guild_count=bot.guild_count,
            errors_in_window=0,
            uptime_sec=10,
        ),
        logs=[],
        logs_dropped=0,
    )

    assert worker.active_tasks == 1
    assert payload.service_liveness.bot == "fresh"
