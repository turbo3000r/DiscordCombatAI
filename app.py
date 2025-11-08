import os
import sys
import asyncio
from dotenv import load_dotenv
from modules.main import DiscordBot
from modules.LoggerHandler import init_logger


if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Initialize logger
logger = init_logger("logger_config.json").get_logger()

load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
if __name__ == "__main__":
    if not API_TOKEN:
        logger.critical("API_TOKEN environment variable is not set")
        raise RuntimeError("API_TOKEN environment variable is not set")
    logger.info("Starting Discord bot...")
    bot = DiscordBot()
    bot.run(API_TOKEN)