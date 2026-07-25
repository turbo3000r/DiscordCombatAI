from __future__ import annotations

import asyncio
from contextlib import suppress

from .composition import build_bot_application, prepare_bot_application


async def async_main() -> None:
    application = build_bot_application(enable_mqtt=True, enable_transport=True)
    await prepare_bot_application(application)
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
