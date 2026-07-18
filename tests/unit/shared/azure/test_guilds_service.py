from __future__ import annotations

import pytest

from shared.azure.services.guilds import GuildConfigService


class _CosmosClient:
    def __init__(self) -> None:
        self.docs = {
            "123456789012345678": {
                "schema_version": 1,
                "id": "123456789012345678",
                "guild_id": "123456789012345678",
                "language": "en",
                "api_key": "old-key",
                "model": "old-model",
                "webhook_url": "",
                "enabled": False,
                "name": "Old Name",
                "icon_url": None,
                "member_count": 1,
                "owner_id": "123456789012345679",
                "created_at": "2026-07-17T00:00:00Z",
                "updated_at": "2026-07-17T00:00:00Z",
                "left_at": "2026-07-17T12:00:00Z",
                "_etag": "etag-1",
            }
        }
        self.patches: list[list[dict]] = []
        self.upserts: list[dict] = []

    async def point_read(self, container_name: str, item_id: str, partition_key: str):
        return self.docs[item_id]

    async def query(self, container_name: str, query: str, parameters: list[dict] | None = None):
        return list(self.docs.values())

    async def upsert(self, container_name: str, document: dict):
        self.upserts.append(document)
        self.docs[document["id"]] = {**document, "_etag": "etag-2"}
        return self.docs[document["id"]]

    async def patch_if_match(
        self,
        container_name: str,
        item_id: str,
        partition_key: str,
        operations: list[dict],
        etag: str,
    ):
        self.patches.append(operations)
        doc = {**self.docs[item_id]}
        for op in operations:
            key = op["path"].lstrip("/")
            doc[key] = op["value"]
        doc["_etag"] = "etag-2"
        self.docs[item_id] = doc
        return doc


@pytest.mark.asyncio()
async def test_guild_service_patch_and_rejoin() -> None:
    cosmos = _CosmosClient()
    service = GuildConfigService(cosmos_client=cosmos, container_name="GuildConfigs")

    patched = await service.patch_admin_config(
        "123456789012345678",
        {
            "language": "es",
            "api_key": "new-key",
            "model": "new-model",
            "webhook_url": "https://discord.com/api/webhooks/abc",
            "enabled": True,
        },
    )
    assert patched.language == "es"
    assert cosmos.patches[0][-1]["path"] == "/updated_at"

    rejoined = await service.ensure_active_guild(
        guild_id="123456789012345678",
        name="New Name",
        icon_url="https://cdn/icon.png",
        member_count=9,
        owner_id="123456789012345680",
    )
    assert rejoined.left_at is None
    assert rejoined.api_key == "new-key"
    assert rejoined.name == "New Name"
    assert cosmos.upserts == []
    assert any(op["path"] == "/left_at" and op["value"] is None for op in cosmos.patches[-1])
