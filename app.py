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
WEB_ENABLED = os.getenv("WEB_ENABLED", "true").lower() == "true"
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "20000"))

if __name__ == "__main__":
    if not API_TOKEN:
        logger.critical("API_TOKEN environment variable is not set")
        raise RuntimeError("API_TOKEN environment variable is not set")
    
    logger.info("Starting Discord bot...")
    bot = DiscordBot()
    
    # Start web interface if enabled
    if WEB_ENABLED:
        try:
            from web.server import run_server_in_thread, get_server_url, setup_log_stream
            setup_log_stream(logger)
            logger.info("Starting web interface...")
            run_server_in_thread(bot_instance=bot, host=WEB_HOST, port=WEB_PORT)
            logger.info(f"Web interface started at: {get_server_url()}")
            logger.info(f"Dashboard: {get_server_url()}/dashboard")
            logger.info(f"Guilds: {get_server_url()}/guilds")
        except Exception as e:
            logger.error(f"Failed to start web interface: {e}", exc_info=True)
            logger.warning("Bot will continue without web interface")
    else:
        logger.info("Web interface is disabled (set WEB_ENABLED=true to enable)")
    
    # Run the bot (blocking call)
    bot.run(API_TOKEN)