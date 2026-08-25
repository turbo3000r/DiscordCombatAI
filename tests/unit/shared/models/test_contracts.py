from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shared.models import (
    ActivationGrant,
    AiTaskResultFailed,
    AiTaskResultSuccess,
    AiWorkerDesiredState,
    BattleArchiveMetadata,
    BotDesiredState,
    ControlAck,
    DrainProgress,
    GuildConfigDocument,
    HealthResponse,
    LauncherStatusResponse,
    NodeMetricsEntity,
    PauseAck,
    PubSubNegotiateResponse,
    StatusDocument,
    SuggestionDocument,
    SuggestionQueueMessage,
    TaskPhase,
    TaskProgressMessage,
    TelemetryLivePayload,
    UnknownSchemaVersionError,
    UpdateAvailableMessage,
    UpdateRequest,
    WebhookAuditRecord,
    build_battle_archive_paths,
    build_row_key,
    build_status_seed,
    build_suggestion_seed_catalog,
    parse_ai_task_envelope,
    parse_ai_task_result,
    parse_launcher_update_response,
    parse_task_progress_message,
    redact_for_web,
    validate_discord_webhook_url,
)
from shared.models.leadership_control import LeaderHeartbeat, LeaseBlobRef
from shared.models.pubsub_live import build_negotiate_response
from shared.models.status_document import (
    BotIdentitySection,
    BotStatusSection,
    SuggestionCatalogSection,
)
from shared.models.suggestion import generate_ticket_uid
from shared.models.telemetry import ServiceLiveness, TelemetryMetrics

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "contracts"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_guild_config_roundtrip_and_redaction() -> None:
    payload = load_fixture("guild_config.json")
    document = GuildConfigDocument.parse_wire_json(json.dumps(payload))

    assert document.is_active() is True
    assert document.ai_language_locale() == "uk-UA"
    assert "api_key" not in redact_for_web(document)
    assert redact_for_web(document)["webhook_configured"] is True
    assert (
        GuildConfigDocument.parse_wire_json(document.model_dump_json()).guild_id
        == document.guild_id
    )


def test_guild_config_unknown_version_rejected() -> None:
    payload = load_fixture("guild_config.json")
    payload["schema_version"] = 99
    with pytest.raises(UnknownSchemaVersionError):
        GuildConfigDocument.parse_wire_json(json.dumps(payload))


def test_ai_task_envelopes_roundtrip() -> None:
    environment = parse_ai_task_envelope(load_fixture("ai_task_environment.json"))
    battle = parse_ai_task_envelope(load_fixture("ai_task_battle.json"))

    assert environment.graph == "environment"
    assert battle.graph == "battle"
    assert (
        parse_ai_task_envelope(environment.model_dump(mode="json")).task_id == environment.task_id
    )
    assert parse_ai_task_envelope(battle.model_dump(mode="json")).task_id == battle.task_id


def test_ai_task_results_roundtrip() -> None:
    success = AiTaskResultSuccess(
        task_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        graph="battle",
        result={"story": "done"},
        completed_at="2026-07-15T17:20:00Z",
    )
    failed = AiTaskResultFailed(
        task_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        graph="battle",
        node="worker_terminated",
        reason="test",
        completed_at="2026-07-15T17:20:00Z",
    )

    assert parse_ai_task_result(success.model_dump(mode="json")).status == "success"
    assert parse_ai_task_result(failed.model_dump(mode="json")).status == "failed"


def test_transport_shell_environment_result_fixture_roundtrip() -> None:
    from shared.models import EnvironmentState

    payload = load_fixture("ai_task_environment_result.json")
    result = parse_ai_task_result(payload)
    envelope = parse_ai_task_envelope(load_fixture("ai_task_environment.json"))

    assert result.status == "success"
    assert result.task_id == envelope.task_id
    assert result.graph == "environment"
    assert result.graph == envelope.graph
    final_environment = EnvironmentState.model_validate(result.result["final_environment"])
    assert final_environment.description == "Phase 2 transport-shell canned environment."
    assert final_environment.tags == ["phase2", "transport-shell"]
    assert final_environment.setting == "realistic"
    assert result.result["attempts_used"] == 0
    assert result.result["forced_selection"] is False
    assert parse_ai_task_result(result.model_dump(mode="json")).task_id == result.task_id


def test_battle_success_result_fixture_roundtrip() -> None:
    payload = load_fixture("ai_task_battle_result.json")
    result = parse_ai_task_result(payload)
    envelope = parse_ai_task_envelope(load_fixture("ai_task_battle.json"))

    assert result.status == "success"
    assert result.task_id == envelope.task_id
    assert result.graph == "battle"
    assert result.result["story"]
    assert result.result["winners"] == ["111111111111111111"]
    assert result.result["attempts_used"] == 1
    assert result.result["forced_selection"] is False
    assert parse_ai_task_result(result.model_dump(mode="json")).task_id == result.task_id


def test_transport_shell_progress_fixture_sequence() -> None:
    ticks = json.loads(
        (FIXTURES / "task_progress_transport_shell.json").read_text(encoding="utf-8")
    )
    envelope = parse_ai_task_envelope(load_fixture("ai_task_environment.json"))
    phases = [parse_task_progress_message(tick).phase for tick in ticks]

    assert phases == [
        TaskPhase.launching,
        TaskPhase.composing,
        TaskPhase.refining,
        TaskPhase.finishing,
    ]
    assert TaskPhase.queued not in phases
    for tick in ticks:
        message = parse_task_progress_message(tick)
        assert message.task_id == envelope.task_id
        assert message.graph == envelope.graph


def test_transport_shell_progress_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError):
        TaskProgressMessage(
            task_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
            graph="environment",
            phase=TaskPhase.launching,
            timestamp="2026-07-15T17:00:01",
        )


def test_transport_shell_progress_unknown_version_rejected() -> None:
    ticks = json.loads(
        (FIXTURES / "task_progress_transport_shell.json").read_text(encoding="utf-8")
    )
    ticks[0]["schema_version"] = 99
    with pytest.raises(UnknownSchemaVersionError):
        parse_task_progress_message(ticks[0])


def test_ai_task_unknown_version_rejected() -> None:
    payload = load_fixture("ai_task_environment.json")
    payload["schema_version"] = 9
    with pytest.raises(UnknownSchemaVersionError):
        parse_ai_task_envelope(payload)


def test_task_progress_roundtrip_and_validation() -> None:
    message = TaskProgressMessage(
        task_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        graph="battle",
        phase=TaskPhase.refining,
        timestamp="2026-07-15T17:20:00Z",
        attempt=2,
    )
    dumped = message.model_dump(mode="json")
    assert parse_task_progress_message(dumped).phase is TaskPhase.refining
    encoded = message.model_dump_json()
    assert parse_task_progress_message(encoded).phase is TaskPhase.refining
    assert parse_task_progress_message(encoded.encode("utf-8")).phase is TaskPhase.refining

    bad = message.model_dump(mode="json")
    bad["schema_version"] = 7
    with pytest.raises(UnknownSchemaVersionError):
        parse_task_progress_message(bad)

    with pytest.raises(ValueError):
        TaskProgressMessage(
            task_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
            graph="battle",
            phase=TaskPhase.refining,
            timestamp="2026-07-15T17:20:00",
        )


def test_leadership_control_roundtrip() -> None:
    desired = BotDesiredState(
        state="inactive",
        head_instance_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        leadership_term=None,
        command_seq=0,
        reason="head_startup",
        issued_at="2026-07-15T17:00:00Z",
    )
    grant = ActivationGrant(
        grant_id="366ed38e-1e52-427f-a8c9-726a69628f69",
        node_id="node-a",
        head_instance_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        leadership_term="cd88086a-fd6d-48d4-8446-39523af2bf70",
        command_seq=3,
        mode="active",
        ttl_sec=45,
        issued_at="2026-07-15T17:01:00Z",
    )
    ack = ControlAck(
        node_id="node-a",
        head_instance_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        leadership_term="cd88086a-fd6d-48d4-8446-39523af2bf70",
        command_seq=8,
        state="stopped",
        gateway_connected=False,
        observed_at="2026-07-15T17:03:00Z",
    )
    heartbeat = LeaderHeartbeat(
        node_id="node-a",
        head_instance_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        leadership_term="cd88086a-fd6d-48d4-8446-39523af2bf70",
        lease_blob=LeaseBlobRef(container="coordination", name="leader.lock"),
        issued_at="2026-07-15T17:01:00Z",
        lease_expires_at="2026-07-15T17:02:00Z",
        application_version="v1.4.0",
    )

    assert BotDesiredState.parse_wire_json(desired.model_dump_json()).state == desired.state
    assert ActivationGrant.parse_wire_json(grant.model_dump_json()).mode == grant.mode
    assert ControlAck.parse_wire_json(ack.model_dump_json()).state == ack.state
    assert LeaderHeartbeat.parse_wire_json(heartbeat.model_dump_json()).type == "leader_heartbeat"


def test_drain_status_roundtrip() -> None:
    drain = DrainProgress(
        node_id="node-a",
        leadership_term="cd88086a-fd6d-48d4-8446-39523af2bf70",
        in_flight_workflows=3,
        observed_at="2026-07-15T17:02:00Z",
    )
    pause = PauseAck(node_id="node-a", paused_at="2026-07-15T17:02:30Z")
    desired = AiWorkerDesiredState(state="paused")
    update = UpdateAvailableMessage(target_version="v1.5.0")

    assert DrainProgress.parse_wire_json(drain.model_dump_json()).in_flight_workflows == 3
    assert PauseAck.parse_wire_json(pause.model_dump_json()).node_id == "node-a"
    assert AiWorkerDesiredState.parse_wire_json(desired.model_dump_json()).state == desired.state
    assert (
        UpdateAvailableMessage.parse_wire_json(update.model_dump_json()).type == "update_available"
    )


def test_launcher_ipc_roundtrip_and_errors() -> None:
    request = UpdateRequest(
        request_id="02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
        target_version="v1.5.0",
        reason="manual",
        requested_at="2026-07-15T17:05:00Z",
        source_node_id="node-a",
    )
    busy = {
        "schema_version": 1,
        "request_id": "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
        "error": {
            "schema_version": 1,
            "code": "update_busy",
            "message": "Launcher is processing another target.",
        },
        "operation_id": "c39120a0-a554-447f-afc7-02c65e65e11d",
        "target_version": "v1.5.0",
    }

    assert UpdateRequest.parse_wire_json(request.model_dump_json()).reason == request.reason
    assert (
        parse_launcher_update_response(
            {
                "schema_version": 1,
                "request_id": "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
                "operation_id": "c39120a0-a554-447f-afc7-02c65e65e11d",
                "state": "accepted",
                "target_version": "v1.5.0",
            }
        ).state
        == "accepted"
    )
    assert (
        parse_launcher_update_response(
            {
                "schema_version": 1,
                "request_id": "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
                "operation_id": "c39120a0-a554-447f-afc7-02c65e65e11d",
                "state": "already_current",
                "target_version": "v1.5.0",
            }
        ).state
        == "already_current"
    )
    assert parse_launcher_update_response(busy).target_version == "v1.5.0"

    health = HealthResponse(
        status="alive",
        version="v1.5.0",
        instance_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        started_at="2026-07-15T17:06:00Z",
    )
    assert HealthResponse.parse_wire_json(health.model_dump_json()).status == health.status

    status = LauncherStatusResponse(
        state="IDLE",
        current_version="v1.5.0",
        previous_version="v1.4.0",
        operation_id=None,
        target_version=None,
    )
    assert LauncherStatusResponse.parse_wire_json(status.model_dump_json()).state == status.state

    with pytest.raises(ValueError):
        UpdateRequest(
            request_id="not-a-uuid",
            target_version="v1.5.0",
            reason="manual",
            requested_at="2026-07-15T17:05:00Z",
            source_node_id="node-a",
        )


def test_status_seed_roundtrip() -> None:
    seed = build_status_seed(seeded_at=datetime(2026, 7, 15, 17, 0, tzinfo=UTC))
    assert seed.identity == BotIdentitySection()
    assert seed.status == BotStatusSection(updated_at=datetime(2026, 7, 15, 17, 0, tzinfo=UTC))
    assert seed.suggestion_catalog == SuggestionCatalogSection(**build_suggestion_seed_catalog())
    assert StatusDocument.parse_wire_json(seed.model_dump_json()).schema_version == 1


def test_suggestion_roundtrip_and_transitions() -> None:
    document = SuggestionDocument.parse_wire_json(json.dumps(load_fixture("suggestion.json")))
    assert document.ticket_uid == "SUG-A1B2C3D4"
    assert generate_ticket_uid().startswith("SUG-")
    assert document.with_done_no_feedback().notification_status is None
    assert document.with_done_and_pending_feedback().notification_status == "pending"

    queue_message = SuggestionQueueMessage(
        id="550e8400-e29b-41d4-a716-446655440000",
        ticket_uid="SUG-A1B2C3D4",
        guild_id="123456789012345678",
        enqueued_at="2026-07-15T17:00:00Z",
    )
    assert (
        SuggestionQueueMessage.parse_wire_json(queue_message.model_dump_json()).ticket_uid
        == queue_message.ticket_uid
    )

    pending = document.model_copy(update={"notification_status": "pending"})
    claimed = pending.with_notification_claim("node-a", datetime(2026, 7, 15, 17, 2, tzinfo=UTC))
    assert claimed.notification_status == "claiming"
    assert claimed.notification_claimed_by == "node-a"
    assert claimed.with_notification_sent().notification_status == "sent"
    assert claimed.with_notification_retry("oops", 2).notification_status in {"pending", "failed"}

    with pytest.raises(UnknownSchemaVersionError):
        bad = document.model_dump(mode="json")
        bad["schema_version"] = 3
        SuggestionDocument.parse_wire_json(json.dumps(bad))


def test_telemetry_and_archive_roundtrip() -> None:
    metrics = NodeMetricsEntity(
        PartitionKey="node-a",
        RowKey="20260715170230_0001",
        node_id="node-a",
        leadership_term="cd88086a-fd6d-48d4-8446-39523af2bf70",
        sampled_at="2026-07-15T17:02:30Z",
        cpu_percent=42.5,
        memory_mb=2048.0,
        memory_percent=51.2,
        latency_ms=87,
        guild_count=12,
        errors_in_window=0,
        uptime_sec=3600,
        batch_interval_sec=60,
    )
    payload = TelemetryLivePayload(
        seq=1842,
        node_id="node-a",
        leadership_term="cd88086a-fd6d-48d4-8446-39523af2bf70",
        sampled_at="2026-07-15T17:02:10Z",
        service_liveness=ServiceLiveness(bot="fresh", ai_worker="fresh"),
        metrics=TelemetryMetrics(
            cpu_percent=42.5,
            memory_mb=2048.0,
            memory_percent=51.2,
            latency_ms=87,
            guild_count=12,
            errors_in_window=0,
            uptime_sec=3600,
        ),
        logs=[],
        logs_dropped=0,
    )

    assert build_row_key(datetime(2026, 7, 15, 17, 2, 30, tzinfo=UTC), 1) == "20260715170230_0001"
    assert NodeMetricsEntity.parse_wire_json(metrics.model_dump_json()).RowKey == metrics.RowKey
    assert TelemetryLivePayload.parse_wire_json(payload.model_dump_json()).type == "telemetry_live"

    story_path, meta_path = build_battle_archive_paths(
        "123456789012345678",
        datetime(2026, 7, 15, 17, 2, tzinfo=UTC),
        "6b44781e-40f8-4807-9b4b-9087430c14b6",
    )
    assert story_path.endswith(".txt")
    assert meta_path.endswith(".meta.json")


def test_webhook_allowlist_and_audit_roundtrip() -> None:
    assert (
        validate_discord_webhook_url("https://discord.com/api/webhooks/111/abc")
        == "https://discord.com/api/webhooks/111/abc"
    )

    record = WebhookAuditRecord(
        acted_by_oid="oid-1",
        acted_by_upn="admin@example.com",
        acted_at="2026-07-15T17:00:00Z",
        action="webhook_send",
        destination="ALL",
        guild_ids=["123456789012345678"],
        request_id="02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
        idempotency_key="02e75c7a-8de1-4e3b-883a-5c41ae8b98cb",
    )
    assert WebhookAuditRecord.parse_wire_json(record.model_dump_json()).acted_by_oid == "oid-1"

    with pytest.raises(ValueError):
        validate_discord_webhook_url("http://example.com/api/webhooks/1/2")


def test_pubsub_negotiate_roundtrip() -> None:
    negotiate = build_negotiate_response(
        "wss://example.test", datetime(2026, 7, 15, 18, 2, tzinfo=UTC), "dashboard-live"
    )
    assert (
        PubSubNegotiateResponse.parse_wire_json(negotiate.model_dump_json()).group
        == "dashboard-live"
    )


def test_battle_archive_metadata_roundtrip() -> None:
    metadata = BattleArchiveMetadata(
        task_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
        guild_id="123456789012345678",
        graph="battle",
        winners=["111111111111111111"],
        created_at="2026-07-15T17:02:00Z",
    )
    assert BattleArchiveMetadata.parse_wire_json(metadata.model_dump_json()).graph == "battle"


def test_launcher_models_invalid_id_and_timestamp() -> None:
    with pytest.raises(ValueError):
        UpdateRequest(
            request_id="not-a-uuid",
            target_version="v1.5.0",
            reason="manual",
            requested_at="2026-07-15T17:05:00Z",
            source_node_id="node-a",
        )
    with pytest.raises(ValueError):
        TaskProgressMessage(
            task_id="6b44781e-40f8-4807-9b4b-9087430c14b6",
            graph="battle",
            phase=TaskPhase.queued,
            timestamp="2026-07-15T17:20:00",
        )
