from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_bot_dockerfile_is_non_root_and_uses_bot_extra() -> None:
    content = (ROOT / "src" / "bot" / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.11-slim" in content
    assert "uv sync --frozen --no-dev --extra bot" in content
    assert "USER 65532:65532" in content
    assert "entrypoint.sh" in content
    assert "AI_WORKER_TRANSPORT_SHELL" not in content


def test_ai_worker_dockerfile_defaults_shell_false() -> None:
    content = (ROOT / "src" / "ai_worker" / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.11-slim" in content
    assert "uv sync --frozen --no-dev --extra ai-worker" in content
    assert "USER 65532:65532" in content
    assert "AI_WORKER_TRANSPORT_SHELL=false" in content
    assert "entrypoint.sh" in content


def test_entrypoint_scripts_use_uv_no_sync() -> None:
    bot = (ROOT / "src" / "bot" / "entrypoint.sh").read_text(encoding="utf-8")
    worker = (ROOT / "src" / "ai_worker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "uv run --no-sync python -m bot.main" in bot
    assert "uv run --no-sync python -m ai_worker.main" in worker


def test_web_dockerfile_is_multistage_non_root_and_uses_web_extra() -> None:
    content = (ROOT / "src" / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert "node:20-bookworm-slim AS frontend" in content
    assert "npm ci" in content
    assert "npm test -- --run && npm run build" in content
    assert "python:3.11-slim AS runtime" in content
    assert "uv sync --frozen --no-dev --extra web" in content
    assert "USER 65532:65532" in content
    assert "entrypoint.sh" in content
    assert "/app/src/web/frontend/dist" in content


def test_web_entrypoint_uses_uvicorn_factory() -> None:
    web = (ROOT / "src" / "web" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "web.backend.main:create_app" in web
    assert "--factory" in web
    assert "WEB_PORT" in web
