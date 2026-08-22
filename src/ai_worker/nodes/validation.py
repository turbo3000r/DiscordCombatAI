"""Graph-neutral Validator node."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ai_worker.graphs.foundation import GraphExecutionContext, ValidatorVerdict
from ai_worker.llm import GeminiClient, ResourceLedger, invoke_structured


def validate_candidate(
    *,
    context: GraphExecutionContext,
    base_prompt: str,
    criteria: str,
    candidate: Any,
    candidate_serializer: Callable[[Any], str],
    max_retries: int,
    client: GeminiClient | None = None,
    sleep: Callable[[float], None] | None = None,
    ledger: ResourceLedger | None = None,
    max_output_tokens: int | None = None,
) -> ValidatorVerdict:
    """Judge exactly the candidate representation and criteria supplied by a graph."""
    prompt = f"{base_prompt}\n\n{criteria}\n\n{candidate_serializer(candidate)}"
    if sleep is None:
        return invoke_structured(
            context=context,
            prompt=prompt,
            schema=ValidatorVerdict,
            client=client,
            max_retries=max_retries,
            ledger=ledger,
            max_output_tokens=max_output_tokens,
        )
    return invoke_structured(
        context=context,
        prompt=prompt,
        schema=ValidatorVerdict,
        client=client,
        max_retries=max_retries,
        sleep=sleep,
        ledger=ledger,
        max_output_tokens=max_output_tokens,
    )


__all__ = ["validate_candidate"]
