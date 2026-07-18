from __future__ import annotations

from typing import Any

from shared.azure._helpers import maybe_await
from shared.azure.configs.credential import get_credential
from shared.azure.configs.settings import AzureServiceName, AzureSettings, load_azure_settings
from shared.azure.lifecycle import register_resource
from shared.utils.retry import RetryCategory, retry_async


class QueueClient:
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
                from azure.storage.queue.aio import QueueServiceClient
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("azure-storage-queue is not available") from exc
            self._service_client = QueueServiceClient(
                account_url=self.settings.queue_endpoint, credential=self.credential, retry_total=0
            )
            register_resource(self._service_client)
        return self._service_client

    def _queue(self, queue_name: str) -> Any:
        return self._client().get_queue_client(queue_name)

    async def send_message(self, queue_name: str, message_text: str) -> None:
        async def _send() -> None:
            queue = self._queue(queue_name)
            result = queue.send_message(message_text)
            await maybe_await(result)

        await retry_async(_send, category=RetryCategory.UNCERTAIN_SEND)

    async def receive_messages(
        self, queue_name: str, *, visibility_timeout: int = 60, max_messages: int = 1
    ) -> list[Any]:
        async def _receive() -> list[Any]:
            queue = self._queue(queue_name)
            result = queue.receive_messages(
                messages_per_page=max_messages, visibility_timeout=visibility_timeout
            )
            messages = await maybe_await(result)
            return list(messages)

        return await retry_async(_receive, category=RetryCategory.SAFE_READ)

    async def delete_message(self, queue_name: str, message: Any) -> None:
        async def _delete() -> None:
            queue = self._queue(queue_name)
            result = queue.delete_message(message.id, message.pop_receipt)
            await maybe_await(result)

        await retry_async(_delete, category=RetryCategory.UNCERTAIN_SEND)

    @staticmethod
    def should_skip_poll_failure(_exc: BaseException | None = None) -> bool:
        return True

    async def close(self) -> None:
        if self._service_client is None:
            return
        close = getattr(self._service_client, "aclose", None) or getattr(
            self._service_client, "close", None
        )
        if close is not None:
            result = close()
            await maybe_await(result)
