"""
FastAPI web server for Discord bot monitoring interface.
"""
import os
import json
import threading
import asyncio
import logging
import atexit
from contextlib import asynccontextmanager, suppress
from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from web.routes import dashboard, guilds, webhook, suggestions
from web.bot_bridge import set_bot
from web.metrics import init_metrics, get_metrics, stop_metrics


log_subscribers: List[WebSocket] = []
log_queue: Optional["asyncio.Queue[str]"] = None
_log_loop: Optional[asyncio.AbstractEventLoop] = None
_log_handler: Optional["WebSocketLogHandler"] = None
_log_broadcaster_task: Optional[asyncio.Task] = None


class WebSocketLogHandler(logging.Handler):
    """Logging handler that forwards records to the websocket broadcaster."""

    def emit(self, record: logging.LogRecord) -> None:
        if _log_loop is None or log_queue is None:
            return
        try:
            msg = self.format(record)
            _log_loop.call_soon_threadsafe(log_queue.put_nowait, msg)
        except Exception:
            pass


def attach_log_stream(logger: logging.Logger) -> None:
    """Attach websocket log handler to provided logger (idempotent)."""
    global _log_handler
    if _log_handler is not None:
        return
    handler = WebSocketLogHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "[%(asctime)s][%(name)s][%(levelname)s][%(guild)s]: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    _log_handler = handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _log_loop, log_queue, _log_broadcaster_task
    _log_loop = asyncio.get_running_loop()
    log_queue = asyncio.Queue()
    _log_broadcaster_task = asyncio.create_task(log_broadcaster())
    try:
        yield
    finally:
        if _log_broadcaster_task:
            _log_broadcaster_task.cancel()
            with suppress(asyncio.CancelledError):
                await _log_broadcaster_task
            _log_broadcaster_task = None
        stop_metrics()


# Create FastAPI app
app = FastAPI(
    title="Discord Combat AI Bot - Web Interface",
    description="Web interface for monitoring and managing the Discord Combat AI Bot",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(dashboard.router)
app.include_router(guilds.router)
app.include_router(webhook.router)
app.include_router(suggestions.router)

# Get the directory where this file is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# Page routes
@app.get("/")
async def home():
    """Serve the home page."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/dashboard")
async def dashboard_page():
    """Serve the dashboard page."""
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))


@app.get("/guilds")
async def guilds_page():
    """Serve the guilds page."""
    return FileResponse(os.path.join(STATIC_DIR, "guilds.html"))


@app.get("/performance")
async def performance_page():
    """Serve the performance page."""
    return FileResponse(os.path.join(STATIC_DIR, "performance.html"))


@app.get("/webhook")
async def webhook_page():
    """Serve the webhook management page."""
    return FileResponse(os.path.join(STATIC_DIR, "webhook.html"))


@app.get("/suggestions")
async def suggestions_page():
    """Serve the suggestions review page."""
    return FileResponse(os.path.join(STATIC_DIR, "suggestions.html"))


# Health check endpoint
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "Discord Combat AI Bot Web Interface"}


@app.websocket("/ws/logs")
async def logs_websocket(websocket: WebSocket):
    await websocket.accept()
    log_subscribers.append(websocket)
    try:
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        if websocket in log_subscribers:
            log_subscribers.remove(websocket)
    except Exception:
        if websocket in log_subscribers:
            log_subscribers.remove(websocket)


async def log_broadcaster():
    while True:
        if log_queue is None:
            await asyncio.sleep(0.1)
            continue
        msg = await log_queue.get()
        dead: List[WebSocket] = []
        for ws in log_subscribers:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in log_subscribers:
                log_subscribers.remove(ws)


# Bot info endpoint
@app.get("/api/bot/info")
async def get_bot_info():
    """Get bot information from bot.json."""
    try:
        bot_config_path = "bot.json"
        if os.path.exists(bot_config_path):
            with open(bot_config_path, "r", encoding="utf-8") as f:
                bot_info = json.load(f)
            return JSONResponse(content=bot_info)
        else:
            # Return default values if file doesn't exist
            return JSONResponse(content={
                "name": "Discord Combat AI Bot",
                "description": "AI-powered combat bot for Discord",
                "id": "",
                "invite_link": "",
                "version": "1.0.0"
            })
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to read bot info: {str(e)}"}
        )


# Global server thread reference
_server_thread: Optional[threading.Thread] = None
_server_config = {
    "host": "0.0.0.0",
    "port": 8000
}


def setup_log_stream(logger: logging.Logger) -> None:
    """Expose log streaming attachment to callers (e.g., app.py)."""
    attach_log_stream(logger)


def run_server_in_thread(bot_instance=None, host: str = "0.0.0.0", port: int = 8000, log_level: str = "info"):
    """
    Run the FastAPI server in a separate thread.
    
    Args:
        bot_instance: The Discord bot instance for metrics collection
        host: Host to bind to (default: 0.0.0.0)
        port: Port to bind to (default: 8000)
        log_level: Uvicorn log level (default: info)
    """
    global _server_thread, _server_config
    
    # Initialize metrics with bot instance
    init_metrics(bot_instance)
    set_bot(bot_instance)
    
    # Store config
    _server_config["host"] = host
    _server_config["port"] = port
    
    def run_server():
        """Internal function to run uvicorn."""
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level=log_level,
            access_log=False  # Disable access logs to reduce noise
        )
    
    # Create and start thread
    _server_thread = threading.Thread(target=run_server, daemon=True, name="WebServer")
    _server_thread.start()
    
    return _server_thread


def get_server_url() -> str:
    """Get the URL where the server is running."""
    host = _server_config["host"]
    port = _server_config["port"]
    # If host is 0.0.0.0, show localhost in URL
    display_host = "localhost" if host == "0.0.0.0" else host
    return f"http://{display_host}:{port}"


# Standalone mode support (for future separation)
def run_standalone(host: str = "0.0.0.0", port: int = 8000):
    """
    Run the server in standalone mode (separate process).
    In this mode, metrics are collected from files instead of bot instance.
    """
    # Initialize metrics without bot instance
    init_metrics(None)
    
    print(f"Starting web server in standalone mode at {host}:{port}")
    print("Note: Some features may be limited without direct bot connection")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )


atexit.register(stop_metrics)


if __name__ == "__main__":
    # When run directly, start in standalone mode
    import sys
    
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8000"))
    
    print(f"Starting Discord Combat AI Bot Web Interface")
    print(f"Server will be available at: http://{'localhost' if host == '0.0.0.0' else host}:{port}")
    print(f"Dashboard: http://{'localhost' if host == '0.0.0.0' else host}:{port}/dashboard")
    print(f"Guilds: http://{'localhost' if host == '0.0.0.0' else host}:{port}/guilds")
    
    run_standalone(host, port)

