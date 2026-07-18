# DiscordCombatAI

Highly available, distributed AI-powered Discord bot.

This repository currently contains **two layouts**:

1. **Legacy monolith** (still runnable): `app.py`, `modules/`, root `web/`, `requirements.txt`.
2. **Target architecture** (Phase 0 foundation in progress): `src/`, `infra/`, `tests/`, `pyproject.toml`, Compose.

Do not mix imports between the layouts. New shared code lives under `src/shared/`.

## Target architecture (Phase 0+)

### Prerequisites

- Python **3.11+**
- [`uv`](https://docs.astral.sh/uv/) (preferred) or an equivalent locked environment
- Docker Compose (for broker integration tests)

### Install

```bash
uv sync --all-extras
```

### Checks

```bash
uv run ruff check .
uv run mypy src
uv run pytest tests/unit
```

Broker integration tests (requires Docker):

```bash
uv run pytest tests/integration -m integration
```

Live Azure tests are **opt-in only** (`-m azure_live`) and must never run by default.

### Configuration

Copy `.env.example` to `.env` and fill secrets. Azure variable definitions are owned by `docs/containers/azure.md`.

### Compose

```bash
docker compose up -d mosquitto rabbitmq
```

Application services (`head`, `bot`, `ai_worker`) are behind the `application` Compose profile until their Phase 1/2 images exist.

## Legacy monolith (unchanged)

1. Create a Discord application and bot, then invite it with the `applications.commands` scope.
2. Copy `.env.example` / set `API_TOKEN` for the legacy app (see historical notes below).
3. Install legacy deps: `pip install -r requirements.txt`
4. Run: `python app.py`

Legacy localization lives in `lang/<locale>.json` and is used by `modules/LocalizationHandler.py`. The target Bot localization path is `src/bot/localization/` (not migrated in Phase 0).

## Documentation

Start at [`docs/Readme.md`](docs/Readme.md). Implementation sequencing is in [`docs/to_resolve.md`](docs/to_resolve.md).
