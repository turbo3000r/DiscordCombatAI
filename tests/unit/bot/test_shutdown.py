from __future__ import annotations

import pytest

from bot.modules.services.lifecycle import LifecycleController


@pytest.mark.asyncio
async def test_shutdown_order_journal() -> None:
    steps: list[str] = []

    async def authorize() -> None:
        return None

    async def revoke() -> None:
        steps.append("gateway_revoked")

    async def close_mqtt() -> None:
        steps.append("mqtt")

    async def close_transport() -> None:
        steps.append("transport")

    async def close_gateway() -> None:
        steps.append("gateway")

    async def close_azure() -> None:
        steps.append("azure")

    life = LifecycleController(
        node_id="node-local",
        drain_progress_interval_sec=5,
        shutdown_grace_sec=1,
        revoke_gateway=revoke,
        authorize_gateway=authorize,
    )
    journal = await life.run_shutdown(
        close_mqtt=close_mqtt,
        close_transport=close_transport,
        close_gateway=close_gateway,
        close_azure=close_azure,
        hard_stop=True,
    )
    assert journal == [
        "admissions_off",
        "hard_stop_complete",
        "timers_off",
        "mqtt_closed",
        "transport_closed",
        "gateway_closed",
        "azure_closed",
        "exit",
    ]
    assert steps == ["gateway_revoked", "mqtt", "transport", "gateway", "azure"]
