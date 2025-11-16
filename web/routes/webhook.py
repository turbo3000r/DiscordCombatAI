"""Webhook management and messaging endpoints."""
import os
import json
import aiohttp
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.metrics import get_metrics
from modules.LoggerHandler import get_logger

router = APIRouter(prefix="/api/webhook", tags=["webhook"])
logger = get_logger()


class AnnouncementRequest(BaseModel):
    title: str
    author: str
    message: str
    destination: str  # "ALL" or comma-separated guild IDs
    guild_ids: Optional[List[str]] = []


class UpdateRequest(BaseModel):
    version: str
    version_name: str
    title: str
    added: List[Dict[str, str]]  # [{text: str, comment: str}]
    removed: List[Dict[str, str]]  # [{text: str, comment: str}]
    source_code: str
    additional_message: str
    destination: str  # "ALL" or comma-separated guild IDs
    guild_ids: Optional[List[str]] = []


async def send_webhook_message(webhook_url: str, content: str = None, embed_data: dict = None) -> bool:
    """
    Send a message via Discord webhook.
    
    Args:
        webhook_url: Discord webhook URL
        content: Text content to send (optional)
        embed_data: Embed data to send (optional)
    
    Returns:
        True if successful, False otherwise
    """
    if not webhook_url:
        return False
    
    try:
        payload = {}
        if content:
            payload["content"] = content
        if embed_data:
            payload["embeds"] = [embed_data]
        
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as response:
                if response.status in [200, 204]:
                    return True
                else:
                    logger.error(f"Webhook send failed with status {response.status}: {await response.text()}", extra={"guild": "WebHook"})
                    return False
    except Exception as e:
        logger.error(f"Failed to send webhook message: {e}", exc_info=True, extra={"guild": "WebHook"})
        return False


async def send_to_guilds(guild_ids: List[str], content: str = None, embed_data: dict = None) -> Dict[str, bool]:
    """
    Send a message to multiple guilds via their webhooks.
    
    Args:
        guild_ids: List of guild IDs to send to
        content: Text content to send
        embed_data: Embed data to send
    
    Returns:
        Dictionary mapping guild_id to success status
    """
    bot = get_metrics().bot
    if not bot or not hasattr(bot, 'guilds_data'):
        return {}
    
    results = {}
    for guild_id_str in guild_ids:
        try:
            guild_id = int(guild_id_str)
            guild_obj = bot.guilds_data.get(guild_id)
            
            if guild_obj and guild_obj.params.get("webhook_url"):
                webhook_url = guild_obj.params["webhook_url"]
                success = await send_webhook_message(webhook_url, content, embed_data)
                results[guild_id_str] = success
                
                if success:
                    logger.info(f"Message sent to guild {guild_id}", extra={"guild": "WebHook"})
                else:
                    logger.warning(f"Failed to send message to guild {guild_id}", extra={"guild": "WebHook"})
            else:
                results[guild_id_str] = False
                logger.warning(f"No webhook configured for guild {guild_id}", extra={"guild": "WebHook"})
        except Exception as e:
            results[guild_id_str] = False
            logger.error(f"Error sending to guild {guild_id_str}: {e}", extra={"guild": "WebHook"})
    
    return results


async def send_to_all_guilds(content: str = None, embed_data: dict = None) -> Dict[str, bool]:
    """
    Send a message to all guilds with configured webhooks.
    
    Args:
        content: Text content to send
        embed_data: Embed data to send
    
    Returns:
        Dictionary mapping guild_id to success status
    """
    bot = get_metrics().bot
    if not bot or not hasattr(bot, 'guilds_data'):
        return {}
    
    results = {}
    for guild_id, guild_obj in bot.guilds_data.items():
        try:
            if guild_obj.params.get("webhook_url"):
                webhook_url = guild_obj.params["webhook_url"]
                success = await send_webhook_message(webhook_url, content, embed_data)
                results[str(guild_id)] = success
                
                if success:
                    logger.info(f"Message sent to guild {guild_id}", extra={"guild": "WebHook"})
                else:
                    logger.warning(f"Failed to send message to guild {guild_id}", extra={"guild": "WebHook"})
            else:
                logger.debug(f"No webhook configured for guild {guild_id}", extra={"guild": "WebHook"})
        except Exception as e:
            results[str(guild_id)] = False
            logger.error(f"Error sending to guild {guild_id}: {e}", extra={"guild": "WebHook"})
    
    return results


@router.post("/send")
async def send_announcement(request: AnnouncementRequest):
    """Send an announcement to selected guilds."""
    try:
        # Create embed for announcement
        embed = {
            "title": request.title,
            "description": request.message,
            "color": 0x5865F2,  # Discord blurple
            "author": {
                "name": request.author
            }
        }
        
        # Send to guilds
        if request.destination == "ALL":
            results = await send_to_all_guilds(embed_data=embed)
        else:
            results = await send_to_guilds(request.guild_ids, embed_data=embed)
        
        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)
        
        return JSONResponse(content={
            "success": True,
            "message": f"Announcement sent to {success_count}/{total_count} guilds",
            "results": results
        })
    except Exception as e:
        logger.error(f"Failed to send announcement: {e}", exc_info=True, extra={"guild": "WebHook"})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update")
async def send_update(request: UpdateRequest):
    """Send an update notification to selected guilds."""
    try:
        # Format the update message with Discord markdown and diff
        message_lines = [f"# {request.title}", ""]
        
        if request.added:
            message_lines.append("**Added:**")
            message_lines.append("```diff")
            for item in request.added:
                message_lines.append(f"+ {item['text']}")
                if item.get('comment'):
                    message_lines.append(f"  # {item['comment']}")
            message_lines.append("```")
            message_lines.append("")
        
        if request.removed:
            message_lines.append("**Removed:**")
            message_lines.append("```diff")
            for item in request.removed:
                message_lines.append(f"- {item['text']}")
                if item.get('comment'):
                    message_lines.append(f"  # {item['comment']}")
            message_lines.append("```")
            message_lines.append("")
        
        if request.source_code:
            message_lines.append(f"**Source Code:** {request.source_code}")
            message_lines.append("")
        
        if request.additional_message:
            message_lines.append(request.additional_message)
        
        content = "\n".join(message_lines)
        
        # Send to guilds
        if request.destination == "ALL":
            results = await send_to_all_guilds(content=content)
        else:
            results = await send_to_guilds(request.guild_ids, content=content)
        
        success_count = sum(1 for success in results.values() if success)
        total_count = len(results)
        
        # Save update to file
        try:
            updates_dir = "updates"
            os.makedirs(updates_dir, exist_ok=True)
            
            filename = f"{request.version}-{request.version_name}.md"
            filepath = os.path.join(updates_dir, filename)
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            
            logger.info(f"Update saved to {filepath}", extra={"guild": "WebHook"})
        except Exception as e:
            logger.error(f"Failed to save update file: {e}", extra={"guild": "WebHook"})
        
        # Update version in bot.json
        try:
            bot_config_path = "bot.json"
            if os.path.exists(bot_config_path):
                with open(bot_config_path, "r", encoding="utf-8") as f:
                    bot_config = json.load(f)
                
                bot_config["version"] = request.version
                
                with open(bot_config_path, "w", encoding="utf-8") as f:
                    json.dump(bot_config, f, indent=2, ensure_ascii=False)
                
                logger.info(f"Bot version updated to {request.version}", extra={"guild": "WebHook"})
        except Exception as e:
            logger.error(f"Failed to update bot.json version: {e}", extra={"guild": "WebHook"})
        
        return JSONResponse(content={
            "success": True,
            "message": f"Update sent to {success_count}/{total_count} guilds",
            "results": results,
            "version": request.version
        })
    except Exception as e:
        logger.error(f"Failed to send update: {e}", exc_info=True, extra={"guild": "WebHook"})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/version")
async def get_version_info():
    """Get current version and suggest next version."""
    try:
        bot_config_path = "bot.json"
        if os.path.exists(bot_config_path):
            with open(bot_config_path, "r", encoding="utf-8") as f:
                bot_config = json.load(f)
            
            current_version = bot_config.get("version", "1.0.0")
            
            # Parse version and suggest next patch version
            parts = current_version.split(".")
            if len(parts) == 3:
                try:
                    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
                    suggested_versions = [
                        f"{major}.{minor}.{patch + 1}",  # Next patch
                        f"{major}.{minor + 1}.0",  # Next minor
                        f"{major + 1}.0.0"  # Next major
                    ]
                except ValueError:
                    suggested_versions = ["1.0.1"]
            else:
                suggested_versions = ["1.0.1"]
            
            return JSONResponse(content={
                "current_version": current_version,
                "suggested_versions": suggested_versions
            })
        else:
            return JSONResponse(content={
                "current_version": "1.0.0",
                "suggested_versions": ["1.0.1", "1.1.0", "2.0.0"]
            })
    except Exception as e:
        logger.error(f"Failed to get version info: {e}", extra={"guild": "WebHook"})
        raise HTTPException(status_code=500, detail=str(e))

