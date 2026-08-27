"""Components V2 messenger must not send classic content with LayoutView."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import discord
import pytest
from discord import ui

from bot.modules.commands.battle.command import InteractionMessenger
from bot.modules.commands.battle.v2 import (
    StatusNoticeView,
    edit_kwargs,
    localization_key_for_denial,
    send_kwargs,
)


def _mentions() -> discord.AllowedMentions:
    return discord.AllowedMentions.none()


def test_progress_checklist_marks_reached_phases() -> None:
    from bot.modules.commands.battle.UI.views import progress_checklist
    from shared.models import TaskPhase

    text = progress_checklist(lambda key: key.split(".")[-1], TaskPhase.composing)
    assert "✅ phase_queued" in text
    assert "✅ phase_launching" in text
    assert "✅ phase_composing" in text
    assert "⬜ phase_refining" in text
    assert "⬜ phase_finishing" in text


def test_layout_send_omits_content() -> None:
    view = ui.LayoutView()
    view.add_item(ui.TextDisplay(content="lobby"))
    payload = send_kwargs(content="must-not-appear", view=view, allowed_mentions=_mentions())
    assert "content" not in payload
    assert payload["view"] is view


def test_plain_send_keeps_content() -> None:
    payload = send_kwargs(content="story chunk", view=None, allowed_mentions=_mentions())
    assert payload["content"] == "story chunk"
    assert "view" not in payload


def test_terminal_edit_replaces_layout_without_content() -> None:
    payload = edit_kwargs(
        content="The battle was aborted.",
        view=None,
        disable=True,
        allowed_mentions=_mentions(),
    )
    assert "content" not in payload
    assert isinstance(payload["view"], StatusNoticeView)


def test_denial_keys_map_short_codes() -> None:
    assert localization_key_for_denial("not_owner") == "errors.denied_permission"
    assert (
        localization_key_for_denial("commands.quick-battle.denied_busy_guild")
        == "commands.quick-battle.denied_busy_guild"
    )


@pytest.mark.asyncio
async def test_interaction_messenger_layout_send_and_edit() -> None:
    recorded: list[dict[str, Any]] = []

    class Channel:
        async def send(self, **kwargs: Any) -> SimpleNamespace:
            recorded.append(kwargs)
            return SimpleNamespace(id=99)

        def get_partial_message(self, message_id: int) -> Any:
            class Partial:
                async def edit(self, **kwargs: Any) -> None:
                    recorded.append({"edit_id": message_id, **kwargs})

            return Partial()

    messenger = InteractionMessenger(SimpleNamespace(channel=Channel()))
    view = ui.LayoutView()
    view.add_item(ui.TextDisplay(content="lobby"))
    message_id = await messenger.send("must-not-send", view=view)
    assert message_id == "99"
    assert "content" not in recorded[0]
    await messenger.edit("99", "The battle was aborted.", disable=True)
    assert "content" not in recorded[1]
    assert isinstance(recorded[1]["view"], StatusNoticeView)
