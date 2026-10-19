"""Where Paper Radar keeps things, and process-level settings.

However the app is launched (installed command, desktop launcher or from a checkout), it uses:

- the user data directory for the library: ~/.local/share/paper-radar on Linux;
- the user config directory for secrets and overrides: ~/.config/paper-radar, holding an
  optional .env (for example ANTHROPIC_API_KEY) and optional copies of taxonomy.yaml,
  venues.yaml or profile.yaml that take precedence over the packaged defaults;
- packaged defaults in paper_radar/defaults.

PAPER_RADAR_DATA_DIR, PAPER_RADAR_CONFIG_DIR and PAPER_RADAR_DB_PATH override the locations.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "paper-radar"
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULTS_DIR = PACKAGE_DIR / "defaults"
ASSETS_DIR = PACKAGE_DIR / "assets"
# The Streamlit script lives in its own folder: Streamlit puts that folder first on sys.path,
# so it must not contain modules that could shadow other packages.
APP_DIR = PACKAGE_DIR / "streamlit_app"
APP_SCRIPT = APP_DIR / "app.py"


def _home_dir(env_var: str, xdg_var: str, xdg_default: str, windows_var: str) -> Path:
    if override := os.environ.get(env_var):
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get(windows_var, Path.home())) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get(xdg_var) or Path.home() / xdg_default) / APP_NAME


def user_config_dir() -> Path:
    return _home_dir("PAPER_RADAR_CONFIG_DIR", "XDG_CONFIG_HOME", ".config", "APPDATA")


def user_data_dir() -> Path:
    return _home_dir("PAPER_RADAR_DATA_DIR", "XDG_DATA_HOME", ".local/share", "LOCALAPPDATA")


def env_file() -> Path:
    return user_config_dir() / ".env"


def config_file(name: str) -> Path:
    """A user override in the config directory if present, otherwise the packaged default."""
    override = user_config_dir() / name
    return override if override.is_file() else DEFAULTS_DIR / name


@dataclass(frozen=True)
class AppConfig:
    db_path: Path
    taxonomy_path: Path
    venues_path: Path
    profile_path: Path
    contact_email: str | None
    http_timeout: float

    @classmethod
    def from_env(cls, *, load_env_file: bool = True) -> AppConfig:
        if load_env_file and env_file().is_file():
            load_dotenv(env_file(), override=False)  # real environment variables win
        db_path = Path(os.environ.get("PAPER_RADAR_DB_PATH") or user_data_dir() / "paper_radar.db").expanduser()
        return cls(
            db_path=db_path,
            taxonomy_path=config_file("taxonomy.yaml"),
            venues_path=config_file("venues.yaml"),
            profile_path=config_file("profile.yaml"),
            contact_email=os.environ.get("PAPER_RADAR_CONTACT_EMAIL") or None,
            http_timeout=float(os.environ.get("PAPER_RADAR_HTTP_TIMEOUT", "30")),
        )
