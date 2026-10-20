import json
import subprocess

import pytest

from paper_radar.llm.base import LLMError, LLMRequest
from paper_radar.llm.claude_code_provider import ClaudeCodeProvider, subprocess_env

REQUEST = LLMRequest(system="rules", prompt="the paper", schema={"type": "object"}, effort="medium")


def result(**overrides):
    data = {
        "type": "result", "subtype": "success", "is_error": False, "api_error_status": None,
        "result": '{"a": 1}', "structured_output": {"a": 1}, "session_id": "s-1",
        "usage": {"input_tokens": 5, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 0, "output_tokens": 400},
        "modelUsage": {"claude-opus-5-5": {}}, "total_cost_usd": 0.05,
    }
    data.update(overrides)
    return json.dumps(data)


class Runner:
    def __init__(self, stdout="", stderr="", raises=None):
        self.stdout, self.stderr, self.raises, self.calls = stdout, stderr, raises, []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(command, 0, self.stdout, self.stderr)


def provider(runner, tmp_path):
    return ClaudeCodeProvider("claude-opus-5-5", binary="/usr/bin/claude", runner=runner, workdir=tmp_path)


def test_runs_claude_as_a_tool_free_summarizer_on_the_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("CLAUDECODE", "1")
    runner = Runner(result())
    response = provider(runner, tmp_path).generate_json(REQUEST)
    command, kwargs = runner.calls[0]
    assert command[:2] == ["/usr/bin/claude", "-p"]
    for flag in ("--output-format", "--json-schema", "--model", "--effort", "--system-prompt", "--tools"):
        assert flag in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--model") + 1] == "claude-opus-5-5"
    assert json.loads(command[command.index("--json-schema") + 1]) == {"type": "object"}
    assert "--bare" not in command  # --bare would ignore the subscription login
    assert {"--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"} <= set(command)
    assert kwargs["input"] == "the paper" and kwargs["cwd"] == tmp_path
    assert "ANTHROPIC_API_KEY" not in kwargs["env"] and "CLAUDECODE" not in kwargs["env"]
    assert response.data == {"a": 1} and response.model == "claude-opus-5-5"
    assert (response.input_tokens, response.output_tokens) == (1005, 400)
    assert ClaudeCodeProvider.billed_per_token is False


def test_falls_back_to_parsing_the_result_text(tmp_path) -> None:
    response = provider(Runner(result(structured_output=None, result='{"b": 2}')), tmp_path).generate_json(REQUEST)
    assert response.data == {"b": 2}


@pytest.mark.parametrize(
    ("stdout", "kind"),
    [
        (result(is_error=True, subtype="error", result="Invalid API key · Please run /login"), "auth"),
        (result(is_error=True, subtype="error", result="Claude AI usage limit reached|1760000000"), "rate_limit"),
        (result(is_error=True, subtype="error_during_execution", result="Something else"), "server"),
        ("not json at all", "server"),
    ],
)
def test_errors_are_explained(tmp_path, stdout, kind) -> None:
    with pytest.raises(LLMError) as error:
        provider(Runner(stdout), tmp_path).generate_json(REQUEST)
    assert error.value.kind == kind


def test_timeout_and_missing_binary(tmp_path, monkeypatch) -> None:
    runner = Runner(raises=subprocess.TimeoutExpired("claude", 900))
    with pytest.raises(LLMError, match="didn't finish"):
        provider(runner, tmp_path).generate_json(REQUEST)
    with pytest.raises(LLMError) as error:
        ClaudeCodeProvider("claude-opus-5-5", runner=Runner()).generate_json(REQUEST)  # conftest hides `claude`
    assert error.value.kind == "not_configured"


def test_subprocess_env_strips_keys_and_session_markers(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "x")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "y")
    monkeypatch.setenv("HOME_STAYS", "z")
    env = subprocess_env()
    assert "ANTHROPIC_AUTH_TOKEN" not in env and "CLAUDE_CODE_SESSION_ID" not in env and env["HOME_STAYS"] == "z"
