"""Claude adapter: one Messages API call with a JSON-schema output format.

- Refusal fallback: on models that support it, `fallbacks="default"` lets the API re-run a
  request that a safety classifier declined on Anthropic's recommended substitute model.
- Thinking is adaptive by default on current models; depth is set with `effort`.
- Errors are mapped to LLMError messages that never include credentials.
"""

from __future__ import annotations

import json

import anthropic

from paper_radar.llm import credentials
from paper_radar.llm.base import MODELS, LLMError, LLMRequest, LLMResponse

FALLBACK_BETA = "server-side-fallback-2026-07-01"
NOT_CONFIGURED = "No Claude API key is set up yet. Add one in Settings → AI digests."


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, *, client: anthropic.Anthropic | None = None, timeout: float = 300.0):
        self.model = model
        self._client = client
        self._timeout = timeout

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not credentials.status().configured:
                raise LLMError(NOT_CONFIGURED, "not_configured")
            self._client = anthropic.Anthropic(timeout=self._timeout, max_retries=2)
        return self._client

    def _request_kwargs(self, request: LLMRequest) -> dict:
        kwargs = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.prompt}],
            "output_config": {
                "effort": request.effort,
                "format": {"type": "json_schema", "schema": request.schema},
            },
        }
        info = MODELS.get(self.model)
        if info is None or info.server_fallback:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        try:
            response = self.client.beta.messages.create(**self._request_kwargs(request))
        except anthropic.AuthenticationError as exc:
            raise LLMError("Claude rejected the API key. Check or replace it in Settings → AI digests.", "auth") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError("This API key isn't allowed to use that model.", "permission") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"The model {self.model} isn't available to this API key. Choose another in Settings.", "model") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Claude's rate limit was reached. Wait a minute and try again.", "rate_limit") from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Claude couldn't process the request: {exc.message}", "bad_request") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMError("Claude took too long to answer. Try again, or use a lower effort level.", "timeout") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Couldn't reach Claude. Check your internet connection.", "network") from exc
        except anthropic.APIStatusError as exc:
            kind = "overloaded" if exc.status_code == 529 else "server"
            raise LLMError(f"Claude had a temporary problem (HTTP {exc.status_code}). Try again shortly.", kind) from exc
        except TypeError as exc:  # the SDK raises this when no credentials can be resolved
            if "authentication" in str(exc):
                raise LLMError(NOT_CONFIGURED, "not_configured") from exc
            raise

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            reason = f" (safety category: {category})" if category else ""
            raise LLMError(f"Claude declined to summarize this paper{reason}.", "refusal")
        if response.stop_reason == "max_tokens":
            raise LLMError("The digest was cut off before it finished. Try again with a lower effort level.", "truncated")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if not text:
            raise LLMError("Claude returned no digest text.", "invalid_output")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("Claude's answer wasn't valid JSON.", "invalid_output") from exc

        usage = response.usage
        input_tokens = (
            (usage.input_tokens or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        return LLMResponse(
            data=data,
            model=response.model or self.model,
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens,
            request_id=getattr(response, "_request_id", None),
        )

    def check(self) -> None:
        """Verify the key and model with a free token-count request."""
        try:
            self.client.messages.count_tokens(model=self.model, messages=[{"role": "user", "content": "ping"}])
        except anthropic.AuthenticationError as exc:
            raise LLMError("Claude rejected the API key.", "auth") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"The model {self.model} isn't available to this API key.", "model") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Couldn't reach Claude. Check your internet connection.", "network") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude answered with HTTP {exc.status_code}.", "server") from exc
