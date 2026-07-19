from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from shared.azure._helpers import maybe_await
from shared.azure.configs.credential import get_credential
from shared.azure.configs.settings import AzureServiceName, AzureSettings, load_azure_settings
from shared.azure.lifecycle import register_resource
from shared.models import PubSubNegotiateResponse
from shared.utils.retry import RetryCategory, retry_async


class PubSubClient:
    def __init__(
        self,
        *,
        service: AzureServiceName,
        service_client: Any | None = None,
        settings: AzureSettings | None = None,
        credential: Any | None = None,
    ) -> None:
        self.service = service
        self.settings = settings or load_azure_settings(service)
        self.credential = credential or get_credential(service, self.settings)
        self._service_client = service_client
        register_resource(self.credential)
        if self._service_client is not None:
            register_resource(self._service_client)

    def _client(self) -> Any:
        if self._service_client is None:
            try:
                from azure.messaging.webpubsubservice.aio import WebPubSubServiceClient
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("azure-messaging-webpubsubservice is not available") from exc
            self._service_client = WebPubSubServiceClient(
                endpoint=self.settings.azure_webpubsub_endpoint,
                hub=self.settings.azure_webpubsub_hub_name,
                credential=self.credential,
                retry_total=0,
            )
            register_resource(self._service_client)
        return self._service_client

    async def send_to_group(
        self, group: str, message: Any, *, content_type: str = "application/json"
    ) -> None:
        async def _send() -> None:
            client = self._client()
            result = client.send_to_group(group=group, content=message, content_type=content_type)
            await maybe_await(result)

        await retry_async(_send, category=RetryCategory.UNCERTAIN_SEND)

    async def get_client_access_token(
        self,
        *,
        group: str,
        user_id: str,
        expires_in_minutes: int = 60,
        allow_send: bool = False,
    ) -> PubSubNegotiateResponse:
        async def _mint() -> PubSubNegotiateResponse:
            client = self._client()
            roles = [f"webpubsub.joinLeaveGroup.{group}"]
            if allow_send:
                roles.append(f"webpubsub.sendToGroup.{group}")
            result = client.get_client_access_token(
                user_id=user_id, roles=roles, minutes_to_expire=expires_in_minutes
            )
            token = await maybe_await(result)
            url = getattr(token, "url", None) or token.get("url")
            expires_at = datetime.now(UTC) + timedelta(minutes=expires_in_minutes)
            return PubSubNegotiateResponse(url=url, expires_at=expires_at, group=group)

        return await retry_async(_mint, category=RetryCategory.SAFE_READ)

    async def close(self) -> None:
        if self._service_client is None:
            return
        close = getattr(self._service_client, "aclose", None) or getattr(
            self._service_client, "close", None
        )
        if close is not None:
            result = close()
            await maybe_await(result)
