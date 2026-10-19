"""Provider-neutral interface for structured JSON generation, plus model pricing.

Business logic (prompts, validation, caching) lives in services/digest.py; adapters only turn a
request into one API call and map failures to LLMError with a message fit to show the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    input_per_mtok: float  # USD per million input tokens
    output_per_mtok: float  # USD per million output tokens (thinking included)
    server_fallback: bool  # supports the server-side refusal fallback
    note: str


MODELS: dict[str, ModelInfo] = {
    m.id: m
    for m in (
        ModelInfo("claude-opus-5-5", "Claude Opus 5.5", 4.00, 20.00, True, "Most careful reading. Recommended."),
        ModelInfo("claude-sonnet-5-5", "Claude Sonnet 5.5", 2.00, 10.00, True, "Faster, about half the cost of Opus."),
        ModelInfo("claude-haiku-5-5", "Claude Haiku 5.5", 0.10, 0.50, False, "Cheapest; fine for quick abstract screening."),
    )
}
DEFAULT_MODEL = "claude-opus-5-5"


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    info = MODELS.get(model)
    if info is None or input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * info.input_per_mtok + output_tokens * info.output_per_mtok) / 1_000_000


class LLMError(Exception):
    """A generation failure. `str(error)` is safe and useful to show the user."""

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind  # not_configured, auth, permission, model, rate_limit, overloaded, timeout,
        # network, refusal, truncated, bad_request, invalid_output, server


@dataclass(frozen=True)
class LLMRequest:
    system: str
    prompt: str
    schema: dict
    effort: str = "medium"
    max_tokens: int = 16000


@dataclass(frozen=True)
class LLMResponse:
    data: dict
    model: str  # the model that actually answered (a refusal fallback may substitute one)
    input_tokens: int | None
    output_tokens: int | None
    request_id: str | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    def generate_json(self, request: LLMRequest) -> LLMResponse: ...
