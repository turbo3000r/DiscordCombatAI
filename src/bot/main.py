from __future__ import annotations

import asyncio
from contextlib import suppress

from .application import BotApplication
from .settings import BotSettings


async def async_main() -> None:
    settings = BotSettings()  # type: ignore[call-arg]
    application = BotApplication(settings, enable_mqtt=True, enable_transport=True)
    try:
        await application.start()
        await asyncio.Event().wait()
    finally:
        await application.close()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(async_main())


if __name__ == "__main__":
    main()
