"""Compatibility entry point for Codex Notifier.

The implementation lives in :mod:`codex_notifier`; these aliases keep the
historic script, helper names, and integration points working.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_notifier import cli as _cli
from codex_notifier import config as _config
from codex_notifier import payloads as _payloads
from codex_notifier import state as _state
from codex_notifier import trace_logging as _trace
from codex_notifier.channels import voice as _voice
from codex_notifier.channels.webhook import send_webhook_notification as _transport

# These imports remain module attributes for compatibility with callers that
# patched notifier.subprocess/shutil/tempfile in the original monolith.
import base64  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import subprocess  # noqa: F401
import tempfile  # noqa: F401

STATE_DIR_ENV = _config.STATE_DIR_ENV
CONFIG_PATH_ENV = _config.CONFIG_PATH_ENV
DEFAULT_TEAMS_MIN_SECONDS = _config.DEFAULT_TEAMS_MIN_SECONDS
DEFAULT_DISCORD_MIN_SECONDS = _config.DEFAULT_DISCORD_MIN_SECONDS
DEFAULT_VOICE_MIN_SECONDS = _config.DEFAULT_VOICE_MIN_SECONDS
DEFAULT_QUIET_START = _config.DEFAULT_QUIET_START
DEFAULT_QUIET_END = _config.DEFAULT_QUIET_END
STALE_MARKER_SECONDS = _state.STALE_MARKER_SECONDS
MAX_TITLE_CHARS = _payloads.MAX_TITLE_CHARS
DEFAULT_SUMMARY_MAX_CHARS = _config.DEFAULT_SUMMARY_MAX_CHARS
DEFAULT_SUMMARY_TAIL_CHARS = _config.DEFAULT_SUMMARY_TAIL_CHARS
MIN_SUMMARY_MAX_CHARS = _config.MIN_SUMMARY_MAX_CHARS
MAX_SUMMARY_MAX_CHARS = _config.MAX_SUMMARY_MAX_CHARS
MAX_DISCORD_SUMMARY_MAX_CHARS = _config.MAX_DISCORD_SUMMARY_MAX_CHARS
DEFAULT_LOG_RETENTION_DAYS = _config.DEFAULT_LOG_RETENTION_DAYS
MAX_LOG_RETENTION_DAYS = _config.MAX_LOG_RETENTION_DAYS
MAX_LOG_ERROR_CHARS = _trace.MAX_LOG_ERROR_CHARS
SPEECH_TIMEOUT_SECONDS = _voice.SPEECH_TIMEOUT_SECONDS


def _text(value: Any) -> str:
    return _state.text(value)


def _truncate(value: str, limit: int) -> str:
    return _payloads.truncate(value, limit)


def _summary_excerpt(value: str, limit: int, tail_chars: int) -> str:
    return _payloads.summary_excerpt(value, limit, tail_chars)


def default_config_path(**kwargs: Any) -> Path:
    return _config.default_config_path(**kwargs)


def config_path() -> Path:
    return _config.config_path()


def load_config() -> dict[str, Any]:
    return _config.load_config()


def _config_section(config: dict[str, Any], name: str) -> dict[str, Any]:
    return _config.config_section(config, name)


def _config_bool(config: dict[str, Any], section_name: str, key: str, default: bool) -> bool:
    return _config.config_bool(config, section_name, key, default)


def _config_seconds(config: dict[str, Any], section_name: str, key: str, default: float) -> float:
    return _config.config_seconds(config, section_name, key, default)


def _config_int(config: dict[str, Any], section_name: str, key: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    return _config.config_int(config, section_name, key, default, minimum=minimum, maximum=maximum)


def _config_text(config: dict[str, Any], section_name: str, key: str, default: str = "") -> str:
    return _config.config_text(config, section_name, key, default)


def default_state_dir(**kwargs: Any) -> Path:
    return _config.default_state_dir(**kwargs)


def state_dir() -> Path:
    return _config.state_dir()


def log_dir() -> Path:
    return _config.log_dir()


def _safe_error(exc: Exception, *secrets: str) -> str:
    return _trace.safe_error(exc, *secrets)


def write_trace_log(config: dict[str, Any], **kwargs: Any) -> None:
    return _trace.write_trace_log(config, **kwargs)


def _marker_path(turn_id: str) -> Path:
    return _state.marker_path(turn_id)


def record_start(payload: dict[str, Any], *, now: float | None = None) -> bool:
    return _state.record_start(payload, now=now)


def consume_start(notification: dict[str, Any], *, now: float | None = None) -> tuple[float | None, dict[str, Any]]:
    return _state.consume_start(notification, now=now)


def is_quiet_time(moment: datetime, config: dict[str, Any] | None = None) -> bool:
    return _cli.is_quiet_time(moment, config)


def notification_channels(duration_seconds: float | None, moment: datetime, config: dict[str, Any] | None = None) -> tuple[bool, bool, bool]:
    return _cli.notification_channels(duration_seconds, moment, config)


def _duration_text(duration_seconds: float | None) -> str:
    return _payloads.duration_text(duration_seconds)


def _spoken_duration(duration_seconds: float, language: str) -> str:
    return _cli.spoken_duration(duration_seconds, language)


def detect_language(value: str, configured: str = "auto") -> str:
    return _voice.detect_language(value, configured)


def _chat_title(notification: dict[str, Any]) -> str:
    return _payloads.chat_title(notification)


def build_payload(notification: dict[str, Any], duration_seconds: float | None, marker: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    return _payloads.build_payload(notification, duration_seconds, marker, config)


def build_discord_payload(notification: dict[str, Any], duration_seconds: float | None, marker: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    return _payloads.build_discord_payload(notification, duration_seconds, marker, config)


def _send_webhook_notification(service: str, webhook_url: str, payload: dict[str, Any]) -> None:
    _transport(service, webhook_url, payload)


def send_teams_notification(webhook_url: str, payload: dict[str, Any]) -> None:
    _send_webhook_notification("Teams", webhook_url, payload)


def send_discord_notification(webhook_url: str, payload: dict[str, Any]) -> None:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(webhook_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["wait"] = "true"
    confirmed_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    _send_webhook_notification("Discord", confirmed_url, payload)


def _voice_message(notification: dict[str, Any], duration_seconds: float | None, marker: dict[str, Any], language: str) -> str:
    return _cli.voice_message(notification, duration_seconds, marker, language)


def _configured_volume(voice_config: dict[str, Any]) -> float | None:
    return _voice.configured_volume(voice_config)


def speak_windows(message: str, *, language: str = "es", preferred_voice: str = "", volume: float | None = None) -> None:
    return _voice.speak_sapi_windows(message, language=language, preferred_voice=preferred_voice, volume=volume)


def _configured_voice(voice_config: dict[str, Any], language: str) -> str:
    return _voice.configured_voice(voice_config, language)


def _resolve_executable(configured: str, default: str) -> str | None:
    return _voice.resolve_executable(configured, default)


def generate_piper_wav(message: str, *, language: str = "es", voice_config: dict[str, Any], temporary_dir: str | Path) -> Path:
    return _voice.generate_piper_wav(message, language=language, voice_config=voice_config, temporary_dir=temporary_dir)


def play_wav_windows(wav_path: Path, *, powershell: str | None = None) -> None:
    return _voice.play_wav_windows(wav_path, powershell=powershell)


def play_wav_linux(wav_path: Path) -> None:
    return _voice.play_wav_linux(wav_path)


def speak_piper(message: str, *, language: str = "es", voice_config: dict[str, Any]) -> None:
    return _voice.speak_piper(message, language=language, voice_config=voice_config)


def speak_linux(message: str, *, language: str = "es", voice_config: dict[str, Any] | None = None) -> None:
    return _voice.speak_linux(message, language=language, voice_config=voice_config)


def speak(message: str, *, language: str = "es", preferred_voice: str = "", voice_config: dict[str, Any] | None = None) -> None:
    return _voice.speak(message, language=language, preferred_voice=preferred_voice, voice_config=voice_config)


def handle_completion(notification: dict[str, Any], *, now: float | None = None) -> None:
    _cli.handle_completion(notification, now=now, load=load_config, consume=consume_start,
                           speak_fn=speak, send_teams_fn=send_teams_notification,
                           send_discord_fn=send_discord_notification, log_fn=write_trace_log)


def main() -> int:
    return _cli.main(speak_fn=speak)


if __name__ == "__main__":
    raise SystemExit(main())
