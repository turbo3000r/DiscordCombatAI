from __future__ import annotations

import asyncio
import threading

import pytest

from bot.modules.services.lifecycle import handoff_to_loop


@pytest.mark.asyncio
async def test_mqtt_callback_handoff_mutates_only_on_loop() -> None:
    loop = asyncio.get_running_loop()
    owner_id = threading.get_ident()
    seen: list[int] = []

    async def mutate() -> None:
        seen.append(threading.get_ident())

    done = threading.Event()

    def foreign_callback() -> None:
        future = asyncio.run_coroutine_threadsafe(mutate(), loop)
        future.result(timeout=2)
        done.set()

    thread = threading.Thread(target=foreign_callback)
    thread.start()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if done.is_set():
            break
    thread.join(timeout=2)
    assert seen == [owner_id]


@pytest.mark.asyncio
async def test_handoff_helper() -> None:
    loop = asyncio.get_running_loop()
    flag = {"ok": False}

    async def set_flag() -> None:
        flag["ok"] = True

    await handoff_to_loop(loop, set_flag())
    assert flag["ok"] is True
