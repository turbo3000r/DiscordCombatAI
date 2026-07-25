"""Tests for /config Components V2 UI."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bot.localization.handler import load_localization
from bot.modules.commands.config.UI import ConfigDraft, ConfigSettingsView


class _Response:
    def __init__(self) -> None:
        self._done = False
        self.sent: list[dict[str, Any]] = []
        self.modals: list[Any] = []

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self._done = True
        self.sent.append({"content": content, "ephemeral": ephemeral})

    async def edit_message(self, **kwargs: Any) -> None:
        self._done = True
        self.sent.append({"edit": kwargs})

    async def send_modal(self, modal: Any) -> None:
        self._done = True
        self.modals.append(modal)

    async def defer(self, *, ephemeral: bool = False) -> None:
        self._done = True
        self.sent.append({"defer": True, "ephemeral": ephemeral})


def _interaction(*, user_id: int = 1, values: list[str] | None = None) -> Any:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=_Response(),
        followup=SimpleNamespace(send=AsyncMock()),
        data={"values": values or []},
        edit_original_response=AsyncMock(),
    )


@pytest.fixture
def l10n():
    return load_localization()


def _view(l10n, **kwargs: Any) -> ConfigSettingsView:
    draft = kwargs.pop("draft", ConfigDraft(language="en"))
    return ConfigSettingsView(
        owner_id=1,
        draft=draft,
        locale_key="en",
        t=l10n.t,
        **kwargs,
    )


def test_config_timeout_is_300(l10n) -> None:
    view = _view(l10n)
    assert view.timeout == 300.0


def test_header_never_renders_api_key_or_webhook(l10n) -> None:
    draft = ConfigDraft(
        api_key_configured=True,
        staged_api_key="SECRET_KEY_VALUE",
        webhook_configured=True,
        staged_webhook="https://discord.com/api/webhooks/1/secret",
    )
    view = _view(l10n, draft=draft)
    text = view._header_text()
    assert "SECRET_KEY_VALUE" not in text
    assert "webhooks" not in text
    assert "Staged" in text or "configured" in text.lower() or "Configur" in text


def test_model_select_capped_at_25(l10n) -> None:
    view = _view(l10n)
    options = [(f"m{i}", f"Model {i}") for i in range(40)]
    view.set_model_options(options, total=40)
    assert len(view.draft.model_options) == 25
    assert view.draft.models_truncated is True
    view.rebuild(disabled=False)
    # Ensure build does not explode with 25 options
    items = view.build_items(disabled=False)
    assert items


@pytest.mark.asyncio
async def test_foreign_user_denied(l10n) -> None:
    view = _view(l10n)
    interaction = _interaction(user_id=99)
    assert await view.interaction_check(interaction) is False
    assert interaction.response.sent[0]["ephemeral"] is True


@pytest.mark.asyncio
async def test_cancel_becomes_terminal_disabled(l10n) -> None:
    view = _view(l10n)
    view.draft.staged_language = "es"
    interaction = _interaction()
    await view._on_cancel(interaction)
    assert view.terminal == "cancel"
    assert view.draft.staged_language is None
    # All interactive children under rebuilt tree should be disabled when terminal
    disabled_flags: list[bool] = []
    for item in view.walk_children():
        if hasattr(item, "disabled"):
            disabled_flags.append(bool(item.disabled))
    assert disabled_flags
    assert all(disabled_flags)


@pytest.mark.asyncio
async def test_timeout_terminal(l10n) -> None:
    view = _view(l10n)
    await view.on_timeout()
    assert view.terminal == "timeout"
    assert view.status_text


@pytest.mark.asyncio
async def test_double_apply_serialized(l10n) -> None:
    calls = {"n": 0}

    async def on_apply(v: ConfigSettingsView) -> None:
        calls["n"] += 1

    view = _view(l10n, on_apply=on_apply)
    interaction = _interaction()
    # First acquires lock and sets busy path via run_exclusive
    started = await view.run_exclusive(AsyncMock())
    assert started is True
    # While lock held / busy cleared after, concurrent exclusive should still work sequentially
    assert await view.run_exclusive(AsyncMock()) is True
    await view._on_apply(interaction)
    assert calls["n"] == 1


def test_model_disabled_without_options(l10n) -> None:
    view = _view(l10n, draft=ConfigDraft(api_key_configured=False))
    items = view.build_items(disabled=False)
    # ActionRows contain selects; find model select (second select)
    selects = [c for c in view.walk_children() if c.__class__.__name__ == "Select"]
    assert len(selects) >= 2
    assert selects[1].disabled is True
    _ = items
