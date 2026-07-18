from __future__ import annotations

import pytest

from shared.azure.clients.pubsub import PubSubClient


class _Service:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object, str]] = []
        self.tokens: list[tuple[str, list[str], int]] = []

    def send_to_group(self, *, group: str, content: object, content_type: str):
        self.sent.append((group, content, content_type))
        return None

    def get_client_access_token(self, *, user_id: str, roles: list[str], minutes_to_expire: int):
        self.tokens.append((user_id, roles, minutes_to_expire))
        return {"url": "wss://example?token=1"}


@pytest.mark.asyncio()
async def test_pubsub_client_token_minting(azure_settings, fake_credential) -> None:
    service = _Service()
    client = PubSubClient(
        service="web",
        service_client=service,
        settings=azure_settings,
        credential=fake_credential,
    )

    await client.send_to_group("dashboard-live", {"ok": True})
    token = await client.get_client_access_token(group="dashboard-live", user_id="oid-1")

    assert service.sent[0][0] == "dashboard-live"
    assert service.tokens[0][1] == ["webpubsub.joinLeaveGroup.dashboard-live"]
    assert token.group == "dashboard-live"
    assert token.url.startswith("wss://example")
