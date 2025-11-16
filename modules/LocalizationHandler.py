import json
import os
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class LocalizationHandler:
    def __init__(self, locales_dir: str = "lang", default_locale: str = "en"):
        self.locales_dir = locales_dir
        self.default_locale = default_locale
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._mtimes: Dict[str, float] = {}

    def _locale_path(self, locale: str) -> str:
        return os.path.join(self.locales_dir, f"{locale}.json")

    def _ensure_loaded(self, locale: str) -> None:
        path = self._locale_path(locale)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            # Locale file not found
            self._cache.setdefault(locale, {})
            self._mtimes[locale] = -1.0
            return

        # Load or reload if file changed
        if locale not in self._cache or self._mtimes.get(locale) != mtime:
            with open(path, "r", encoding="utf-8") as f:
                self._cache[locale] = json.load(f)
            self._mtimes[locale] = mtime

    def _lookup(self, data: Dict[str, Any], key_path: str) -> Optional[Any]:
        parts = key_path.split(".") if key_path else []
        node: Any = data
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def translate(self, locale: str, key: str, **variables: Any) -> str:
        """
        Look up a translation for key in the given locale with fallback to default locale.
        Supports nested keys with dot-notation and str.format-style placeholders.
        Unprovided placeholders are left intact.
        """
        # Load requested and default locales (lazy with reload-on-change)
        self._ensure_loaded(locale)
        if locale != self.default_locale:
            self._ensure_loaded(self.default_locale)

        raw = self._lookup(self._cache.get(locale, {}), key)
        if raw is None and locale != self.default_locale:
            raw = self._lookup(self._cache.get(self.default_locale, {}), key)

        if raw is None:
            # Return the key if not found to make missing strings obvious
            return key

        if isinstance(raw, str):
            if variables:
                # Safe format: leave unknown variables as placeholders
                try:
                    return raw.format_map(_SafeDict(variables))
                except Exception:
                    return raw
            return raw

        # If the value is not a string (e.g., object), return JSON string
        try:
            return json.dumps(raw, ensure_ascii=False)
        except Exception:
            return str(raw)

    def available_locales(self) -> Dict[str, str]:
        """Return mapping of locale code to file path for discovered locales."""
        locales: Dict[str, str] = {}
        if not os.path.isdir(self.locales_dir):
            return locales
        for name in os.listdir(self.locales_dir):
            if name.endswith(".json"):
                code = name[:-5]
                locales[code] = os.path.join(self.locales_dir, name)
        return locales

    def resolve_guild_locale(self, guild_id: int) -> str:
        """Best-effort read of a guild's configured language; falls back to default."""
        cfg_path = os.path.join("guilds", str(guild_id), "config.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            lang = cfg.get("language")
            if isinstance(lang, str) and lang:
                return lang
        except Exception:
            pass
        return self.default_locale

    def t(self, key: str, locale: Optional[str] = None, guild_id: Optional[int] = None, **variables: Any) -> str:
        """
        Convenience translate method: supply either locale or guild_id.
        If both provided, locale takes precedence.
        """
        if locale is None and guild_id is not None:
            locale = self.resolve_guild_locale(guild_id)
        locale = locale or self.default_locale
        return self.translate(locale, key, **variables)

    def full_localization_name(self, locale: str) -> str:
        """
        Return the full name of a localization code.
        Format: Language-Region (e.g., "en" -> "English-General", "es" -> "Spanish-Spain")
        
        Args:
            locale: The locale code (e.g., "en", "es", "uk")
        
        Returns:
            Full localization name in format "Language-Region"
        """
        mapping: Dict[str, str] = {
            "en": "English-General",
            "es": "Spanish-Spain",
            "fr": "French-France",
            "de": "German-Germany",
            "it": "Italian-Italy",
            "pt": "Portuguese-Brazil",
            "ru": "Russian-Russia",
            "zh": "Chinese-China",
            "ja": "Japanese-Japan",
            "ko": "Korean-Korea",
            "pl": "Polish-Poland",
            "tr": "Turkish-Turkey",
            "vi": "Vietnamese-Vietnam",
            "cs": "Czech-Czech Republic",
            "da": "Danish-Denmark",
            "fi": "Finnish-Finland",
            "hi": "Hindi-India",
            "hr": "Croatian-Croatia",
            "hu": "Hungarian-Hungary",
            "id": "Indonesian-Indonesia",
            "nl": "Dutch-Netherlands",
            "no": "Norwegian-Norway",
            "ro": "Romanian-Romania",
            "sv": "Swedish-Sweden",
            "th": "Thai-Thailand",
            "uk": "Ukrainian-Ukraine",
            "ua": "Ukrainian-Ukraine",
        }
        return mapping.get(locale, f"{locale}-Unknown")

    def _simple_to_discord_locale(self, simple_locale: str) -> List[str]:
        """
        Map simple locale codes (en, es) to Discord locale codes.
        Returns a list of Discord locale codes that should use this translation.
        """
        mapping: Dict[str, List[str]] = {
            "en": ["en-US", "en-GB"],
            "es": ["es-ES"],
            "fr": ["fr"],
            "de": ["de"],
            "it": ["it"],
            "pt": ["pt-BR"],
            "ru": ["ru"],
            "zh": ["zh-CN"],
            "ja": ["ja"],
            "ko": ["ko"],
            "pl": ["pl"],
            "tr": ["tr"],
            "vi": ["vi"],
            "cs": ["cs"],
            "da": ["da"],
            "fi": ["fi"],
            "hi": ["hi"],
            "hr": ["hr"],
            "hu": ["hu"],
            "id": ["id"],
            "nl": ["nl"],
            "no": ["no"],
            "ro": ["ro"],
            "sv": ["sv-SE"],
            "th": ["th"],
            "ua": ["uk"],
        }
        return mapping.get(simple_locale, [simple_locale])

    def get_command_localizations(self, command_name: str) -> Dict[str, Dict[str, str]]:
        """
        Load command name and description localizations for all available locales.
        Returns a dict with 'name_localizations' and 'description_localizations' keys.
        Each contains a mapping of Discord locale codes to translated strings.
        
        Args:
            command_name: The command name (e.g., "ping", "quick-battle")
        
        Returns:
            Dict with 'name_localizations' and 'description_localizations' keys
        """
        name_localizations: Dict[str, str] = {}
        description_localizations: Dict[str, str] = {}
        
        # Load all available locales
        available = self.available_locales()
        
        for simple_locale in available.keys():
            self._ensure_loaded(simple_locale)
            locale_data = self._cache.get(simple_locale, {})
            
            # Look up command data
            command_data = self._lookup(locale_data, f"commands.{command_name}")
            
            if command_data and isinstance(command_data, dict):
                # Get description
                description = command_data.get("description")
                if isinstance(description, str):
                    # Map to Discord locale codes
                    discord_locales = self._simple_to_discord_locale(simple_locale)
                    for discord_locale in discord_locales:
                        description_localizations[discord_locale] = description
                
                # Note: Discord doesn't typically localize command names, but we can if needed
                # For now, we'll skip name localization as it's less common
        
        return {
            "name_localizations": name_localizations,
            "description_localizations": description_localizations
        }

    def get_argument_localizations(self, command_name: str, argument_name: str) -> Dict[str, Dict[str, str]]:
        """
        Load argument name and description localizations for all available locales.
        Returns a dict with 'name_localizations' and 'description_localizations' keys.
        
        Args:
            command_name: The command name (e.g., "quick-battle")
            argument_name: The argument name (e.g., "opponent")
        
        Returns:
            Dict with 'name_localizations' and 'description_localizations' keys
        """
        name_localizations: Dict[str, str] = {}
        description_localizations: Dict[str, str] = {}
        
        # Load all available locales
        available = self.available_locales()
        
        for simple_locale in available.keys():
            self._ensure_loaded(simple_locale)
            locale_data = self._cache.get(simple_locale, {})
            
            # Look up command data
            command_data = self._lookup(locale_data, f"commands.{command_name}")
            
            if command_data and isinstance(command_data, dict):
                # Look for args array
                args = command_data.get("args")
                if isinstance(args, list):
                    # Find the argument by name
                    for arg in args:
                        if isinstance(arg, dict) and arg.get("name") == argument_name:
                            # Get description
                            description = arg.get("description")
                            if isinstance(description, str):
                                # Map to Discord locale codes
                                discord_locales = self._simple_to_discord_locale(simple_locale)
                                for discord_locale in discord_locales:
                                    description_localizations[discord_locale] = description
                            break
        
        return {
            "name_localizations": name_localizations,
            "description_localizations": description_localizations
        }


def loadLocalizationForCommand(commandName: str) -> Dict[str, Dict[str, str]]:
    """
    Convenience function to load command localizations.
    Creates a LocalizationHandler instance and loads localizations for the given command.
    
    Args:
        commandName: The command name (e.g., "ping", "quick-battle")
    
    Returns:
        Dict with 'name_localizations' and 'description_localizations' keys
    """
    handler = LocalizationHandler()
    return handler.get_command_localizations(commandName)


class DiscordTranslator(app_commands.Translator):
    """
    A Translator implementation that uses LocalizationHandler to translate
    Discord app command strings from JSON localization files.
    """
    
    def __init__(self, l10n: LocalizationHandler):
        super().__init__()
        self.l10n = l10n
        self._discord_to_simple_locale: Dict[str, str] = {}
        self._build_locale_mapping()
    
    def _build_locale_mapping(self) -> None:
        """Build reverse mapping from Discord locale codes to simple locale codes."""
        mapping = {
            "en": ["en-US", "en-GB"],
            "es": ["es-ES"],
            "fr": ["fr"],
            "de": ["de"],
            "it": ["it"],
            "pt": ["pt-BR"],
            "ru": ["ru"],
            "zh": ["zh-CN"],
            "ja": ["ja"],
            "ko": ["ko"],
            "pl": ["pl"],
            "tr": ["tr"],
            "vi": ["vi"],
            "cs": ["cs"],
            "da": ["da"],
            "fi": ["fi"],
            "hi": ["hi"],
            "hr": ["hr"],
            "hu": ["hu"],
            "id": ["id"],
            "nl": ["nl"],
            "no": ["no"],
            "ro": ["ro"],
            "sv": ["sv-SE"],
            "th": ["th"],
            "ua": ["uk"],
        }
        
        for simple_locale, discord_locales in mapping.items():
            for discord_locale in discord_locales:
                self._discord_to_simple_locale[discord_locale] = simple_locale
    
    def _discord_locale_to_simple(self, locale: discord.Locale) -> str:
        """Convert Discord Locale enum to simple locale code."""
        locale_str = str(locale.value)  # e.g., "en-US", "es-ES"
        return self._discord_to_simple_locale.get(locale_str, "en")
    
    def _get_translation_key(self, context: app_commands.TranslationContextTypes) -> Optional[str]:
        """Extract translation key from context."""
        location = context.location
        data = context.data
        
        if location == app_commands.TranslationContextLocation.command_description:
            # For command description: commands.{command_name}.description
            command = data
            command_name = command.name
            return f"commands.{command_name}.description"
        
        elif location == app_commands.TranslationContextLocation.parameter_description:
            # For parameter description, we need to look it up from the args array
            # The key format is: commands.{command_name}.args.{param_name}
            # But since args is an array, we'll use a special lookup method
            parameter = data
            command = parameter.command
            command_name = command.name
            param_name = parameter.name
            # Return a special key that we'll handle in translate method
            return f"commands.{command_name}.args.{param_name}"
        
        elif location == app_commands.TranslationContextLocation.choice_name:
            # For choice name, we need to extract command, parameter, and choice value
            # The data should be a Choice object
            choice = data
            # Get the parameter that this choice belongs to
            # We need to traverse up to find the parameter
            # The choice should have a reference to its parameter
            try:
                # Try to get parameter from choice's parent
                if hasattr(choice, 'parameter'):
                    parameter = choice.parameter
                    command = parameter.command
                    command_name = command.name
                    param_name = parameter.name
                    # Map choice value to key name
                    # For custom_environment: 0 -> "generic", 1 -> "custom"
                    choice_value = choice.value
                    choice_key = None
                    if param_name == "custom_environment":
                        choice_key = "generic" if choice_value == 0 else "custom"
                    else:
                        # For other parameters, use the value as string if it's a string
                        choice_key = str(choice_value) if isinstance(choice_value, str) else None
                    
                    if choice_key:
                        # Try both path formats for compatibility
                        # Format 1: commands.{command}.choices.{param}.{choice}
                        # Format 2: commands.{command}.args.{param}.choices.{choice}
                        return f"commands.{command_name}.choices.{param_name}.{choice_key}"
            except Exception:
                pass
        
        # For other locations, return None (no translation)
        return None
    
    def _lookup_arg_description(self, locale_data: Dict[str, Any], command_name: str, param_name: str) -> Optional[str]:
        """Look up argument description from the args array in locale data."""
        command_data = self.l10n._lookup(locale_data, f"commands.{command_name}")
        if command_data and isinstance(command_data, dict):
            args = command_data.get("args")
            if isinstance(args, list):
                for arg in args:
                    if isinstance(arg, dict) and arg.get("name") == param_name:
                        return arg.get("description")
        return None
    
    def _lookup_choice_name(self, locale_data: Dict[str, Any], command_name: str, param_name: str, choice_key: str) -> Optional[str]:
        """Look up choice name from the choices object in locale data."""
        command_data = self.l10n._lookup(locale_data, f"commands.{command_name}")
        if command_data and isinstance(command_data, dict):
            # Try format 1: commands.{command}.choices.{param}.{choice}
            choices = command_data.get("choices")
            if isinstance(choices, dict):
                param_choices = choices.get(param_name)
                if isinstance(param_choices, dict):
                    return param_choices.get(choice_key)
            # Try format 2: commands.{command}.args.{param}.choices.{choice}
            args = command_data.get("args")
            if isinstance(args, list):
                for arg in args:
                    if isinstance(arg, dict) and arg.get("name") == param_name:
                        arg_choices = arg.get("choices")
                        if isinstance(arg_choices, dict):
                            return arg_choices.get(choice_key)
        return None
    
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContextTypes
    ) -> Optional[str]:
        """
        Translate a locale_str to the specified locale using LocalizationHandler.

        Priority:
        1) If string.extras contains a 'key', use that directly.
        2) Otherwise, infer key from context (command/parameter).

        Returns None if translation is not found (Discord will use the default message).
        """
        # Prefer explicit key passed via extras
        explicit_key = None
        try:
            explicit_key = string.extras.get("key")  # type: ignore[attr-defined]
        except Exception:
            explicit_key = None

        if explicit_key:
            translation_key = explicit_key
        else:
            # Get the translation key from context
            translation_key = self._get_translation_key(context)
            if translation_key is None:
                return None
        
        # Convert Discord locale to simple locale code
        simple_locale = self._discord_locale_to_simple(locale)
        
        # Check if we have this locale available
        available = self.l10n.available_locales()
        if simple_locale not in available:
            return None
        
        # Load locale data
        self.l10n._ensure_loaded(simple_locale)
        locale_data = self.l10n._cache.get(simple_locale, {})
        
        # Handle explicit keys or inferred keys
        # Strip .description suffix if present for arg descriptions
        translation_key_for_lookup = translation_key
        is_arg_description = translation_key.endswith(".description") and ".args." in translation_key
        if is_arg_description:
            translation_key_for_lookup = translation_key[:-11]  # Remove ".description"
        
        if ".args." in translation_key_for_lookup and not translation_key_for_lookup.endswith(".description"):
            # Check if this is a choice name lookup
            if ".choices." in translation_key_for_lookup:
                # Choice name lookup: commands.{command}.choices.{param}.{choice} or commands.{command}.args.{param}.choices.{choice}
                parts = translation_key_for_lookup.split(".choices.")
                if len(parts) == 2:
                    # Extract command and param from first part
                    first_part = parts[0].replace("commands.", "")
                    if ".args." in first_part:
                        # Format: commands.{command}.args.{param}.choices.{choice}
                        cmd_and_param = first_part.split(".args.")
                        if len(cmd_and_param) == 2:
                            command_name = cmd_and_param[0]
                            param_name = cmd_and_param[1]
                            choice_key = parts[1]
                            translated = self._lookup_choice_name(locale_data, command_name, param_name, choice_key)
                            if translated:
                                return translated
                    else:
                        # Format: commands.{command}.choices.{param}.{choice}
                        # First part is just command name, need to extract param from the structure
                        # Actually, the format should be: commands.{command}.choices.{param}.{choice}
                        # So we need to split differently
                        if "." in first_part:
                            # This shouldn't happen with this format, but handle it
                            pass
                        else:
                            # We need param name from context, but we can try to extract from the key
                            # The full key is: commands.{command}.choices.{param}.{choice}
                            # So we need to split on ".choices." and then get param from the second part
                            second_part = parts[1]
                            if "." in second_part:
                                param_and_choice = second_part.split(".", 1)
                                if len(param_and_choice) == 2:
                                    param_name = param_and_choice[0]
                                    choice_key = param_and_choice[1]
                                    command_name = first_part
                                    translated = self._lookup_choice_name(locale_data, command_name, param_name, choice_key)
                                    if translated:
                                        return translated
            else:
                # Argument description - need to look up from array if not an explicit '.description' path
                parts = translation_key_for_lookup.split(".args.")
                if len(parts) == 2:
                    command_name = parts[0].replace("commands.", "")
                    param_name = parts[1]
                    translated = self._lookup_arg_description(locale_data, command_name, param_name)
                    if translated:
                        return translated
        elif is_arg_description:
            # Handle case where key ends with .description - strip it and look up
            parts = translation_key_for_lookup.split(".args.")
            if len(parts) == 2:
                command_name = parts[0].replace("commands.", "")
                param_name = parts[1]
                translated = self._lookup_arg_description(locale_data, command_name, param_name)
                if translated:
                    return translated
        elif ".choices." in translation_key and ".args." not in translation_key:
            # Choice name lookup in format: commands.{command}.choices.{param}.{choice}
            parts = translation_key.split(".choices.")
            if len(parts) == 2:
                command_name = parts[0].replace("commands.", "")
                second_part = parts[1]
                if "." in second_part:
                    param_and_choice = second_part.split(".", 1)
                    if len(param_and_choice) == 2:
                        param_name = param_and_choice[0]
                        choice_key = param_and_choice[1]
                        translated = self._lookup_choice_name(locale_data, command_name, param_name, choice_key)
                        if translated:
                            return translated

        # Fallback to direct lookup using translate (handles normal keys, including '.description' or any provided path)
        translated = self.l10n.translate(simple_locale, translation_key)
        if translated != translation_key:
            return translated
        
        return None


def lstr(key: str, default: Optional[str] = None) -> app_commands.locale_str:
    """
    Factory to create a locale_str that carries a translation key in extras.
    The message (fallback) is taken from the default locale if not provided.
    """
    handler = LocalizationHandler()
    fallback = default if default is not None else handler.translate(handler.default_locale, key)
    # Attach the key via extras so Translator can prioritise it
    return app_commands.locale_str(str(fallback), key=key)