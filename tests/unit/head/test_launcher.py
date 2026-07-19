from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import pytest
from helpers import FakeClock

from head.launcher import (
    ClientResponse,
    LauncherClient,
    LauncherRequestError,
)
from shared.models import UpdateReason


class FakeHttpTransport:
    def __init__(self, outcomes: list[ClientResponse | BaseException]) -> None:
        self.outcomes = outcomes
        self.requests: list[tuple[Mapping[str, str], bytes]] = []
        self.closed = 0

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        connect_timeout_sec: float,
        response_timeout_sec: float,
    ) -> ClientResponse:
        self.requests.append((headers, body))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed += 1


def _accepted() -> ClientResponse:
    return ClientResponse(
        status=202,
        body=(
            b'{"schema_version":1,'
            b'"request_id":"11111111-1111-4111-8111-111111111111",'
            b'"operation_id":"22222222-2222-4222-8222-222222222222",'
            b'"state":"accepted","target_version":"v1.1.0"}'
        ),
    )


@pytest.mark.asyncio
async def test_unknown_outcome_retries_identical_body_and_request_id() -> None:
    transport = FakeHttpTransport([TimeoutError("unknown outcome"), _accepted()])
    client = LauncherClient(
        base_url="http://launcher:9700",
        secret=b"x" * 32,
        source_node_id="node-a",
        transport=transport,
        clock=FakeClock(),
        uuid_factory=lambda: UUID("11111111-1111-4111-8111-111111111111"),
        jitter=lambda _: 0,
    )

    result = await client.request_update("v1.1.0", reason=UpdateReason.auto_detected)

    assert result.target_version == "v1.1.0"
    assert len(transport.requests) == 2
    assert transport.requests[0][1] == transport.requests[1][1]
    assert (
        transport.requests[0][0]["X-DCA-Request-ID"]
        == transport.requests[1][0]["X-DCA-Request-ID"]
    )
    assert transport.requests[0][0]["X-DCA-Signature"] == transport.requests[1][0][
        "X-DCA-Signature"
    ]


@pytest.mark.asyncio
async def test_non_retryable_launcher_response_stops_immediately() -> None:
    transport = FakeHttpTransport([ClientResponse(status=409, body=b"{}"), _accepted()])
    client = LauncherClient(
        base_url="http://launcher:9700",
        secret=b"x" * 32,
        source_node_id="node-a",
        transport=transport,
        clock=FakeClock(),
        uuid_factory=lambda: UUID("11111111-1111-4111-8111-111111111111"),
    )

    with pytest.raises(LauncherRequestError) as raised:
        await client.request_update("v1.1.0")

    assert raised.value.status == 409
    assert len(transport.requests) == 1
