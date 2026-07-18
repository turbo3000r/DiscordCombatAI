from __future__ import annotations

import pytest

from shared.azure.clients.blob import BlobClient


class _Download:
    def __init__(self, payload: bytes, etag: str) -> None:
        self._payload = payload
        self.etag = etag

    def readall(self) -> bytes:
        return self._payload


class _Lease:
    def __init__(self) -> None:
        self.renewed = False
        self.released = False

    def renew(self) -> str:
        self.renewed = True
        return "renewed"

    def release(self) -> str:
        self.released = True
        return "released"


class _Blob:
    def __init__(self) -> None:
        self.uploads: list[tuple[bytes, dict]] = []
        self.appends: list[bytes] = []
        self.created_append = False
        self.download_payload = b"hello"
        self.etag = "etag-1"
        self.lease = _Lease()

    def download_blob(self, timeout: int) -> _Download:
        assert timeout == 15
        return _Download(self.download_payload, self.etag)

    def upload_blob(self, payload: bytes, **kwargs):
        self.uploads.append((payload, kwargs))
        return None

    def create_append_blob(self):
        self.created_append = True
        return None

    def append_block(self, payload: bytes):
        if not self.created_append and not self.appends:
            raise RuntimeError("BlobNotFound: the specified blob does not exist")
        self.appends.append(payload)
        return None

    def acquire_lease(self, lease_duration: int):
        assert lease_duration == 15
        return self.lease

    def renew_lease(self, lease: object):
        return "renewed-direct"

    def release_lease(self, lease: object):
        return "released-direct"


class _Service:
    def __init__(self) -> None:
        self.blobs: dict[tuple[str, str], _Blob] = {}

    def get_blob_client(self, container: str, blob: str) -> _Blob:
        return self.blobs.setdefault((container, blob), _Blob())


@pytest.mark.asyncio()
async def test_blob_client_round_trips(azure_settings, fake_credential) -> None:
    service = _Service()
    client = BlobClient(
        service="head",
        service_client=service,
        settings=azure_settings,
        credential=fake_credential,
    )

    text, etag = await client.read_text("container", "blob.txt")
    assert text == "hello"
    assert etag == "etag-1"

    await client.write_text("container", "blob.txt", "payload", etag="etag-1")
    await client.append_text("container", "new.log", "more")
    blob = service.blobs[("container", "new.log")]
    assert blob.created_append is True
    assert blob.appends == [b"more"]
    lease = await client.acquire_lease("container", "blob.txt")
    assert lease.renew() == "renewed"
    assert lease.release() == "released"
