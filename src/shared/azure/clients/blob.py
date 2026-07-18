from __future__ import annotations

from typing import Any

from shared.azure._helpers import maybe_await
from shared.azure.configs.credential import get_credential
from shared.azure.configs.settings import AzureServiceName, AzureSettings, load_azure_settings
from shared.azure.lifecycle import register_resource
from shared.utils.retry import RetryCategory, retry_async


def _is_blob_not_found(exc: BaseException) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 404:
        return True
    message = str(exc).lower()
    return "blobnotfound" in message or "not found" in message or "404" in message


class BlobClient:
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
                from azure.storage.blob.aio import BlobServiceClient
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("azure-storage-blob is not available") from exc
            self._service_client = BlobServiceClient(
                account_url=self.settings.blob_endpoint,
                credential=self.credential,
                retry_total=0,
            )
            register_resource(self._service_client)
        return self._service_client

    def _blob(self, container_name: str, blob_name: str) -> Any:
        return self._client().get_blob_client(container=container_name, blob=blob_name)

    async def read_text(self, container_name: str, blob_name: str) -> tuple[str, str | None]:
        async def _read() -> tuple[str, str | None]:
            blob = self._blob(container_name, blob_name)
            stream = blob.download_blob(timeout=15)
            stream = await maybe_await(stream)
            body = stream.readall()
            body = await maybe_await(body)
            etag = getattr(stream, "etag", None) or getattr(
                getattr(stream, "properties", None), "etag", None
            )
            return body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body), etag

        return await retry_async(_read, category=RetryCategory.SAFE_READ)

    async def write_text(
        self,
        container_name: str,
        blob_name: str,
        payload: str,
        *,
        etag: str | None = None,
        create_only: bool = False,
    ) -> None:
        async def _write() -> None:
            blob = self._blob(container_name, blob_name)
            kwargs: dict[str, Any] = {"overwrite": not create_only}
            if create_only:
                kwargs["if_none_match"] = "*"
            elif etag is not None:
                try:
                    from azure.core import MatchConditions

                    match_condition: Any = MatchConditions.IfNotModified
                except Exception:
                    match_condition = "IfNotModified"
                kwargs.update({"etag": etag, "match_condition": match_condition})
            result = blob.upload_blob(payload.encode("utf-8"), **kwargs)
            await maybe_await(result)

        await _write()

    async def append_text(self, container_name: str, blob_name: str, payload: str) -> None:
        async def _append() -> None:
            blob = self._blob(container_name, blob_name)
            try:
                result = blob.append_block(payload.encode("utf-8"))
                await maybe_await(result)
            except Exception as exc:
                if not _is_blob_not_found(exc):
                    raise
                create_result = blob.create_append_blob()
                await maybe_await(create_result)
                result = blob.append_block(payload.encode("utf-8"))
                await maybe_await(result)

        await retry_async(_append, category=RetryCategory.APPEND_BLOB)

    async def acquire_lease(
        self, container_name: str, blob_name: str, *, lease_duration: int = 15
    ) -> Any:
        async def _acquire() -> Any:
            blob = self._blob(container_name, blob_name)
            result = blob.acquire_lease(lease_duration=lease_duration)
            return await maybe_await(result)

        return await retry_async(_acquire, category=RetryCategory.LEASE_FAIL_FAST)

    async def renew_lease(self, container_name: str, blob_name: str, lease: Any) -> Any:
        blob = self._blob(container_name, blob_name)
        result = lease.renew() if hasattr(lease, "renew") else blob.renew_lease(lease=lease)
        return await maybe_await(result)

    async def release_lease(self, container_name: str, blob_name: str, lease: Any) -> Any:
        blob = self._blob(container_name, blob_name)
        result = lease.release() if hasattr(lease, "release") else blob.release_lease(lease=lease)
        return await maybe_await(result)

    async def close(self) -> None:
        if self._service_client is None:
            return
        close = getattr(self._service_client, "aclose", None) or getattr(
            self._service_client, "close", None
        )
        if close is not None:
            result = close()
            await maybe_await(result)
