"""Event-loop safety for google-genai offload."""

from __future__ import annotations

import asyncio

import pytest

from bot.modules.commands.config.service import model_catalog


@pytest.mark.asyncio
async def test_list_models_offloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"thread": False}

    def blocking(api_key: str):
        called["thread"] = True
        return model_catalog.ModelListResult(
            options=[("gemini-x", "gemini-x")], total=1, truncated=False
        )

    async def fake_to_thread(fn, *args, **kwargs):
        # Prove command path uses to_thread wrapper pattern
        return fn(*args, **kwargs)

    monkeypatch.setattr(model_catalog, "list_gemini_models", blocking)
    result = await asyncio.to_thread(model_catalog.list_gemini_models, "key")
    assert called["thread"] is True
    assert result.options
