from __future__ import annotations

from bot.modules.services.guild_guard import is_managed_guild
from shared.runtime.settings import RuntimeMode

DEV_GUILD = "111111111111111111"
OTHER = "222222222222222222"


def test_development_accepts_only_configured_guild() -> None:
    assert is_managed_guild(
        mode=RuntimeMode.development,
        guild_id=DEV_GUILD,
        development_guild_id=DEV_GUILD,
    )
    assert not is_managed_guild(
        mode=RuntimeMode.development,
        guild_id=OTHER,
        development_guild_id=DEV_GUILD,
    )


def test_production_rejects_reserved_guild() -> None:
    assert not is_managed_guild(
        mode=RuntimeMode.production,
        guild_id=DEV_GUILD,
        development_guild_id=DEV_GUILD,
    )
    assert is_managed_guild(
        mode=RuntimeMode.production,
        guild_id=OTHER,
        development_guild_id=DEV_GUILD,
    )
