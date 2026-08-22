"""Graph-neutral bounded Decider node."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ai_worker.graphs.foundation import (
    AttemptRecord,
    DeciderSelection,
    GraphExecutionContext,
    NodeExecutionError,
    validate_decider_pool,
)
from ai_worker.llm import GeminiClient, ResourceLedger, StructuredOutputError, invoke_structured


def decide_candidate(
    *,
    context: GraphExecutionContext,
    base_prompt: str,
    criteria: str,
    attempts: Sequence[AttemptRecord],
    candidate_serializer: Callable[[AttemptRecord], str],
    max_retries: int,
    client: GeminiClient | None = None,
    sleep: Callable[[float], None] | None = None,
    ledger: ResourceLedger | None = None,
    max_output_tokens: int | None = None,
) -> DeciderSelection:
    """Make one structured selection from the complete, bounded attempt pool."""
    try:
        validate_decider_pool(attempts)
    except ValueError as exc:
        raise NodeExecutionError(context.executing_node, str(exc)) from exc
    prompt = f"{base_prompt}\n\n{criteria}\n\n" + "\n\n".join(
        candidate_serializer(attempt) for attempt in attempts
    )
    allowed_indexes = {attempt.attempt_index for attempt in attempts}

    def validate_selection(selection: DeciderSelection) -> None:
        if selection.selected_attempt_index not in allowed_indexes:
            raise StructuredOutputError("Decider selected an unknown attempt index")

    if sleep is None:
        return invoke_structured(
            context=context,
            prompt=prompt,
            schema=DeciderSelection,
            client=client,
            max_retries=max_retries,
            validate=validate_selection,
            ledger=ledger,
            max_output_tokens=max_output_tokens,
        )
    return invoke_structured(
        context=context,
        prompt=prompt,
        schema=DeciderSelection,
        client=client,
        max_retries=max_retries,
        sleep=sleep,
        validate=validate_selection,
        ledger=ledger,
        max_output_tokens=max_output_tokens,
    )


__all__ = ["decide_candidate"]
