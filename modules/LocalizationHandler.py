import json
import os
from typing import Any, Dict, Optional


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