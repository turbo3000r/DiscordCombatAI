# DiscordCombatAI

Highly available, distributed AI-powered Discord bot.

This repository currently contains **two layouts**:

1. **Legacy monolith** (still runnable): `app.py`, `modules/`, root `web/`, `requirements.txt`.
2. **Target architecture** (Phases 0–3 implemented under `src/`; Phase 4 AI graphs and Phase 5 `/quick-battle` not started): `src/`, `launcher/`, `infra/`, `tests/`, `pyproject.toml`, Compose.

Do not mix imports between the layouts. New shared code lives under `src/shared/`.

## Target architecture

Documentation sequencing: [`docs/to_resolve.md`](docs/to_resolve.md). Canonical docs start at [`docs/Readme.md`](docs/Readme.md).

| Phase | Status |
| --- | --- |
| 0 Foundation / contracts | Implemented |
| 1 Launcher / Head | Implemented |
| 2 Bot / AI Worker transport | Implemented (transport shell; no real graphs) |
| 2.5 Local development spine | Implemented (S14 spine; remaining S14 cases wait on later phases) |
| 3 `/config` + `/suggest` + Web Suggestions | Implemented |
| 4 AI graphs | Not started (blocked on P1.2) |
| 5 `/quick-battle` | Not started (blocked on P1.1 / P1.2) |
| 6 Remaining Web pages / S13 | Partial (auth shell + Suggestions only) |

### Prerequisites

- Python **3.11+**
- [`uv`](https://docs.astral.sh/uv/) (preferred) or an equivalent locked environment
- Go **1.25+** (Launcher)
- Node **20+** (Web frontend)
- Docker Compose (for broker integration tests)

### Install

```bash
uv sync --all-extras
```

### Checks

Target tree (matches CI Python job, plus tests):

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest tests/unit tests/acceptance
```

Launcher:

```bash
cd launcher
go vet ./...
go test ./...
```

Broker integration tests (requires Docker; publishes loopback ports via the development overlay):

```bash
uv run pytest tests/integration -m integration
```

Web frontend:

```bash
cd src/web/frontend
npm ci
npm run build
npm run test -- --run
```

Live Azure tests are **opt-in only** (`-m azure_live`) and must never run by default.

`uv run ruff check .` also lints the legacy monolith (`app.py`, `modules/`, root `web/`) and is not the CI gate.

### Configuration

Copy `.env.example` to `.env` and fill secrets. Azure variable definitions are owned by `docs/containers/azure.md`. `DISCORD_DEVELOPMENT_GUILD_ID` is required at **process start** for Bot, Web, and `dev-support`; Compose broker/Head commands do not require it to parse.

### Compose

Brokers only (default production file; Head is not started unless named or using a full `up`):

```bash
docker compose up -d mosquitto rabbitmq
```

Local loopback ports and development services use the overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d mosquitto rabbitmq
```

- **Production file:** Head starts with the default project (not profile-gated). Bot and AI Worker use the `application` profile.
- **Development overlay:** Head moves to the `production` profile (absent from the merged development stack). Bot, AI Worker, Web, and `dev-support` use `application` / `development` profiles and local image builds.

## Legacy monolith (unchanged)

1. Create a Discord application and bot, then invite it with the `applications.commands` scope.
2. Copy `.env.example` / set `API_TOKEN` for the legacy app (see historical notes below).
3. Install legacy deps: `pip install -r requirements.txt`
4. Run: `python app.py`

Legacy localization lives in `lang/<locale>.json` and is used by `modules/LocalizationHandler.py`. Target Bot localization lives in `src/bot/localization/`.

## Documentation

Start at [`docs/Readme.md`](docs/Readme.md). Implementation sequencing is in [`docs/to_resolve.md`](docs/to_resolve.md).
