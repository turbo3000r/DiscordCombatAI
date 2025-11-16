"""Dashboard API endpoints."""
import os
from typing import Optional
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
import aiofiles

from web.metrics import get_metrics


router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/metrics")
async def get_current_metrics():
    """Get current bot metrics including CPU, memory, latency, uptime, and errors."""
    metrics_collector = get_metrics()
    metrics = metrics_collector.get_all_metrics()
    return JSONResponse(content=metrics)


@router.get("/metrics/history")
async def get_metrics_history(minutes: Optional[int] = Query(default=2, ge=1, le=1440)):
    """
    Get historical metrics data for graphs.
    
    Args:
        minutes: Number of minutes of history to return (default: 2, max: 1440 = 24 hours)
    """
    metrics_collector = get_metrics()
    history = metrics_collector.get_history(minutes=minutes)
    return JSONResponse(content=history)


@router.get("/logs")
async def get_logs(lines: int = Query(default=100, ge=1, le=1000), since: Optional[float] = None):
    """
    Get recent log entries from Latest.log.
    
    Args:
        lines: Number of lines to retrieve (default: 100, max: 1000)
        since: Unix timestamp to get logs after (optional)
    """
    log_path = os.path.join("logs", "Latest.log")
    
    if not os.path.exists(log_path):
        return JSONResponse(content={"logs": [], "timestamp": 0})
    
    try:
        async with aiofiles.open(log_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            log_lines = content.strip().split('\n') if content.strip() else []
            
            # Get last N lines
            recent_logs = log_lines[-lines:] if log_lines else []
            
            # Get file modification time
            file_mtime = os.path.getmtime(log_path)
            
            return JSONResponse(content={
                "logs": recent_logs,
                "timestamp": file_mtime,
                "total_lines": len(log_lines)
            })
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "logs": [], "timestamp": 0},
            status_code=500
        )

