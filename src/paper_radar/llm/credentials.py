"""Find and store the Claude API key without ever echoing it.

The key lives in an environment variable or in the user config directory's .env file
(permissions 600). It is never written to the database, logs or the UI.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from paper_radar.config import env_file

KEY_VAR = "ANTHROPIC_API_KEY"


def _line(var: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:export\s+)?{re.escape(var)}\s*=")


@dataclass(frozen=True)
class CredentialStatus:
    configured: bool
    description: str  # e.g. "saved in ~/.config/paper-radar/.env"


def _file_key(path: Path, var: str = KEY_VAR) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        if _line(var).match(line):
            return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


def _display(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def status() -> CredentialStatus:
    key = os.environ.get(KEY_VAR)
    path = env_file()
    if key and _file_key(path) == key:
        return CredentialStatus(True, f"Claude API key saved in {_display(path)}")
    if key:
        return CredentialStatus(True, f"Claude API key from the {KEY_VAR} environment variable")
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return CredentialStatus(True, "Claude access token from the ANTHROPIC_AUTH_TOKEN environment variable")
    if (Path.home() / ".config" / "anthropic").is_dir():
        return CredentialStatus(True, "Claude login from the ant command-line tool")
    return CredentialStatus(False, "No Claude API key yet")


def save_key(key: str, var: str = KEY_VAR) -> Path:
    """Store a key in the user .env (mode 600) and make it live for this process."""
    key = key.strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("That doesn't look like an API key.")
    path = env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [ln for ln in path.read_text().splitlines() if not _line(var).match(ln)] if path.is_file() else []
    lines.append(f"{var}={key}")
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text("\n".join(lines) + "\n")
    os.environ[var] = key
    return path


def remove_key(var: str = KEY_VAR) -> None:
    path = env_file()
    if path.is_file():
        lines = [ln for ln in path.read_text().splitlines() if not _line(var).match(ln)]
        path.write_text("\n".join(lines) + ("\n" if lines else ""))
    os.environ.pop(var, None)


def is_saved(var: str) -> bool:
    return bool(os.environ.get(var)) and _file_key(env_file(), var) == os.environ.get(var)
