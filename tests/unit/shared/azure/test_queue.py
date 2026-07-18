from __future__ import annotations

import pytest

from shared.azure.clients.queue import QueueClient


class _Message:
    def __init__(self, message_id: str = "m1", pop_receipt: str = "r1") -> None:
        self.id = message_id
        self.pop_receipt = pop_receipt


class _Queue:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.deleted: list[tuple[str, str]] = []

    def send_message(self, text: str):
        self.sent.append(text)
        return None

    def receive_messages(self, *, messages_per_page: int, visibility_timeout: int):
        assert messages_per_page == 1
        assert visibility_timeout == 60
        return [_Message()]

    def delete_message(self, message_id: str, pop_receipt: str):
        self.deleted.append((message_id, pop_receipt))
        return None


class _Service:
    def __init__(self) -> None:
        self.queue = _Queue()

    def get_queue_client(self, queue_name: str) -> _Queue:
        assert queue_name == "suggestions"
        return self.queue


@pytest.mark.asyncio()
async def test_queue_client_round_trips(azure_settings, fake_credential) -> None:
    service = _Service()
    client = QueueClient(
        service="bot",
        service_client=service,
        settings=azure_settings,
        credential=fake_credential,
    )

    await client.send_message("suggestions", "payload")
    messages = await client.receive_messages("suggestions")
    await client.delete_message("suggestions", messages[0])

    assert service.queue.sent == ["payload"]
    assert service.queue.deleted == [("m1", "r1")]
    assert QueueClient.should_skip_poll_failure(RuntimeError("boom")) is True
