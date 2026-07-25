from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dev_support.store import connect
from dev_support.store.repositories import DevStore
from shared.models import ActivationGrant, ActivationGrantMode, build_status_seed
from shared.models.suggestion import SuggestionDocument


def test_status_seed_matches_canonical(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "dev.db"))
    store = DevStore(conn)
    seeded = store.ensure_status_seeded()
    expected = build_status_seed(seeded_at=seeded.status.updated_at)
    assert seeded.suggestion_catalog.model_dump() == expected.suggestion_catalog.model_dump()
    again = store.ensure_status_seeded()
    assert again.model_dump() == seeded.model_dump()
    conn.close()


def test_guild_crud_and_restart_persistence(tmp_path: Path) -> None:
    db = tmp_path / "dev.db"
    conn = connect(str(db))
    store = DevStore(conn)
    doc = store.ensure_active_guild(
        guild_id="123456789012345678",
        name="Dev Guild",
        icon_url=None,
        member_count=3,
        owner_id="111111111111111111",
    )
    assert doc.name == "Dev Guild"
    patched = store.patch_admin("123456789012345678", {"enabled": True, "model": "gemini"})
    assert patched.enabled is True
    conn.close()

    conn2 = connect(str(db))
    store2 = DevStore(conn2)
    loaded = store2.get_guild("123456789012345678")
    assert loaded.enabled is True
    assert loaded.model == "gemini"
    conn2.close()


def test_suggestion_crud(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "dev.db"))
    store = DevStore(conn)
    suggestion_id = str(uuid4())
    payload = {
        "schema_version": 2,
        "id": suggestion_id,
        "ticket_uid": "SUG-AABBCCDD",
        "guild_id": "123456789012345678",
        "title": "t",
        "details": "d",
        "type": "request",
        "categories": ["commands"],
        "submitter": {
            "id": "111111111111111111",
            "name": "user",
            "display_name": "User",
            "global_name": None,
            "discriminator": "0",
        },
        "contact": {"method": "dm", "user_id": "111111111111111111"},
        "locale": {"user": "en-US", "guild": "en-US", "stored": "en"},
        "guild_snapshot": {"id": "123456789012345678", "name": "Dev"},
        "context": {
            "interaction_id": str(uuid4()),
            "channel_id": "222222222222222222",
            "in_guild": True,
        },
        "status": "pending",
        "conversation": [],
        "response_text": None,
        "acted_by_oid": None,
        "acted_by_upn": None,
        "acted_at": None,
        "notification_status": None,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    created = store.create_suggestion(payload)
    assert isinstance(created, SuggestionDocument)
    listed = store.list_suggestions("123456789012345678")
    assert len(listed) == 1
    store.delete_suggestion("123456789012345678", suggestion_id)
    assert store.list_suggestions("123456789012345678") == []
    conn.close()


def test_activation_grant_wire_shape() -> None:
    grant = ActivationGrant(
        grant_id=str(uuid4()),
        node_id="node-local",
        head_instance_id=str(uuid4()),
        leadership_term=str(uuid4()),
        command_seq=1,
        mode=ActivationGrantMode.active,
        ttl_sec=45,
        issued_at=datetime.now(UTC),
    )
    parsed = ActivationGrant.parse_wire_json(grant.model_dump_json())
    assert parsed.mode is ActivationGrantMode.active
    assert parsed.ttl_sec == 45
