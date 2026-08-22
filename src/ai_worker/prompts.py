"""Safe, deterministic loading and assembly of mounted graph prompts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path


class PromptLoadError(ValueError):
    """A prompt path or template is missing, invalid, or escapes the prompt root."""


class PromptLoader:
    """Loads target-layout prompt files only from an explicit mounted root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def read(self, relative_path: str) -> str:
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise PromptLoadError("prompt path traversal is not allowed")
        resolved = (self._root / path).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise PromptLoadError("prompt path escapes the prompt root") from exc
        if not resolved.is_file():
            raise PromptLoadError(f"prompt file is missing: {relative_path}")
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PromptLoadError(f"prompt file is not valid UTF-8: {relative_path}") from exc
        if not content.strip():
            raise PromptLoadError(f"prompt file is empty: {relative_path}")
        return content

    def element(self, relative_path: str, values: Mapping[str, str] | None = None) -> str:
        template = self.read(relative_path)
        try:
            return template.format(**(values or {}))
        except (KeyError, ValueError) as exc:
            raise PromptLoadError(f"invalid element template: {relative_path}") from exc

    def assemble(
        self,
        *,
        base_path: str,
        criteria_path: str | None = None,
        elements: Sequence[tuple[str, Mapping[str, str] | None]] = (),
    ) -> str:
        """Join base, optional criteria, then labelled element blocks in caller order."""
        parts = [self.read(base_path)]
        if criteria_path is not None:
            parts.append(self.read(criteria_path))
        parts.extend(self.element(path, values) for path, values in elements)
        return "\n\n".join(parts)


__all__ = ["PromptLoadError", "PromptLoader"]
