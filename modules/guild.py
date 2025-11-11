import json
from discord import Guild as DiscordGuild
import discord
from modules.AIHandler import AIHandler
from modules.LocalizationHandler import LocalizationHandler


class Guild:
    def __init__(self, guild: DiscordGuild):
        self._guild = guild
        self.guild_id = guild.id
        self.params = {}
        self.__load__()
        self.AIHandler = AIHandler(api_key=self.params.get("api_key"), model=self.params.get("model"))

    @property
    def localization(self):
        return LocalizationHandler(default_locale=self.params.get("language"))
    
    def onMemberHasNoPermissions(self, interaction: discord.Interaction):
        interaction.response.send_message(self.localization.t("errors.member_has_no_permissions"), ephemeral=True)


    def onError(self, interaction: discord.Interaction, error: Exception):
        interaction.response.send_message(self.localization.t("errors.error"), ephemeral=True)

    def isOwnerCheck(self, interaction: discord.Interaction):
        return interaction.user.id == self._guild.owner_id
        

    def __getattr__(self, name):
        # Delegate missing attributes to the underlying discord.Guild
        return getattr(self._guild, name)
    
    def check(self):
        if not self.params.get("enabled"):
            return False
        return True
    
    def __load__(self):
        with open(f"guilds/{self.guild_id}/config.json", "r") as f:
            self.params.update(json.load(f))

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

    