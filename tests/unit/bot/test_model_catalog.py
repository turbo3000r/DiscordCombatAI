"""Model catalog unit tests."""

from __future__ import annotations

from types import SimpleNamespace

from bot.modules.commands.config.service.model_catalog import (
    ModelListResult,
    ProviderFailureKind,
)


def _filter_models(models: list[SimpleNamespace]) -> ModelListResult:
    filtered: dict[str, str] = {}
    for model in models:
        model_id = str(model.name).split("/")[-1]
        if "gemini" not in model_id.lower():
            continue
        actions = model.supported_actions
        if not any("generatecontent" in str(a).lower() for a in actions):
            continue
        filtered[model_id] = model_id
    ordered = sorted(filtered.items(), key=lambda item: item[0])
    total = len(ordered)
    return ModelListResult(options=ordered[:25], total=total, truncated=total > 25)


def test_list_filters_sorts_dedupes_and_caps() -> None:
    models = [
        SimpleNamespace(name="models/zeta-gemini", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/alpha-gemini", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/alpha-gemini", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/other", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/gemini-no", supported_actions=["embedContent"]),
    ]
    for i in range(30):
        models.append(
            SimpleNamespace(
                name=f"models/gemini-extra-{i:02d}",
                supported_actions=["generateContent"],
            )
        )
    result = _filter_models(models)
    assert result.truncated is True
    assert len(result.options) == 25
    assert result.options == sorted(result.options, key=lambda x: x[0])
    values = [v for v, _ in result.options]
    assert len(values) == len(set(values))


def test_provider_failure_kinds_exist() -> None:
    assert ProviderFailureKind.invalid_key.value == "invalid_key"
