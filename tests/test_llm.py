import os
import stat
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from paper_radar.config import env_file
from paper_radar.llm import credentials
from paper_radar.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from paper_radar.llm.base import LLMError, LLMRequest, estimate_cost

REQUEST = LLMRequest(system="sys", prompt="prompt", schema={"type": "object"}, effort="medium", max_tokens=1000)
HTTP_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def response(stop_reason="end_turn", text='{"ok": true}', category=None):
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
        content=content,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300, cache_creation_input_tokens=0, cache_read_input_tokens=100),
        model="claude-opus-5-5",
    )


class FakeClient:
    def __init__(self, result):
        self.calls = []

        def create(**kwargs):
            self.calls.append(kwargs)
            if isinstance(result, Exception):
                raise result
            return result

        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def test_request_uses_structured_output_effort_and_refusal_fallback() -> None:
    client = FakeClient(response())
    result = AnthropicProvider("claude-opus-5-5", client=client).generate_json(REQUEST)
    sent = client.calls[0]
    assert sent["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert sent["fallbacks"] == "default" and sent["betas"] == [FALLBACK_BETA]
    assert "thinking" not in sent  # adaptive thinking is the default on current models
    assert result.data == {"ok": True}
    assert (result.input_tokens, result.output_tokens) == (1300, 300)


def test_haiku_has_no_server_side_fallback() -> None:
    client = FakeClient(response())
    AnthropicProvider("claude-haiku-5-5", client=client).generate_json(REQUEST)
    assert "fallbacks" not in client.calls[0] and "betas" not in client.calls[0]


@pytest.mark.parametrize(
    ("result", "kind", "fragment"),
    [
        (response("refusal", category="bio"), "refusal", "safety category: bio"),
        (response("max_tokens"), "truncated", "cut off"),
        (response(text="not json"), "invalid_output", "valid JSON"),
        (anthropic.AuthenticationError("bad key", response=httpx2.Response(401, request=HTTP_REQUEST), body=None), "auth", "API key"),
        (anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=HTTP_REQUEST), body=None), "rate_limit", "rate limit"),
        (anthropic.NotFoundError("no model", response=httpx2.Response(404, request=HTTP_REQUEST), body=None), "model", "isn't available"),
        (anthropic.APIStatusError("overloaded", response=httpx2.Response(529, request=HTTP_REQUEST), body=None), "overloaded", "529"),
        (anthropic.APITimeoutError(HTTP_REQUEST), "timeout", "too long"),
        (anthropic.APIConnectionError(request=HTTP_REQUEST), "network", "internet"),
    ],
)
def test_failures_become_readable_errors(result, kind, fragment) -> None:
    with pytest.raises(LLMError) as error:
        AnthropicProvider("claude-opus-5-5", client=FakeClient(result)).generate_json(REQUEST)
    assert error.value.kind == kind
    assert fragment in str(error.value)


def test_missing_credentials_are_reported_before_any_call() -> None:
    with pytest.raises(LLMError) as error:
        AnthropicProvider("claude-opus-5-5").generate_json(REQUEST)
    assert error.value.kind == "not_configured" and "Settings" in str(error.value)


def test_cost_estimate_uses_model_prices() -> None:
    assert estimate_cost("claude-opus-5-5", 1_000_000, 100_000) == pytest.approx(4.0 + 2.0)
    assert estimate_cost("unknown-model", 10, 10) is None


def test_saving_and_removing_the_key(monkeypatch) -> None:
    assert not credentials.status().configured
    path = credentials.save_key("  sk-ant-test-123  ")
    assert path == env_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-123"
    status = credentials.status()
    assert status.configured and "saved in" in status.description
    assert "sk-ant" not in status.description  # the key itself is never shown

    path.write_text(path.read_text() + "OTHER=1\n")
    credentials.remove_key()
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert path.read_text() == "OTHER=1\n"
    with pytest.raises(ValueError):
        credentials.save_key("has spaces in it")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert "environment variable" in credentials.status().description


def test_check_reports_auth_errors() -> None:
    def count_tokens(**kwargs):
        raise anthropic.AuthenticationError("bad", response=httpx2.Response(401, request=HTTP_REQUEST), body=None)

    client = SimpleNamespace(messages=SimpleNamespace(count_tokens=count_tokens))
    with pytest.raises(LLMError, match="rejected"):
        AnthropicProvider("claude-opus-5-5", client=client).check()
