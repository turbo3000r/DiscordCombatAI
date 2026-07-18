from __future__ import annotations

import pytest

from shared.azure.clients.cosmos import CosmosClient


class _Container:
    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []
        self.patches: list[tuple[str, str, list[dict], str, object]] = []
        self.upserts: list[dict] = []

    def read_item(self, item: str, partition_key: str):
        self.reads.append((item, partition_key))
        return {"id": item, "guild_id": partition_key, "_etag": "etag-1"}

    def patch_item(
        self,
        *,
        item: str,
        partition_key: str,
        patch_operations: list[dict],
        etag: str,
        match_condition: object,
    ):
        self.patches.append((item, partition_key, patch_operations, etag, match_condition))
        return {"id": item, "guild_id": partition_key, "_etag": "etag-2"}

    def upsert_item(self, document: dict):
        self.upserts.append(document)
        return {**document, "_etag": "etag-3"}

    def query_items(
        self, *, query: str, parameters: list[dict], enable_cross_partition_query: bool
    ):
        assert enable_cross_partition_query is True
        assert query.startswith("SELECT")
        return [{"id": "a", "guild_id": "g1"}]


class _AsyncQueryItems:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def __aiter__(self):
        async def _iter():
            for item in self._items:
                yield item

        return _iter()


@pytest.mark.asyncio()
async def test_cosmos_client_query_collects_async_pages(azure_settings, fake_credential) -> None:
    class _AsyncContainer(_Container):
        def query_items(
            self, *, query: str, parameters: list[dict], enable_cross_partition_query: bool
        ):
            return _AsyncQueryItems([{"id": "async", "guild_id": "g2"}])

    class _AsyncDatabase:
        def __init__(self) -> None:
            self.container = _AsyncContainer()

        def get_container_client(self, container_name: str) -> _AsyncContainer:
            return self.container

    class _AsyncService:
        def get_database_client(self, database_name: str) -> _AsyncDatabase:
            return _AsyncDatabase()

    client = CosmosClient(
        service="web",
        service_client=_AsyncService(),
        settings=azure_settings,
        credential=fake_credential,
    )
    rows = await client.query("GuildConfigs", "SELECT * FROM c")
    assert rows == [{"id": "async", "guild_id": "g2"}]


class _Database:
    def __init__(self, container: _Container) -> None:
        self.container = container

    def get_container_client(self, container_name: str) -> _Container:
        return self.container


class _Service:
    def __init__(self) -> None:
        self.container = _Container()

    def get_database_client(self, database_name: str) -> _Database:
        assert database_name == "DiscordCombatAI"
        return _Database(self.container)


@pytest.mark.asyncio()
async def test_cosmos_client_methods(azure_settings, fake_credential) -> None:
    service = _Service()
    client = CosmosClient(
        service="web",
        service_client=service,
        settings=azure_settings,
        credential=fake_credential,
    )

    doc = await client.point_read("GuildConfigs", "guild-1", "guild-1")
    assert doc["id"] == "guild-1"

    patched = await client.patch_if_match(
        "GuildConfigs",
        "guild-1",
        "guild-1",
        [{"op": "set", "path": "/name", "value": "new"}],
        etag="etag-1",
    )
    assert patched["_etag"] == "etag-2"

    saved = await client.upsert("GuildConfigs", {"id": "guild-2"})
    assert saved["_etag"] == "etag-3"

    rows = await client.query("GuildConfigs", "SELECT * FROM c")
    assert rows[0]["id"] == "a"
