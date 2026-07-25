"""Config apply planning tests."""

from __future__ import annotations

from bot.modules.commands.config.models import build_apply_plan, commit_probe_success
from bot.modules.commands.config.UI.state import ConfigDraft


def test_partial_apply_rejects_bad_webhook_keeps_language() -> None:
    draft = ConfigDraft(
        language="en",
        staged_language="es",
        staged_webhook="https://evil.example/hook",
    )
    plan = build_apply_plan(
        current_api_key="k",
        current_model="m",
        current_language="en",
        current_webhook="",
        current_enabled=True,
        draft=draft,
    )
    assert "language" in plan.applied
    assert "webhook_url" in plan.rejected
    assert plan.patch.get("language") == "es"
    assert "webhook_url" not in plan.patch


def test_probe_success_commits_key_model() -> None:
    draft = ConfigDraft(staged_api_key="new-key", staged_model="gemini-2.0-flash")
    plan = build_apply_plan(
        current_api_key="old",
        current_model="old-model",
        current_language="en",
        current_webhook="",
        current_enabled=False,
        draft=draft,
    )
    assert plan.needs_probe is True
    commit_probe_success(plan, draft, current_enabled=False)
    assert plan.patch["api_key"] == "new-key"
    assert plan.patch["model"] == "gemini-2.0-flash"
    assert plan.patch["enabled"] is True
