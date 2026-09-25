"""Configuration paths and conservative typed accessors."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH_ENV = "CODEX_NOTIFIER_CONFIG"
STATE_DIR_ENV = "CODEX_NOTIFIER_STATE_DIR"

DEFAULT_TEAMS_MIN_SECONDS = 300.0
DEFAULT_DISCORD_MIN_SECONDS = 300.0
DEFAULT_VOICE_MIN_SECONDS = 30.0
DEFAULT_QUIET_START = "23:00"
DEFAULT_QUIET_END = "07:00"
DEFAULT_SUMMARY_MAX_CHARS = 1800
DEFAULT_SUMMARY_TAIL_CHARS = 600
MIN_SUMMARY_MAX_CHARS = 100
MAX_SUMMARY_MAX_CHARS = 6000
MAX_DISCORD_SUMMARY_MAX_CHARS = 4096
DEFAULT_LOG_RETENTION_DAYS = 7
MAX_LOG_RETENTION_DAYS = 365


def default_config_path(*, platform_name: str | None = None,
                        environment: dict[str, str] | None = None,
                        home: Path | None = None) -> Path:
    platform = os.name if platform_name is None else platform_name
    env = os.environ if environment is None else environment
    user_home = Path.home() if home is None else home
    if platform == "nt":
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / "CodexNotifier" / "config.json"
        return user_home / "AppData" / "Local" / "CodexNotifier" / "config.json"
    xdg = env.get("XDG_CONFIG_HOME", "").strip()
    return (Path(xdg) if xdg else user_home / ".config") / "codex-notifier" / "config.json"


def config_path() -> Path:
    configured = os.environ.get(CONFIG_PATH_ENV, "").strip()
    return Path(configured).expanduser() if configured else default_config_path()


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    content = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(content, dict):
        raise TypeError(f"{path} debe contener un objeto JSON")
    return content


def config_section(config: dict[str, Any], name: str) -> dict[str, Any]:
    section = config.get(name, {})
    return section if isinstance(section, dict) else {}


def config_bool(config: dict[str, Any], section_name: str, key: str, default: bool) -> bool:
    value = config_section(config, section_name).get(key)
    return value if isinstance(value, bool) else default


def config_seconds(config: dict[str, Any], section_name: str, key: str, default: float) -> float:
    value = config_section(config, section_name).get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    return default


def config_int(config: dict[str, Any], section_name: str, key: str, default: int,
               *, minimum: int = 0, maximum: int | None = None) -> int:
    value = config_section(config, section_name).get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        value = default
    result = max(minimum, int(value))
    return min(result, maximum) if maximum is not None else result


def config_text(config: dict[str, Any], section_name: str, key: str, default: str = "") -> str:
    value = config_section(config, section_name).get(key)
    return value.strip() if isinstance(value, str) else default


def default_state_dir(*, platform_name: str | None = None,
                      environment: dict[str, str] | None = None,
                      home: Path | None = None) -> Path:
    platform = os.name if platform_name is None else platform_name
    env = os.environ if environment is None else environment
    user_home = Path.home() if home is None else home
    if platform == "nt":
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / "CodexNotifier"
        return user_home / "AppData" / "Local" / "CodexNotifier"
    xdg = env.get("XDG_STATE_HOME", "").strip()
    return (Path(xdg) if xdg else user_home / ".local" / "state") / "codex-notifier"


def state_dir() -> Path:
    configured = os.environ.get(STATE_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else default_state_dir()


def log_dir() -> Path:
    return state_dir() / "logs"
