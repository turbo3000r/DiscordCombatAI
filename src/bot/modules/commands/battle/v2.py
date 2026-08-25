"""Components V2 send/edit helpers for /quick-battle.

Discord rejects ``content=`` on messages that use ``MessageFlags.IS_COMPONENTS_V2``.
Visible text on a ``LayoutView`` lives in ``TextDisplay`` items, not the classic
content field. Trivial non-V2 messages (story chunks, winner line) may still use
``content=`` when no layout view is attached.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import ui

_SHORT_DENIAL_KEYS = {
    "not_owner": "errors.denied_permission",
    "not_participant": "errors.denied_permission",
    "owner_cannot_leave": "errors.denied_permission",
    "lobby_closed": "commands.quick-battle.session_expired",
    "lobby_full": "commands.quick-battle.denied_busy_user",
    "wrong_phase": "commands.quick-battle.session_expired",
    "session_expired": "commands.quick-battle.session_expired",
}


class StatusNoticeView(ui.LayoutView):
    """Replacement Components V2 tree for terminal/status edits."""

    def __init__(self, text: str) -> None:
        super().__init__(timeout=None)
        self.add_item(ui.TextDisplay(content=text))


def is_layout_view(view: Any | None) -> bool:
    return isinstance(view, ui.LayoutView)


def localization_key_for_denial(code: str) -> str:
    if code.startswith("commands.") or code.startswith("errors."):
        return code
    return _SHORT_DENIAL_KEYS.get(code, "errors.error_unexpected")


def send_kwargs(
    *,
    content: str,
    view: Any | None,
    allowed_mentions: discord.AllowedMentions,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"allowed_mentions": allowed_mentions}
    if is_layout_view(view):
        payload["view"] = view
        return payload
    payload["content"] = content
    if view is not None:
        payload["view"] = view
    return payload


def edit_kwargs(
    *,
    content: str,
    view: Any | None,
    disable: bool,
    allowed_mentions: discord.AllowedMentions,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"allowed_mentions": allowed_mentions}
    if disable or view is None:
        payload["view"] = StatusNoticeView(content)
        return payload
    if is_layout_view(view):
        payload["view"] = view
        return payload
    payload["content"] = content
    payload["view"] = view
    return payload


__all__ = [
    "StatusNoticeView",
    "edit_kwargs",
    "is_layout_view",
    "localization_key_for_denial",
    "send_kwargs",
]
