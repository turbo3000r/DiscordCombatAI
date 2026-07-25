"""Tests for /suggest Components V2 UI."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bot.localization.handler import load_localization
from bot.modules.commands.suggest.UI import SuggestionDraft, SuggestionView


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


def _draft() -> SuggestionDraft:
    return SuggestionDraft(
        type_options=[("bug", "Bug"), ("idea", "Idea")],
        category_options=[("ui", "UI"), ("ai", "AI"), ("other", "Other")],
    )


def _view(l10n, draft: SuggestionDraft | None = None, **kwargs: Any) -> SuggestionView:
    return SuggestionView(
        owner_id=1,
        draft=draft or _draft(),
        locale_key="en",
        t=l10n.t,
        **kwargs,
    )


def test_suggest_timeout_is_600(l10n) -> None:
    assert _view(l10n).timeout == 600.0


@pytest.mark.asyncio
async def test_modal_button_gated_until_selections(l10n) -> None:
    view = _view(l10n)
    buttons = [c for c in view.walk_children() if c.__class__.__name__ == "Button"]
    assert buttons
    assert buttons[0].disabled is True

    await view._on_type(_interaction(values=["bug"]))
    await view._on_categories(_interaction(values=["ui", "ai"]))
    buttons = [c for c in view.walk_children() if c.__class__.__name__ == "Button"]
    assert buttons[0].disabled is False
    assert view.draft.type_value == "bug"
    assert view.draft.category_values == ["ui", "ai"]


@pytest.mark.asyncio
async def test_open_modal_is_first_response(l10n) -> None:
    draft = _draft()
    draft.type_value = "bug"
    draft.category_values = ["ui"]
    view = _view(l10n, draft=draft)
    interaction = _interaction()
    await view._on_open_modal(interaction)
    assert interaction.response.modals


@pytest.mark.asyncio
async def test_success_terminal_disables_controls(l10n) -> None:
    async def on_submit(view: SuggestionView, title: str, details: str) -> None:
        view.terminal = "success"
        view.status_text = view.t("commands.suggest.success", ticket_uid="T1")

    draft = _draft()
    draft.type_value = "bug"
    draft.category_values = ["ui"]
    view = _view(l10n, draft=draft, on_submit=on_submit)
    interaction = _interaction()
    await view.handle_modal_submit(interaction, title="Hello", details="World")
    assert view.terminal == "success"
    assert "T1" in (view.status_text or "")
    for item in view.walk_children():
        if hasattr(item, "disabled"):
            assert item.disabled is True


@pytest.mark.asyncio
async def test_modal_after_timeout_fails_closed(l10n) -> None:
    draft = _draft()
    draft.type_value = "bug"
    draft.category_values = ["ui"]
    view = _view(l10n, draft=draft)
    await view.on_timeout()
    interaction = _interaction()
    await view.handle_modal_submit(interaction, title="x", details="y")
    interaction.followup.send.assert_awaited()


@pytest.mark.asyncio
async def test_stores_values_not_labels(l10n) -> None:
    view = _view(l10n)
    await view._on_type(_interaction(values=["bug"]))
    await view._on_categories(_interaction(values=["ui"]))
    assert view.draft.type_value == "bug"
    assert "Bug" not in view.draft.category_values
