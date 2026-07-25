"""Bot composition root — selects repositories and isolation knobs by runtime mode."""

from __future__ import annotations

import logging

import httpx

from shared.runtime.guards import RuntimeConfigurationError, assert_service_runtime
from shared.runtime.settings import RuntimeMode, RuntimeSettings, load_runtime_settings
from shared.storage import build_repositories

from .application import BotApplication
from .modules.client import CombatBot, create_bot
from .settings import BotSettings

logger = logging.getLogger(__name__)


async def _require_dev_support_health(url: str) -> None:
    health_url = f"{url.rstrip('/')}/internal/v1/health"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(health_url)
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeConfigurationError(
            f"dev-support is not reachable at {health_url}: {exc}"
        ) from exc


def build_bot_application(
    *,
    bot_settings: BotSettings | None = None,
    runtime: RuntimeSettings | None = None,
    enable_mqtt: bool = True,
    enable_transport: bool = True,
) -> BotApplication:
    runtime = runtime or load_runtime_settings()
    assert_service_runtime(runtime, "bot")
    settings = bot_settings or BotSettings()  # type: ignore[call-arg]
    repos = build_repositories("bot", runtime)

    development_guild_id = runtime.discord_development_guild_id
    expected_application_id = runtime.discord_development_application_id
    mode = runtime.mode

    def _factory() -> CombatBot:
        return create_bot(
            runtime_mode=mode,
            development_guild_id=development_guild_id,
            expected_application_id=expected_application_id,
        )

    app = BotApplication(
        settings,
        guild_service=repos.guild,
        status_service=repos.status,
        bot_factory=_factory,
        enable_mqtt=enable_mqtt,
        enable_transport=enable_transport,
        runtime_mode=mode,
        development_guild_id=development_guild_id,
        expected_application_id=expected_application_id,
        # Suggestion queue poller must not start in development (suppressed).
        enable_suggestion_queue=mode is RuntimeMode.production,
    )
    app._runtime = runtime  # type: ignore[attr-defined]
    app._dev_support_url = runtime.dev_support_url  # type: ignore[attr-defined]
    return app


async def prepare_bot_application(app: BotApplication) -> None:
    runtime: RuntimeSettings | None = getattr(app, "_runtime", None)
    if runtime is None or runtime.mode is not RuntimeMode.development:
        return
    url = getattr(app, "_dev_support_url", None)
    if not url:
        raise RuntimeConfigurationError("DEV_SUPPORT_URL missing for development Bot")
    await _require_dev_support_health(url)


__all__ = ["build_bot_application", "prepare_bot_application"]
