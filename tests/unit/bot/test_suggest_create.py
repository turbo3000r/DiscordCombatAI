"""Suggest create idempotency tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.modules.commands.suggest.service.category_catalog import (
    CatalogError,
    parse_suggestion_catalog,
)
from bot.modules.commands.suggest.service.suggestion_service import (
    create_suggestion,
    deterministic_suggestion_id,
)
from shared.azure.errors import AzurePermanentError
from shared.models.suggestion import SuggestionDocument


def test_catalog_fail_closed_on_duplicates() -> None:
    with pytest.raises(CatalogError):
        parse_suggestion_catalog(
            {
                "types": [{"value": "a", "label": "A"}, {"value": "a", "label": "A2"}],
                "categories": [{"value": "c", "label": "C"}],
            }
        )


def test_deterministic_id_stable() -> None:
    a = deterministic_suggestion_id("987654321098765432")
    b = deterministic_suggestion_id("987654321098765432")
    assert a == b


@pytest.mark.asyncio
async def test_create_conflict_reloads() -> None:
    payload = {"id": "11111111-1111-4111-8111-111111111111", "guild_id": "dm"}
    doc = AsyncMock(spec=SuggestionDocument)
    repo = AsyncMock()
    repo.create = AsyncMock(
        side_effect=AzurePermanentError("conflict", status_code=409)
    )
    repo.get = AsyncMock(return_value=doc)
    result = await create_suggestion(repo, payload)
    assert result is doc
    repo.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_conflict_reloads_local_http_error() -> None:
    from shared.storage.local_adapters import LocalHttpError

    payload = {"id": "11111111-1111-4111-8111-111111111111", "guild_id": "dm"}
    doc = AsyncMock(spec=SuggestionDocument)
    repo = AsyncMock()
    repo.create = AsyncMock(side_effect=LocalHttpError("conflict", status_code=409))
    repo.get = AsyncMock(return_value=doc)
    result = await create_suggestion(repo, payload)
    assert result is doc
    repo.get.assert_awaited_once()
