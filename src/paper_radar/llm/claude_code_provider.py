"""Digests through the user's own Claude Code installation (`claude -p`), on their Claude plan.

The app runs Claude Code non-interactively as a plain summarizer: no tools, no MCP servers, no
slash commands or skills, an empty working folder, and the digest schema enforced with
--json-schema. API-key variables are removed from its environment so Claude Code uses its own
login (the subscription), never a key the app happens to know about.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from paper_radar.config import user_data_dir
from paper_radar.llm.base import LLMError, LLMRequest, LLMResponse

BINARY_ENV = "PAPER_RADAR_CLAUDE_BIN"
_FALLBACK_LOCATIONS = ("~/.local/bin/claude", "~/.claude/local/claude", "/usr/local/bin/claude", "/opt/homebrew/bin/claude")
# Variables that would make Claude Code bill an API key or think it runs inside another session.
_STRIPPED_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "PYTHONPATH", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_EXECPATH", "CLAUDE_PID", "CLAUDE_EFFORT",
)


def find_claude() -> str | None:
    if override := os.environ.get(BINARY_ENV):
        return shutil.which(override) or (override if Path(override).expanduser().exists() else None)
    if found := shutil.which("claude"):
        return found
    return next((str(p) for loc in _FALLBACK_LOCATIONS if (p := Path(loc).expanduser()).exists()), None)


def claude_version(binary: str) -> str | None:
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else None


def subprocess_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    return env


def _classify_error(message: str, status: object) -> tuple[str, str]:
    lowered = message.lower()
    if "login" in lowered or "invalid api key" in lowered or "not logged in" in lowered or status in (401, 403):
        return "auth", "Claude Code isn't logged in. Open a terminal, run `claude`, and sign in with /login."
    if "usage limit" in lowered or "limit reached" in lowered or "rate limit" in lowered or status == 429:
        return "rate_limit", f"Your Claude plan's usage limit is reached for now. {message}".strip()
    if "overloaded" in lowered or status == 529:
        return "overloaded", "Claude is overloaded right now. Try again in a few minutes."
    if "model" in lowered and ("not found" in lowered or "not available" in lowered or "invalid" in lowered):
        return "model", f"Claude Code can't use that model on your plan: {message}"
    return "server", f"Claude Code reported a problem: {message}"


class ClaudeCodeProvider:
    name = "claude_code"
    billed_per_token = False  # runs on the user's Claude plan

    def __init__(
        self,
        model: str,
        *,
        binary: str | None = None,
        timeout: float = 900.0,
        workdir: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.model = model
        self._binary = binary
        self._timeout = timeout
        self._workdir = workdir
        self._run = runner

    @property
    def binary(self) -> str:
        binary = self._binary or find_claude()
        if not binary:
            raise LLMError(
                "Claude Code isn't installed (no `claude` command found). Install it, or switch Settings → AI digests "
                "to an API key.",
                "not_configured",
            )
        return binary

    def command(self, request: LLMRequest) -> list[str]:
        return [
            self.binary,
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(request.schema),
            "--model", self.model,
            "--effort", request.effort,
            "--system-prompt", request.system,
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]

    def _workdir_path(self) -> Path:
        path = self._workdir or user_data_dir() / "claude-code-workdir"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        try:
            completed = self._run(
                self.command(request),
                input=request.prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                cwd=self._workdir_path(),
                env=subprocess_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(
                f"Claude Code didn't finish within {self._timeout / 60:.0f} minutes. Try a lower effort level.", "timeout"
            ) from exc
        except OSError as exc:
            raise LLMError(f"Couldn't start Claude Code: {exc}", "not_configured") from exc

        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            detail = (completed.stderr or completed.stdout or "no output").strip().splitlines()[-1:] or ["no output"]
            kind, message = _classify_error(detail[0], None)
            raise LLMError(message if kind != "server" else f"Claude Code didn't return a result: {detail[0]}", kind) from exc

        if data.get("is_error") or data.get("subtype") != "success":
            kind, message = _classify_error(str(data.get("result") or data.get("subtype") or "unknown error"), data.get("api_error_status"))
            raise LLMError(message, kind)

        structured = data.get("structured_output")
        if structured is None:
            try:
                structured = json.loads(data.get("result") or "")
            except json.JSONDecodeError as exc:
                raise LLMError("Claude Code's answer wasn't valid JSON.", "invalid_output") from exc

        usage = data.get("usage") or {}
        input_tokens = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        served = next(iter(data.get("modelUsage") or {}), None) or self.model
        return LLMResponse(
            data=structured,
            model=served,
            input_tokens=input_tokens or None,
            output_tokens=usage.get("output_tokens"),
            request_id=data.get("session_id"),
        )

    def check(self) -> str:
        """A tiny real request (uses a sliver of your plan) to confirm Claude Code is logged in."""
        request = LLMRequest(
            system="Answer with JSON only.",
            prompt="Reply with ok set to true.",
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
            effort="low",
            max_tokens=200,
        )
        response = self.generate_json(request)
        return response.model
