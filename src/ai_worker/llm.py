"""Gemini structured-output invocation shared by all Phase 4 graph nodes."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from shared.security.redact import redact_sensitive

from .graphs.foundation import GraphExecutionContext, NodeExecutionError

T = TypeVar("T", bound=BaseModel)
LOGGER = logging.getLogger(__name__)


class GeminiClient(Protocol):
    """Small fakeable surface; a new client is created for each task credential."""

    def generate_structured(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        response_schema: type[BaseModel],
        max_output_tokens: int,
    ) -> GeminiResponse | str:
        """Return the provider's JSON object response."""


class StructuredOutputError(ValueError):
    """The provider replied, but not with one valid strict JSON object."""


@dataclass(frozen=True)
class GeminiUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class GeminiResponse:
    text: str
    usage: GeminiUsage


class ResourceLedger:
    """Reusable monotonic deadline and cumulative provider-usage ledger."""

    def __init__(
        self,
        *,
        deadline_sec: float,
        max_input_tokens: int,
        max_output_tokens: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._deadline = clock() + deadline_sec
        self._max_input = max_input_tokens
        self._max_output = max_output_tokens
        self.input_tokens = 0
        self.output_tokens = 0

    def reserve_attempt(self, *, node: str, max_output_tokens: int) -> None:
        if self._clock() >= self._deadline:
            raise NodeExecutionError(node, "graph deadline exhausted before LLM attempt")
        if self.output_tokens + max_output_tokens > self._max_output:
            raise NodeExecutionError(node, "cumulative output token budget exhausted")

    def can_wait(self, *, node: str, delay: float) -> None:
        if self._clock() + delay >= self._deadline:
            raise NodeExecutionError(node, "graph deadline cannot accommodate LLM retry backoff")

    def record_usage(self, *, node: str, usage: GeminiUsage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        if self.input_tokens > self._max_input:
            raise NodeExecutionError(node, "cumulative input token budget exhausted")
        if self.output_tokens > self._max_output:
            raise NodeExecutionError(node, "cumulative output token budget exhausted")


class GoogleGeminiClient:
    """google-genai adapter. It never stores a process-wide credential."""

    def generate_structured(
        self, *, api_key: str, model: str, prompt: str, response_schema: type[BaseModel],
        max_output_tokens: int,
    ) -> GeminiResponse:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=response_schema,
                max_output_tokens=max_output_tokens,
            ),
        )
        if not response.text:
            raise StructuredOutputError("Gemini returned an empty structured response")
        metadata = response.usage_metadata
        return GeminiResponse(
            text=response.text,
            usage=GeminiUsage(
                input_tokens=int(getattr(metadata, "prompt_token_count", 0) or 0),
                output_tokens=int(getattr(metadata, "candidates_token_count", 0) or 0),
            ),
        )


def _no_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StructuredOutputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_structured_output(raw: str, schema: type[T]) -> T:
    """Strictly parse one JSON object; schemas own graph-specific bounds."""
    try:
        decoded = json.loads(raw, object_pairs_hook=_no_duplicate_object_pairs)
    except (json.JSONDecodeError, StructuredOutputError) as exc:
        raise StructuredOutputError("response is not one valid JSON object") from exc
    if not isinstance(decoded, dict):
        raise StructuredOutputError("response must be one JSON object")
    try:
        return schema.model_validate(decoded, strict=True)
    except ValidationError as exc:
        raise StructuredOutputError("response does not satisfy the structured schema") from exc


def _is_transient_gemini_failure(error: Exception) -> bool:
    """Retry only documented transient HTTP/provider failures."""
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    return isinstance(status, int) and status in {408, 429, 500, 502, 503, 504}


def _backoff_seconds(retry_number: int) -> float:
    """Deterministic exponential retry backoff: 1s, 2s, then 4s."""
    return float(2**retry_number)


def invoke_structured(
    *,
    context: GraphExecutionContext,
    prompt: str,
    schema: type[T],
    client: GeminiClient | None = None,
    max_retries: int,
    sleep: Callable[[float], None] = time.sleep,
    validate: Callable[[T], None] | None = None,
    ledger: ResourceLedger | None = None,
    max_output_tokens: int | None = None,
) -> T:
    """Call Gemini with one initial attempt plus the shared retry budget."""
    provider = client or GoogleGeminiClient()
    for retry_number in range(max_retries + 1):
        failure: Exception
        try:
            if ledger is not None:
                if max_output_tokens is None:
                    raise ValueError("ledger calls require max_output_tokens")
                ledger.reserve_attempt(
                    node=context.executing_node,
                    max_output_tokens=max_output_tokens,
                )
            request: dict[str, Any] = {
                "api_key": context.api_key.get_secret_value(),
                "model": context.model,
                "prompt": prompt,
                "response_schema": schema,
            }
            if max_output_tokens is not None:
                request["max_output_tokens"] = max_output_tokens
            response = provider.generate_structured(**request)
            if isinstance(response, str):
                raw = response
            else:
                raw = response.text
                if ledger is not None:
                    ledger.record_usage(node=context.executing_node, usage=response.usage)
            parsed = parse_structured_output(raw, schema)
            if validate is not None:
                validate(parsed)
            return parsed
        except StructuredOutputError as exc:
            retryable = True
            failure = exc
        except Exception as exc:
            retryable = _is_transient_gemini_failure(exc)
            failure = exc

        LOGGER.warning(
            "Gemini structured call failed node=%s retry=%s reason=%s",
            context.executing_node,
            retry_number,
            redact_sensitive(str(failure)),
        )
        if not retryable or retry_number == max_retries:
            raise NodeExecutionError(
                context.executing_node,
                "structured LLM call failed after retry budget exhaustion"
                if retryable
                else "Gemini returned a permanent API failure",
            ) from failure
        delay = _backoff_seconds(retry_number)
        if ledger is not None:
            ledger.can_wait(node=context.executing_node, delay=delay)
        sleep(delay)

    raise AssertionError("unreachable")


__all__ = [
    "GeminiClient",
    "GoogleGeminiClient",
    "GeminiResponse",
    "GeminiUsage",
    "ResourceLedger",
    "StructuredOutputError",
    "invoke_structured",
    "parse_structured_output",
]
