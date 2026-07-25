"""Smoke test: target packages are importable after editable install."""

from __future__ import annotations


def test_shared_package_imports() -> None:
    import shared
    import shared.models
    import shared.utils

    assert shared.__name__ == "shared"
    assert shared.models.__name__ == "shared.models"
    assert shared.utils.__name__ == "shared.utils"


def test_service_packages_import() -> None:
    import ai_worker
    import ai_worker.settings
    import bot
    import bot.application
    import bot.settings
    import head

    assert head.__name__ == "head"
    assert bot.__name__ == "bot"
    assert ai_worker.__name__ == "ai_worker"
    assert bot.settings.BotSettings.__name__ == "BotSettings"
    assert ai_worker.settings.AiWorkerSettings.__name__ == "AiWorkerSettings"
    assert bot.application.BotApplication.__name__ == "BotApplication"
