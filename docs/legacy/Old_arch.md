 

Here is the full mini *LEGACY* documentation for the **DiscordCombatAI** project.

---

# DiscordCombatAI — Project Documentation

## Overview


**DiscordCombatAI** (v0.1.2) is an AI-powered Discord bot that lets server members stage narrative text battles. Players choose fighters, describe them, define strategies and environments, then a Google Gemini model writes and judges the fight in real time. The bot is accompanied by a self-hosted web admin panel for monitoring, guild management, announcement broadcasting, and user-suggestion review.

---

## Technology Stack

| Layer                   | Technology                                                                               |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| **Bot framework**       | [discord.py](https://discordpy.readthedocs.io) ≥ 2.3 (slash commands via `app_commands`) |
| **AI model**            | Google Gemini (`google-generativeai` ≥ 0.8), default model `gemini-2.5-flash`            |
| **Web server**          | FastAPI + Uvicorn (runs in a background daemon thread)                                   |
| **Metrics persistence** | SQLite via a thin wrapper (`web/persistence.py`)                                         |
| **System metrics**      | psutil                                                                                   |
| **HTTP client**         | aiohttp (webhook delivery)                                                               |
| **File I/O**            | aiofiles (async log reading)                                                             |
| **Config / secrets**    | python-dotenv (`.env` file)                                                              |
| **Runtime**             | Python 3.11+, asyncio with Windows `SelectorEventLoopPolicy`                             |

---

## High-Level Architecture

```

  

┌──────────────────────────────────────────┐ 
│                app.py                    │  ← entry point
│  loads .env → creates DiscordBot         │
│  optionally starts FastAPI in thread     │
└──────────┬───────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────┐
│        modules/main.py  DiscordBot       │  ← discord.py Bot subclass
│  - registers /ping, /suggest, /config    │
│  - on_guild_join / on_guild_remove       │
│  - holds BattleHandler                   │
└──────┬──────────────────────────────┬────┘
       │                              │
       ▼                              ▼
┌─────────────────┐          ┌──────────────────────┐
│  BattleHandler  │          │  ConfigurationHandler│
│  /quick-battle  │          │  /config command     │
└────────┬────────┘          └──────────────────────┘
         │
    battle flow

  

    ┌────┴──────────────────────────┐

  

    │  EnvironmentCreator           │

  

    │  FighterCreator               │

  

    │  StrategyCreator              │

  

    │  → PromptHandler.evaluate()   │─── AIHandler ──► Google Gemini API

  

    │  → save_battle_result()       │─── guilds/{id}/quick-battle/*.txt

  

    └───────────────────────────────┘

  
  

┌────────────────────────────────────────────────┐

  

│               web/server.py  FastAPI           │

  

│  GET  /dashboard   /guilds  /performance       │

  

│  GET  /webhook     /suggestions                │

  

│  API  /api/metrics /api/logs /api/guilds       │

  

│  API  /api/suggestions  /api/webhook           │

  

│  WS   /ws/logs  (live log stream)              │

  

└──────┬─────────────────────────────────────────┘

  

       │                         │

  

  web/metrics.py            web/bot_bridge.py

  

  MetricsCollector           (holds bot ref for DMs)

  

  → MetricsDB (SQLite)

  

```


---

## Module-by-Module Breakdown

### `app.py` — Entry Point

- Reads environment variables (`API_TOKEN`, `WEB_ENABLED`, `WEB_HOST`, `WEB_PORT`).

- Supports `--dev` flag to restrict commands to a single guild (`DEV_GUILD_ID`).  

- Starts the FastAPI web server in a daemon thread if `WEB_ENABLED=true`.  

- Calls `bot.run(API_TOKEN)` (blocking).

  
  

---

  
  

### `modules/main.py` — `DiscordBot`

  

The central bot class. Responsibilities:  

- Initializes `BattleHandler` and calls `register_setup` (config command).  

- Registers global slash commands `/ping` and `/suggest`.  

- `on_ready`: syncs slash commands, loads all guild data into `self.guilds_data`.  

- `on_guild_join`: creates the guild's folder, saves a default `config.json`, sends a localised welcome message with a language-selector dropdown. 

- `on_guild_remove`: removes the guild from memory and deletes its folder.  
  

**UI components defined here:** `WelcomeView`, `WelcomeLocaleSelect`, `SuggestionView`, `SuggestionTypeSelect`, `SuggestionCategorySelect`, `SuggestionModal`.

  
---

  
  

### `modules/BattleHandler.py` — Battle Logic  

Registers the `/quick-battle` slash command and orchestrates the entire multi-step battle flow.  
  

**Key classes:**
  
| Class                                                                               | Role                                                                       |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `Fighter`                                                                           | Data model: name, description, strategy, Discord member                    |
| `FighterCreator`                                                                    | Sends a button/modal flow to collect fighter details from each participant |
| `StrategyCreator`                                                                   | Same pattern, collects battle strategies                                   |
| `EnvironmentCreator`                                                                | Collects environment descriptions, combines them with AI                   |
| `QuickBattleRequest`                                                                | The live lobby: join/leave/start/abort buttons, 1-second tick timer        |
| `JoinButton` / `LeaveButton` / `StartButton` / `AbortButton` / `CreatorAbortButton` | Discord UI buttons                                                         |

  
  

**Battle flow:**

  

1. Owner runs `/quick-battle` → `QuickBattleRequest` lobby created with countdown.  

2. Players join; owner presses Start (or timer expires).  

3. If `custom_environment`, `EnvironmentCreator` collects descriptions from all players, then AI merges them.  

4. `FighterCreator` collects fighter name + description from each player.  

5. `StrategyCreator` collects battle strategies.  

6. All prompts are assembled and sent to Gemini via `PromptHandler.evaluateMultiple`.  

7. The AI-generated story is posted to the channel and saved to disk.  
  

---

  
  

### `modules/AIHandler.py` — Gemini Wrapper

  

- Wraps `google.genai.Client`.  

- `generate_response()` is **async** — runs the blocking Gemini call in a thread-pool executor so it never blocks the Discord event loop.  

- Per-API-key `threading.Lock` prevents race conditions when multiple guilds with different keys run simultaneously.  

- `is_api_key_valid` makes a minimal 1-token test call.  
  

---

  
  

### `modules/PromptHandler.py` — Prompt Assembly

  

- `Prompt` / `SystemPrompt` / `RandomSystemPrompt`: classes that load `.txt` files and can be combined with `+` (separator `\n---\n`).  

- `Prompts` namespace: pre-loaded prompt singletons for `Core`, `Setting`, and `Elements`.  

- `SETTINGS` dict maps user-facing setting names to the corresponding `SystemPrompt`.  

- `PromptHandler.evaluateMultiple()`: concatenates all system prompts, then calls `AIHandler.generate_response`.  
  

**Available settings:** `unpredictable-dreamcore`, `unpredictable-funny`, `dreamcore`, `realistic`, `realistic-urban`, `realistic-nature`.  
  

---

  
  

### `modules/guild.py` — `Guild` Wrapper

  

A thin wrapper around `discord.Guild` that:  

- Loads `guilds/{guild_id}/config.json` on construction (`__load__`).  

- Exposes params as attributes via `__getattr__` (falls back to the underlying discord object).  

- `enableAI()`: creates an `AIHandler` and validates the API key.  

- `__save__`: writes params back to JSON.

  
  

Default guild config keys: `enabled`, `language`, `AIEnabled`, `api_key`, `model`, `webhook_url`.

  
  

---

  
  

### `modules/ConfigurationHandler.py` — `/config` Command

  

Registers the `/config` slash command (admin-only). Shows an interactive embed with:

  

- Language dropdown (`LanguageSelect`) — supports `en`, `es`, `ua`.

  

- **AI Config** button → `AIConfigModal` (Google API key + model name).

  

- **Webhook Change** button → `WebhookConfigModal` (Discord webhook URL).

  

- **Apply** button → validates the API key via `AIHandler.is_api_key_valid` and saves to `config.json`.

  
  

Changes are staged in an in-memory dict `_pending_changes` until Apply is pressed.

  
  

---

  
  

### `modules/LocalizationHandler.py` — i18n

  

- Reads `lang/<locale>.json`, supports dot-notation keys and `{placeholder}` substitution.

  

- Auto-reloads if the file changes on disk (mtime check).

  

- Falls back to `en` for missing keys.

  

- `DiscordTranslator` implements `app_commands.Translator` to translate slash command descriptions/choices for Discord's native locale system.

  

- `lstr(key)` factory creates `app_commands.locale_str` objects with the key embedded in `extras`.

  
  

---

  
  

### `modules/LoggerHandler.py` — Structured Logging

  

- `CustomLogger` reads `logger_config.json` and sets up four `RotatingFileHandler` instances:

  
  

| File | Levels |

  

|---|---|

  

| `logs/Errors.log` | ERROR, CRITICAL |

  

| `logs/Log.log` | INFO, WARNING, ERROR, CRITICAL |

  

| `logs/Latest.log` | All (cleared on startup) |

  

| `logs/debug.log` | DEBUG only |

  
  

- Console shows only DEBUG.

  

- All log records carry a custom `guild` field (default `"Core"`).

  

- Global singleton via `get_logger()`.

  
  

---

  
  

### `modules/utils.py` — Shared Utilities

  
  

| Function / Class | Purpose |

  

|---|---|

  

| `setup_guild(guild_id)` | Creates `guilds/{id}/config.json` with defaults |

  

| `load_guilds(bot)` | Loads all guild directories into a dict |

  

| `save_battle_result` | Writes `guilds/{id}/{folder}/battle-<timestamp>.txt` (metadata JSON on line 1, AI story on remaining lines) |

  

| `append_suggestion_record` | Thread-safe append to `generic/suggestions.json` |

  

| `load_suggestions / find_suggestion_by_id / update_suggestion_record` | CRUD helpers for suggestions |

  

| `ProcessCommand(...)` | Decorator that handles guild checks, permission checks, dev-mode gating, logging, and error responses before delegating to the real handler |

  

| `sendMessage / editMessage` | Safe wrappers that handle permissions, auto-split messages > 2000 chars |

  

| `BattleMetadata` | Serialisable metadata attached to every saved battle |

  
  

---

  
  

### `web/server.py` — FastAPI Application

  

Runs in a daemon thread spawned by `app.py`. Routes:

  
  

**Page routes (HTML):**

  

- `GET /` → `index.html`

  

- `GET /dashboard` → `dashboard.html`

  

- `GET /guilds` → `guilds.html`

  

- `GET /performance` → `performance.html`

  

- `GET /webhook` → `webhook.html`

  

- `GET /suggestions` → `suggestions.html`

  
  

**API + WebSocket:**

  

- `GET /api/health`

  

- `GET /api/bot/info`

  

- `WS /ws/logs` — live log stream powered by `WebSocketLogHandler` → `asyncio.Queue` → broadcaster task

  
  

---

  
  

### `web/routes/` — API Route Modules

  
  

| Module | Prefix | Key endpoints |

  

|---|---|---|

  

| `dashboard.py` | `/api` | `GET /metrics`, `GET /metrics/history?minutes=`, `GET /logs?lines=` |

  

| `guilds.py` | `/api` | `GET /guilds`, `GET /guilds/{id}` |

  

| `webhook.py` | `/api/webhook` | `POST /send` (announcements), `POST /update` (release notes), `GET /version` |

  

| `suggestions.py` | `/api` | `GET /suggestions`, `GET /suggestions/{id}`, `POST /suggestions/{id}/respond` |

  
  

---

  
  

### `web/metrics.py` — `MetricsCollector`

  

- Background thread samples CPU, memory, and Discord latency every `METRICS_COLLECTION_INTERVAL` seconds (default 2 s).

  

- Keeps up to 24 h of in-memory history in `deque(maxlen=43200)`.

  

- Optional in-memory compression: recent data kept at full resolution, 1–6 h data averaged over 10-sample windows, older data averaged over 60-sample windows.

  

- Flushes to SQLite every 10 s via `MetricsDB`.

  
  

### `web/persistence.py` — `MetricsDB`

  

- SQLite wrapper with a single `metrics` table: `(id, timestamp, cpu, memory_mb, memory_percent, latency)`.

  

- Indexed on `timestamp`.

  

- Auto-cleans rows older than `METRICS_RETENTION_DAYS` (default 7 days).

  

- Gracefully self-disables if any DB operation fails.

  
  

### `web/bot_bridge.py` — Bot ↔ Web Bridge

  

Holds a global reference to the running `DiscordBot` instance. Used by the suggestions API to:

  

- Send DMs to users from the web panel.

  

- Attach `SuggestionFollowupView` (button → modal) so users can reply to staff responses.

  
  

---

  
  

## Data Storage

  
  

| What | Where | Format |

  

|---|---|---|

  

| Guild configuration | `guilds/{guild_id}/config.json` | JSON |

  

| Battle results | `guilds/{guild_id}/quick-battle/battle-<ts>.txt` | Line 1: JSON metadata; rest: AI story text |

  

| User suggestions | `generic/suggestions.json` | JSON array |

  

| Performance metrics | `metrics.db` (SQLite, configurable) | SQLite table |

  

| Log files | `logs/` (Errors.log, Log.log, Latest.log, debug.log) | Plain text, rotating (10 MB max, 5 backups) |

  

| Bot identity | `bot.json` | JSON (name, id, version, invite link) |

  

| Update changelogs | `updates/<version>-<name>.md` | Markdown |

  

| Localisations | `lang/en.json`, `lang/es.json`, `lang/ua.json` | JSON (nested keys) |

  

| AI prompts | `prompts/**/*.txt` | Plain text instruction files |

  
  

---

  
  

## Slash Commands Reference

  
  

| Command | Who can use | Description |

  

|---|---|---|

  

| `/ping` | Admin | Returns bot latency in ms |

  

| `/config` | Admin | Interactive guild configuration (AI key, language, webhook) |

  

| `/quick-battle` | Anyone (guild must be enabled) | Start a multiplayer AI-judged battle |

  

| `/suggest` | Anyone (no guild required) | Submit a feature request or bug report |

  
  

---

  
  

## Environment Variables

  
  

| Variable | Default | Description |

  

|---|---|---|

  

| `API_TOKEN` | *(required)* | Discord bot token |

  

| `WEB_ENABLED` | `true` | Enable the FastAPI web dashboard |

  

| `WEB_HOST` | `0.0.0.0` | Web server bind host |

  

| `WEB_PORT` | `20000` | Web server port |

  

| `WEB_PORT_DEV` | `20001` | Port used in `--dev` mode |

  

| `DEV_GUILD_ID` | *(none)* | Guild ID that receives commands in dev mode |

  

| `METRICS_DB_PATH` | `metrics.db` | Path to SQLite metrics database |

  

| `METRICS_COLLECTION_INTERVAL` | `2` | Seconds between metric samples |

  

| `METRICS_RETENTION_DAYS` | `7` | How long to keep metrics in SQLite |

| `METRICS_COMPRESSION_ENABLED` | `false` | Enable in-memory history compression |

  
  

---

  
  

## How the Parts Connect (Call Flow Summary)

  
  

```

app.py

  

 └─ creates DiscordBot (modules/main.py)

  

     ├─ BattleHandler (modules/BattleHandler.py)

     │   └─ /quick-battle command

     │       ├─ EnvironmentCreator / FighterCreator / StrategyCreator

     │       │   └─ Discord UI modals & embeds

     │       └─ PromptHandler (modules/PromptHandler.py)

     │           └─ AIHandler (modules/AIHandler.py)

     │               └─ Google Gemini API (HTTP)

     ├─ ConfigurationHandler (modules/ConfigurationHandler.py)

     │   └─ /config command → writes guilds/{id}/config.json

     └─ LocalizationHandler (modules/LocalizationHandler.py)

         └─ lang/*.json files

  
  

 └─ web/server.py (FastAPI, daemon thread)

     ├─ web/routes/dashboard.py  → web/metrics.py → web/persistence.py (SQLite)

     ├─ web/routes/guilds.py     → bot.guilds_data (in-memory)

     ├─ web/routes/webhook.py    → aiohttp → Discord Webhook URLs

     ├─ web/routes/suggestions.py → generic/suggestions.json

     └─ web/bot_bridge.py        → bot.loop (schedule DMs cross-thread)

```

  
  

Every guild is isolated: its config, battle history, and webhook are all namespaced under `guilds/{guild_id}/`. The web panel and the bot share state only through the in-memory `bot` object reference held by `bot_bridge.py` and `MetricsCollector`.

  

"""