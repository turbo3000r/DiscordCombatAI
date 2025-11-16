# DiscordCombatAI

## Setup

1. Create a Discord application and bot, then invite it to your server with the applications.commands scope.
2. Copy `.env.example` to `.env` and set the following variables:
   - `API_TOKEN` – Discord bot token (required)
   - `WEB_ENABLED` – Enable FastAPI dashboard (default `true`)
   - `WEB_HOST` / `WEB_PORT` – Bind address for dashboard (default `0.0.0.0:20000`)
   - `METRICS_DB_PATH` – SQLite file for metrics persistence (default `metrics.db`)
   - `METRICS_COLLECTION_INTERVAL` – Seconds between background samples (default `2`)
   - `METRICS_RETENTION_DAYS` – Days to keep history in SQLite (default `7`)
   - `METRICS_COMPRESSION_ENABLED` – Enables additional data compression (default `false`)
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
