from datetime import datetime
from typing import List, Literal, Optional, Set

from fastapi import APIRouter, Body, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from modules.LocalizationHandler import LocalizationHandler
from modules.utils import (
    find_suggestion_by_id,
    load_suggestions,
    update_suggestion_record,
    append_conversation_entry,
    ensure_ticket_metadata,
)

router = APIRouter(prefix="/api", tags=["suggestions"])

localization_handler = LocalizationHandler()


class SuggestionResponsePayload(BaseModel):
    mode: Literal["send", "done_no_feedback", "done_auto_feedback"] = Field(
        default="send", description="How to mark and respond to the suggestion."
    )
    response_text: Optional[str] = Field(
        default=None, description="Message to send back to the user (required for mode=send).", max_length=2000
    )


def _parse_created_at(suggestion: dict) -> datetime:
    created_at = suggestion.get("created_at")
    if isinstance(created_at, str):
        try:
            return datetime.fromisoformat(created_at)
        except ValueError:
            pass
    return datetime.fromtimestamp(0)


def _filter_by_type(suggestions: List[dict], suggestion_type: Optional[str]) -> List[dict]:
    if not suggestion_type:
        return suggestions
    return [
        s
        for s in suggestions
        if isinstance(s.get("type"), dict) and s["type"].get("value") == suggestion_type
    ]


def _filter_by_categories(suggestions: List[dict], categories_filter: Optional[str]) -> List[dict]:
    if not categories_filter:
        return suggestions
    requested: Set[str] = {segment.strip() for segment in categories_filter.split(",") if segment.strip()}
    if not requested:
        return suggestions

    filtered: List[dict] = []
    for suggestion in suggestions:
        categories = suggestion.get("categories") or []
        for category in categories:
            value = str(category.get("value", "")).strip()
            label = str(category.get("label", "")).strip()
            if value in requested or label in requested:
                filtered.append(suggestion)
                break
    return filtered


def _sort_suggestions(suggestions: List[dict], order: str) -> List[dict]:
    reverse = order != "old"
    return sorted(suggestions, key=_parse_created_at, reverse=reverse)


@router.get("/suggestions")
async def list_suggestions(
    suggestion_type: Optional[str] = Query(default=None, alias="type"),
    categories: Optional[str] = Query(default=None),
    order: str = Query(default="new", pattern="^(new|old)$"),
):
    suggestions = load_suggestions()
    suggestions = _filter_by_type(suggestions, suggestion_type)
    suggestions = _filter_by_categories(suggestions, categories)
    suggestions = _sort_suggestions(suggestions, order)
    return {"items": suggestions, "total": len(suggestions)}


@router.get("/suggestions/{suggestion_id}")
async def get_suggestion(suggestion_id: str):
    suggestion = find_suggestion_by_id(suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    return JSONResponse(content=suggestion)


async def _send_response_message(suggestion: dict, payload: SuggestionResponsePayload, content: str) -> bool:
    """
    Send a DM to the provided user ID through the bot bridge.
    Imported lazily to avoid circular imports when the server starts without the bot.
    """
    try:
        from web.bot_bridge import send_suggestion_response_dm  # Local import to avoid import cycles
    except ImportError:
        return False
    allow_followup = payload.mode == "send" and not suggestion.get("responded")
    return await send_suggestion_response_dm(suggestion, content, allow_followup=allow_followup)


def _resolve_auto_feedback_locale(suggestion: dict) -> str:
    locale_info = suggestion.get("locale") or {}
    for key in ("user", "stored", "guild"):
        locale = locale_info.get(key)
        if locale:
            if isinstance(locale, str) and "-" in locale:
                return locale.split("-")[0]
            return locale
    return "en"


async def _deliver_response(suggestion: dict, payload: SuggestionResponsePayload) -> tuple[bool, Optional[str], str]:
    user_info = suggestion.get("user") or {}
    user_id = user_info.get("id")
    if not user_id:
        return False, None, "missing_user"

    message_to_send: Optional[str] = None
    response_type = payload.mode

    if payload.mode == "send":
        if not payload.response_text:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="response_text is required for send mode")
        message_to_send = payload.response_text
        response_type = "manual"
    elif payload.mode == "done_auto_feedback":
        locale = _resolve_auto_feedback_locale(suggestion)
        message_to_send = localization_handler.t("commands.suggest.auto_feedback.default", locale=locale)
        response_type = "auto"
    elif payload.mode == "done_no_feedback":
        message_to_send = None
        response_type = "no_feedback"

    sent = False
    if message_to_send:
        sent = await _send_response_message(suggestion, payload, message_to_send)
    return sent, message_to_send, response_type


@router.post("/suggestions/{suggestion_id}/respond")
async def respond_to_suggestion(
    suggestion_id: str,
    payload: SuggestionResponsePayload = Body(...),
):
    suggestion = find_suggestion_by_id(suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")

    sent, message_text, response_type = await _deliver_response(suggestion, payload)
    event_time = datetime.utcnow().isoformat()

    def _update(entry: dict):
        ensure_ticket_metadata(entry)

        closing_mode = payload.mode in {"done_no_feedback", "done_auto_feedback"}

        if message_text:
            entry["response_text"] = message_text
        elif payload.response_text and payload.mode == "send":
            entry["response_text"] = payload.response_text

        entry["response_type"] = response_type
        entry["last_response_mode"] = payload.mode
        entry["responded"] = True if closing_mode else entry.get("responded", False)
        entry["response_given"] = entry.get("response_given", False) or sent

        if sent:
            entry["response_sent_at"] = event_time
        elif payload.mode == "done_no_feedback":
            entry["response_sent_at"] = entry.get("response_sent_at") or event_time

        convo_text = message_text
        if payload.mode == "done_no_feedback":
            convo_text = "Marked as done without sending a response."
        elif payload.mode == "done_auto_feedback" and not message_text:
            convo_text = "Marked as done with automatic feedback."

        if convo_text:
            append_conversation_entry(
                entry,
                author_role="staff",
                direction="outgoing",
                text=convo_text,
                metadata={
                    "mode": payload.mode,
                    "response_type": response_type,
                    "sent": sent,
                },
                created_at=event_time,
                source="web_panel",
            )

    updated = update_suggestion_record(suggestion_id, _update)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    return JSONResponse(content=updated)

