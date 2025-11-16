import json
from discord import Guild as DiscordGuild
import discord
from modules.AIHandler import AIHandler
from modules.LocalizationHandler import LocalizationHandler
from modules.LoggerHandler import get_logger

logger = get_logger()
class Guild:
    def __init__(self, guild: DiscordGuild):
        self._guild = guild
        self.guild_id = guild.id
        self.params = {}
        self.__load__()

    @property
    def localization(self):
        return LocalizationHandler(default_locale=self.params.get("language"))
    

    def __initAIHandler__(self) :
        self.AIHandler = None
        if self.enabled:
            self.AIHandler = AIHandler.from_guild(self)
        

    def enableAI(self) -> bool:
        try:
            self.AIHandler = AIHandler.from_guild(self)
        except Exception as e:
            logger.error(f"Failed to enable AI for guild {self.guild_id}: {e}", exc_info=True, extra={"guild": f"{self.name}({self.guild_id})"})
            return False
        if not self.AIHandler or not self.AIHandler.is_api_key_valid:
            logger.warning(f"Failed to enable AI for guild {self.guild_id}: Invalid API key", exc_info=True, extra={"guild": f"{self.name}({self.guild_id})"})
            return False
        return True
        
    def __getattr__(self, name):
        
        if name in self.params:
            return self.params[name]
        # Delegate missing attributes to the underlying discord.Guild
        return getattr(self._guild, name)
    
    def check(self):
        if not self.params.get("enabled"):
            return False
        return True
    
    def __load__(self):
        with open(f"guilds/{self.guild_id}/config.json", "r") as f:
            self.params.update(json.load(f))
        self.__initAIHandler__()

    def __save__(self):
        with open(f"guilds/{self.guild_id}/config.json", "w") as f:
            json.dump(self.params, f)
    
    def __str__(self):
        return f"Guild(id={self.guild_id}, name={self._guild.name})"
    
    def __repr__(self):
        return self.__str__()
    
    def __getitem__(self, key):
        return self.params[key]
    
    def __setitem__(self, key, value):
        self.params[key] = value
        self.__save__()
    
    def __delitem__(self, key):
        del self.params[key]
        self.__save__()

    