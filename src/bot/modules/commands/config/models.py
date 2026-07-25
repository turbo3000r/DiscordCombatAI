"""/config apply helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.modules.commands.config.UI.state import ConfigDraft
from shared.models.localization import LanguageCode
from shared.models.web_auth import is_allowed_discord_webhook_url


@dataclass(slots=True)
class ApplyPlan:
    patch: dict[str, Any] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    needs_probe: bool = False
    probe_key: str | None = None
    probe_model: str | None = None
    resulting_enabled: bool = False


def build_apply_plan(
    *,
    current_api_key: str,
    current_model: str,
    current_language: str,
    current_webhook: str,
    current_enabled: bool,
    draft: ConfigDraft,
    force_disable: bool = False,
) -> ApplyPlan:
    plan = ApplyPlan()

    # Language
    if draft.staged_language is not None and draft.staged_language != current_language:
        try:
            LanguageCode(draft.staged_language)
            plan.patch["language"] = draft.staged_language
            plan.applied.append("language")
        except Exception:
            plan.rejected.append("language")

    # Webhook
    if draft.staged_webhook is not None and draft.staged_webhook != current_webhook:
        if is_allowed_discord_webhook_url(draft.staged_webhook):
            plan.patch["webhook_url"] = draft.staged_webhook
            plan.applied.append("webhook_url")
        else:
            plan.rejected.append("webhook_url")

    staged_key = draft.staged_api_key
    staged_model = draft.staged_model
    effective_key = staged_key if staged_key is not None else current_api_key
    effective_model = staged_model if staged_model is not None else current_model

    key_changing = staged_key is not None and staged_key != current_api_key
    model_changing = staged_model is not None and staged_model != current_model

    if key_changing or model_changing:
        plan.needs_probe = True
        plan.probe_key = effective_key
        plan.probe_model = effective_model
        # Actual inclusion of key/model into patch happens after probe success.

    if force_disable:
        plan.patch["enabled"] = False
        plan.applied.append("enabled")
        plan.resulting_enabled = False
    else:
        plan.resulting_enabled = bool(effective_key.strip() and effective_model.strip())
        if plan.resulting_enabled != current_enabled and not plan.needs_probe:
            # enabled can flip without probe when no key/model change
            if plan.resulting_enabled and effective_key and effective_model:
                plan.patch["enabled"] = True
                plan.applied.append("enabled")
            elif not plan.resulting_enabled:
                plan.patch["enabled"] = False
                plan.applied.append("enabled")

    return plan


def commit_probe_success(plan: ApplyPlan, draft: ConfigDraft, current_enabled: bool) -> ApplyPlan:
    if draft.staged_api_key is not None:
        plan.patch["api_key"] = draft.staged_api_key
        plan.applied.append("api_key")
    if draft.staged_model is not None:
        plan.patch["model"] = draft.staged_model
        plan.applied.append("model")
    key = plan.patch.get("api_key") or draft.staged_api_key
    model = plan.patch.get("model") or draft.staged_model
    # Prefer effective from plan probes
    effective_key = plan.probe_key or ""
    effective_model = plan.probe_model or ""
    enabled = bool(str(effective_key).strip() and str(effective_model).strip())
    plan.resulting_enabled = enabled
    if enabled != current_enabled or enabled:
        plan.patch["enabled"] = enabled
        if "enabled" not in plan.applied:
            plan.applied.append("enabled")
    _ = key, model
    return plan


def reject_key_model(plan: ApplyPlan) -> ApplyPlan:
    plan.needs_probe = False
    plan.rejected.extend([f for f in ("api_key", "model") if f not in plan.rejected])
    return plan


__all__ = ["ApplyPlan", "build_apply_plan", "commit_probe_success", "reject_key_model"]
