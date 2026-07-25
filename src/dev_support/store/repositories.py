"""SQLite-backed domain repositories for product development."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from shared.models.guild_config import (
    ADMIN_PATCH_FIELDS,
    METADATA_PATCH_FIELDS,
    GuildConfigDocument,
    create_guild_config_document,
)
from shared.models.status_document import StatusDocument, build_status_seed
from shared.models.suggestion import SuggestionDocument
from shared.models.telemetry import NodeMetricsEntity


class ConflictError(RuntimeError):
    pass


class NotFoundError(RuntimeError):
    pass


class DevStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def ensure_status_seeded(self) -> StatusDocument:
        row = self._conn.execute(
            "SELECT document_json FROM status_document WHERE id = 1"
        ).fetchone()
        if row is not None:
            return StatusDocument.model_validate_json(row["document_json"])
        seed = build_status_seed()
        self._conn.execute(
            "INSERT INTO status_document (id, document_json, revision) VALUES (1, ?, 1)",
            (seed.model_dump_json(),),
        )
        self._conn.commit()
        return seed

    def get_status(self) -> StatusDocument:
        return self.ensure_status_seeded()

    def update_status_section(self, section: str, payload: dict[str, Any]) -> Any:
        document = self.get_status()
        data = document.model_dump(mode="json")
        if section not in {"identity", "status", "suggestion_catalog"}:
            raise ValueError(f"unknown status section: {section}")
        data[section] = payload
        updated = StatusDocument.model_validate(data)
        self._conn.execute(
            "UPDATE status_document SET document_json = ?, revision = revision + 1 WHERE id = 1",
            (updated.model_dump_json(),),
        )
        self._conn.commit()
        return getattr(updated, section)

    def get_guild(self, guild_id: str) -> GuildConfigDocument:
        row = self._conn.execute(
            "SELECT document_json FROM guild_configs WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"guild {guild_id} not found")
        return GuildConfigDocument.model_validate_json(row["document_json"])

    def list_guilds(self, *, include_left: bool = False) -> list[GuildConfigDocument]:
        rows = self._conn.execute("SELECT document_json FROM guild_configs").fetchall()
        documents = [
            GuildConfigDocument.model_validate_json(row["document_json"]) for row in rows
        ]
        if include_left:
            return documents
        return [doc for doc in documents if doc.left_at is None]

    def ensure_active_guild(
        self,
        *,
        guild_id: str,
        name: str,
        icon_url: str | None,
        member_count: int,
        owner_id: str,
    ) -> GuildConfigDocument:
        try:
            self.get_guild(guild_id)
        except NotFoundError:
            document = create_guild_config_document(
                guild_id,
                name=name,
                icon_url=icon_url,
                member_count=member_count,
                owner_id=owner_id,
            )
            self._conn.execute(
                "INSERT INTO guild_configs (guild_id, document_json, revision) VALUES (?, ?, 1)",
                (guild_id, document.model_dump_json()),
            )
            self._conn.commit()
            return document
        patch = {
            "name": name,
            "icon_url": icon_url,
            "member_count": member_count,
            "owner_id": owner_id,
            "left_at": None,
            "updated_at": datetime.now(UTC),
        }
        return self.patch_guild(guild_id, patch, fields=METADATA_PATCH_FIELDS + ("left_at",))

    def patch_guild(
        self,
        guild_id: str,
        patch: dict[str, Any],
        *,
        fields: tuple[str, ...] = METADATA_PATCH_FIELDS,
    ) -> GuildConfigDocument:
        document = self.get_guild(guild_id)
        data = document.model_dump(mode="json")
        for key, value in patch.items():
            if key in fields or key == "left_at":
                if hasattr(value, "isoformat"):
                    data[key] = value.isoformat().replace("+00:00", "Z")
                else:
                    data[key] = value
        if "updated_at" not in patch:
            data["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        updated = GuildConfigDocument.model_validate(data)
        self._conn.execute(
            "UPDATE guild_configs SET document_json = ?, revision = revision + 1 "
            "WHERE guild_id = ?",
            (updated.model_dump_json(), guild_id),
        )
        self._conn.commit()
        return updated

    def patch_admin(self, guild_id: str, patch: dict[str, Any]) -> GuildConfigDocument:
        return self.patch_guild(guild_id, patch, fields=ADMIN_PATCH_FIELDS)

    def mark_left(self, guild_id: str) -> GuildConfigDocument:
        return self.patch_guild(
            guild_id,
            {"left_at": datetime.now(UTC)},
            fields=METADATA_PATCH_FIELDS,
        )

    def create_suggestion(self, payload: dict[str, Any]) -> SuggestionDocument:
        document = SuggestionDocument.model_validate(payload)
        self._conn.execute(
            "INSERT INTO suggestions (guild_id, suggestion_id, document_json, revision) "
            "VALUES (?, ?, ?, 1)",
            (str(document.guild_id), str(document.id), document.model_dump_json()),
        )
        self._conn.commit()
        return document

    def get_suggestion(self, guild_id: str, suggestion_id: str) -> SuggestionDocument:
        row = self._conn.execute(
            "SELECT document_json FROM suggestions WHERE guild_id = ? AND suggestion_id = ?",
            (guild_id, suggestion_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"suggestion {suggestion_id} not found")
        return SuggestionDocument.model_validate_json(row["document_json"])

    def list_suggestions(self, guild_id: str) -> list[SuggestionDocument]:
        rows = self._conn.execute(
            "SELECT document_json FROM suggestions WHERE guild_id = ?", (guild_id,)
        ).fetchall()
        return [SuggestionDocument.model_validate_json(row["document_json"]) for row in rows]

    def update_suggestion(self, payload: dict[str, Any]) -> SuggestionDocument:
        document = SuggestionDocument.model_validate(payload)
        cur = self._conn.execute(
            "UPDATE suggestions SET document_json = ?, revision = revision + 1 "
            "WHERE guild_id = ? AND suggestion_id = ?",
            (document.model_dump_json(), str(document.guild_id), str(document.id)),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"suggestion {document.id} not found")
        self._conn.commit()
        return document

    def delete_suggestion(self, guild_id: str, suggestion_id: str) -> None:
        cur = self._conn.execute(
            "DELETE FROM suggestions WHERE guild_id = ? AND suggestion_id = ?",
            (guild_id, suggestion_id),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"suggestion {suggestion_id} not found")
        self._conn.commit()

    def batch_upsert_metrics(self, entities: list[dict[str, Any]]) -> list[NodeMetricsEntity]:
        stored: list[NodeMetricsEntity] = []
        for raw in entities:
            entity = NodeMetricsEntity.model_validate(raw)
            self._conn.execute(
                "INSERT INTO metrics (node_id, row_key, document_json) VALUES (?, ?, ?) "
                "ON CONFLICT(node_id, row_key) DO UPDATE SET "
                "document_json = excluded.document_json",
                (entity.node_id, entity.RowKey, entity.model_dump_json()),
            )
            stored.append(entity)
        self._conn.commit()
        return stored

    def query_metrics_history(
        self, node_id: str, *, start_row_key: str, end_row_key: str
    ) -> list[NodeMetricsEntity]:
        rows = self._conn.execute(
            "SELECT document_json FROM metrics WHERE node_id = ? "
            "AND row_key >= ? AND row_key <= ? ORDER BY row_key",
            (node_id, start_row_key, end_row_key),
        ).fetchall()
        return [NodeMetricsEntity.model_validate_json(row["document_json"]) for row in rows]


__all__ = ["ConflictError", "DevStore", "NotFoundError"]
