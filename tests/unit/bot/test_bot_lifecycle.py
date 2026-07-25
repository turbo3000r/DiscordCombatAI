from __future__ import annotations

import pytest

from bot.modules.services.lifecycle import LifecycleController


@pytest.mark.asyncio
async def test_lifecycle_activation_and_stops() -> None:
    gate: list[str] = []

    async def authorize() -> None:
        gate.append("up")

    async def revoke() -> None:
        gate.append("down")

    life = LifecycleController(
        node_id="node-local",
        drain_progress_interval_sec=5,
        shutdown_grace_sec=1,
        revoke_gateway=revoke,
        authorize_gateway=authorize,
    )
    await life.on_activate()
    assert life.accepting_ai_work is True
    await life.on_soft_stop()
    assert life.accepting_ai_work is False
    assert life.soft_stop_count == 1
    await life.on_hard_stop()
    assert life.hard_stop_count == 1
    assert gate == ["up", "down"]
    # Idempotent hard-stop
    await life.on_hard_stop()
    assert life.hard_stop_count == 1
