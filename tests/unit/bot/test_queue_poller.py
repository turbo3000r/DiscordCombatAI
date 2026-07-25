"""Unit tests for suggestion queue poller."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.commands.suggestions.service.notification_delivery import (
    DeliveryOutcome,
    NotificationDeliveryService,
)
from bot.modules.commands.suggestions.service.queue_poller import SuggestionQueuePoller
from shared.models import SuggestionQueueMessage


class FakeHealth:
    def __init__(self) -> None:
        self.azure_queue_ok = True


class FakeQueue:
    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.deleted: list[Any] = []
        self.poison: list[str] = []
        self.fail_receive = 0

    async def receive_messages(
        self, queue_name: str, *, visibility_timeout: int = 60, max_messages: int = 1
    ) -> list[Any]:
        if self.fail_receive > 0:
            self.fail_receive -= 1
            raise RuntimeError("queue down")
        batch = self.messages[:max_messages]
        self.messages = self.messages[max_messages:]
        return batch

    async def delete_message(self, queue_name: str, message: Any) -> None:
        self.deleted.append(message)

    async def send_message(self, queue_name: str, message_text: str) -> None:
        self.poison.append(message_text)


class FakeDelivery:
    def __init__(self, outcomes: list[DeliveryOutcome] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.outcomes = list(outcomes or [])

    async def deliver(self, *, guild_id: str, suggestion_id: str) -> DeliveryOutcome:
        self.calls.append((guild_id, suggestion_id))
        if self.outcomes:
            return self.outcomes.pop(0)
        return DeliveryOutcome("sent")


def _queue_message(**overrides: Any) -> SuggestionQueueMessage:
    payload = {
        "id": str(uuid4()),
        "ticket_uid": "SUG-11223344",
        "guild_id": "999",
        "enqueued_at": datetime.now(tz=UTC),
    }
    payload.update(overrides)
    return SuggestionQueueMessage.model_validate(payload)


@pytest.mark.asyncio
async def test_poller_deletes_only_after_sent() -> None:
    msg = _queue_message()
    queue = FakeQueue()
    queue.messages.append(
        SimpleNamespace(content=msg.model_dump_json(), dequeue_count=1)
    )
    delivery = FakeDelivery([DeliveryOutcome("sent")])
    health = FakeHealth()
    poller = SuggestionQueuePoller(
        queue_client=queue,
        queue_name="suggestions",
        delivery=delivery,  # type: ignore[arg-type]
        health=health,
        poll_interval_sec=300,
        accepting=lambda: True,
    )
    assert await poller.poll_once() == 1
    assert delivery.calls == [("999", str(msg.id))]
    assert len(queue.deleted) == 1


@pytest.mark.asyncio
async def test_poller_duplicate_already_sent_deletes_without_second_path() -> None:
    msg = _queue_message()
    queue = FakeQueue()
    queue.messages.append(
        SimpleNamespace(content=msg.model_dump_json(), dequeue_count=2)
    )
    delivery = FakeDelivery([DeliveryOutcome("already_sent")])
    poller = SuggestionQueuePoller(
        queue_client=queue,
        queue_name="suggestions",
        delivery=delivery,  # type: ignore[arg-type]
        health=FakeHealth(),
        poll_interval_sec=300,
        accepting=lambda: True,
    )
    await poller.poll_once()
    assert len(queue.deleted) == 1


@pytest.mark.asyncio
async def test_poller_receive_failure_threshold_sets_health_false_then_reset() -> None:
    queue = FakeQueue()
    queue.fail_receive = 3
    health = FakeHealth()
    poller = SuggestionQueuePoller(
        queue_client=queue,
        queue_name="suggestions",
        delivery=FakeDelivery(),  # type: ignore[arg-type]
        health=health,
        poll_interval_sec=300,
        accepting=lambda: True,
    )
    for _ in range(3):
        assert await poller.poll_once() == 0
    assert health.azure_queue_ok is False
    assert await poller.poll_once() == 0
    assert health.azure_queue_ok is True


@pytest.mark.asyncio
async def test_poller_poisons_after_five_dequeues() -> None:
    queue = FakeQueue()
    queue.messages.append(SimpleNamespace(content="{not-json", dequeue_count=5))
    poller = SuggestionQueuePoller(
        queue_client=queue,
        queue_name="suggestions",
        delivery=FakeDelivery(),  # type: ignore[arg-type]
        health=FakeHealth(),
        poll_interval_sec=300,
        accepting=lambda: True,
    )
    await poller.poll_once()
    assert queue.poison
    assert queue.deleted


@pytest.mark.asyncio
async def test_poller_skips_when_claims_disabled() -> None:
    queue = FakeQueue()
    queue.messages.append(SimpleNamespace(content="{}", dequeue_count=1))
    delivery = FakeDelivery()
    poller = SuggestionQueuePoller(
        queue_client=queue,
        queue_name="suggestions",
        delivery=delivery,  # type: ignore[arg-type]
        health=FakeHealth(),
        poll_interval_sec=300,
        accepting=lambda: False,
    )
    assert await poller.poll_once() == 0
    assert not delivery.calls


# Satisfy import for type checkers / unused reference in annotations.
_ = NotificationDeliveryService
