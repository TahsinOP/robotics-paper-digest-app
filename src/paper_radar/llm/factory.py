"""Pick the digest engine from preferences and report whether it is ready to use."""

from __future__ import annotations

from dataclasses import dataclass

from paper_radar.llm import credentials
from paper_radar.llm.anthropic_provider import AnthropicProvider
from paper_radar.llm.base import LLMProvider
from paper_radar.llm.claude_code_provider import ClaudeCodeProvider, find_claude

ENGINES = {
    "claude_code": "Claude Code (your Claude plan)",
    "api": "Anthropic API key (pay per use)",
}


@dataclass(frozen=True)
class EngineStatus:
    ready: bool
    description: str
    billed_per_token: bool


def make_provider(engine: str, model: str) -> LLMProvider:
    return AnthropicProvider(model) if engine == "api" else ClaudeCodeProvider(model)


def engine_status(engine: str) -> EngineStatus:
    if engine == "api":
        status = credentials.status()
        return EngineStatus(status.configured, status.description, billed_per_token=True)
    binary = find_claude()
    if binary:
        return EngineStatus(True, f"Claude Code found at {binary}; digests use your Claude plan", billed_per_token=False)
    return EngineStatus(False, "Claude Code isn't installed or isn't on the PATH", billed_per_token=False)
