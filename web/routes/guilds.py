"""Guilds API endpoints."""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from web.metrics import get_metrics


router = APIRouter(prefix="/api", tags=["guilds"])


def get_bot_instance():
    """Get the bot instance from metrics collector."""
    metrics_collector = get_metrics()
    return metrics_collector.bot


@router.get("/guilds")
async def get_guilds():
    """Get list of all guilds the bot is in."""
    bot = get_bot_instance()
    
    if not bot or not hasattr(bot, 'guilds'):
        return JSONResponse(content={"guilds": [], "count": 0})
    
    guilds_data = []
    for guild in bot.guilds:
        try:
            # Get guild icon URL
            icon_url = None
            if guild.icon:
                icon_url = str(guild.icon.url)
            
            # Get webhook configuration status
            webhook_configured = False
            if hasattr(bot, 'guilds_data') and guild.id in bot.guilds_data:
                guild_obj = bot.guilds_data[guild.id]
                if hasattr(guild_obj, 'params'):
                    webhook_configured = bool(guild_obj.params.get("webhook_url", "").strip())
            
            guild_info = {
                "id": str(guild.id),
                "name": guild.name,
                "member_count": guild.member_count,
                "icon_url": icon_url,
                "created_at": guild.created_at.isoformat() if guild.created_at else None,
                "owner_id": str(guild.owner_id) if guild.owner_id else None,
                "webhook_configured": webhook_configured
            }
            guilds_data.append(guild_info)
        except Exception as e:
            # Skip guilds that cause errors
            continue
    
    return JSONResponse(content={
        "guilds": guilds_data,
        "count": len(guilds_data)
    })


@router.get("/guilds/{guild_id}")
async def get_guild_details(guild_id: str):
    """
    Get detailed information about a specific guild.
    Note: This is currently a placeholder for future functionality.
    """
    bot = get_bot_instance()
    
    if not bot or not hasattr(bot, 'guilds'):
        raise HTTPException(status_code=503, detail="Bot not available")
    
    # Find the guild
    guild = None
    try:
        guild_id_int = int(guild_id)
        for g in bot.guilds:
            if g.id == guild_id_int:
                guild = g
                break
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid guild ID")
    
    if not guild:
        raise HTTPException(status_code=404, detail="Guild not found")
    
    # Get basic guild info
    icon_url = None
    if guild.icon:
        icon_url = str(guild.icon.url)
    
    # Get guild configuration if available
    config_data = {}
    if hasattr(bot, 'guilds_data') and guild.id in bot.guilds_data:
        guild_obj = bot.guilds_data[guild.id]
        if hasattr(guild_obj, 'params'):
            config_data = {
                "language": guild_obj.params.get("language", "en"),
                "model": guild_obj.params.get("model", "N/A"),
                "enabled": guild_obj.params.get("enabled", False),
                "webhook_configured": bool(guild_obj.params.get("webhook_url", "").strip())
            }
    
    guild_details = {
        "id": str(guild.id),
        "name": guild.name,
        "member_count": guild.member_count,
        "icon_url": icon_url,
        "created_at": guild.created_at.isoformat() if guild.created_at else None,
        "owner_id": str(guild.owner_id) if guild.owner_id else None,
        "config": config_data,
        "placeholder": True,  # Indicates full functionality not yet implemented
        "message": "Full guild details functionality coming soon"
    }
    
    return JSONResponse(content=guild_details)

