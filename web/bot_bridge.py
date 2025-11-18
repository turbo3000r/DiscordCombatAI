"""
Bridge utilities to let the FastAPI server interact with the Discord bot instance.
"""
import asyncio
from typing import Optional

from discord.ext import commands

_bot_instance: Optional[commands.Bot] = None


def set_bot(bot: Optional[commands.Bot]) -> None:
    """Store a reference to the running bot for later use by the web server."""
    global _bot_instance
    _bot_instance = bot


def get_bot() -> Optional[commands.Bot]:
    """Return the stored bot instance, if any."""
    return _bot_instance


async def send_user_dm(user_id: int, content: str) -> bool:
    """
    Send a DM to the given user by scheduling the coroutine on the bot's loop.

    Returns:
        bool: True if the message was sent, False otherwise.
    """
    bot = get_bot()
    if bot is None or bot.user is None:
        return False

    loop = bot.loop
    if loop is None or not loop.is_running():
        return False

    async def _send():
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except Exception:
                return False
        if user is None:
            return False
        try:
            await user.send(content)
            return True
        except Exception:
            return False

    thread_safe_future = asyncio.run_coroutine_threadsafe(_send(), loop)
    try:
        wrapped = asyncio.wrap_future(thread_safe_future)
        return await asyncio.wait_for(wrapped, timeout=10)
    except asyncio.TimeoutError:
        thread_safe_future.cancel()
        return False
    except Exception:
        return False

