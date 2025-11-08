# DiscordCombatAI

## Setup

1. Create a Discord application and bot, then invite it to your server with the applications.commands scope.
2. Copy `.env.example` to `.env` and set `API_TOKEN`.
3. Install dependencies:

```
pip install -r requirements.txt
```

## Run

```
python app.py
```


## Localization

Translations live in `lang/<locale>.json` (e.g., `lang/en.json`). Use nested keys and placeholders.

Example `lang/en.json`:

```
{
  "common": {
    "hello": "Hello, {user}!",
    "pong": "Pong! {ms}ms"
  }
}
```

Usage from code via `modules/LocalizationHandler.py`:

```
from modules.LocalizationHandler import LocalizationHandler

l10n = LocalizationHandler()
text = l10n.t("common.hello", guild_id=interaction.guild_id, user=interaction.user.mention)
```

Notes:
- Falls back to `en` if a key or locale is missing.
- Files are cached and automatically reloaded if modified.
