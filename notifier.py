"""Filtered Codex completion notifications for webhooks and local speech.

The script has two entry points:

* ``record-start`` reads a Codex UserPromptSubmit hook payload from stdin.
* ``notify`` reads the agent-turn-complete payload appended by Codex to the
  configured ``notify`` command.

Only Python's standard library is required. Windows speech uses the built-in
SAPI voice through Windows PowerShell.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


STATE_DIR_ENV = "CODEX_NOTIFIER_STATE_DIR"
CONFIG_PATH_ENV = "CODEX_NOTIFIER_CONFIG"

DEFAULT_TEAMS_MIN_SECONDS = 300.0
DEFAULT_DISCORD_MIN_SECONDS = 300.0
DEFAULT_VOICE_MIN_SECONDS = 30.0
DEFAULT_QUIET_START = "23:00"
DEFAULT_QUIET_END = "07:00"
STALE_MARKER_SECONDS = 48 * 60 * 60
MAX_TITLE_CHARS = 120
DEFAULT_SUMMARY_MAX_CHARS = 1800
DEFAULT_SUMMARY_TAIL_CHARS = 600
MIN_SUMMARY_MAX_CHARS = 100
MAX_SUMMARY_MAX_CHARS = 6000
MAX_DISCORD_SUMMARY_MAX_CHARS = 4096
DEFAULT_LOG_RETENTION_DAYS = 7
MAX_LOG_RETENTION_DAYS = 365
MAX_LOG_ERROR_CHARS = 300
SPEECH_TIMEOUT_SECONDS = 30

SPANISH_WORDS = {
    "al",
    "cambios",
    "completado",
    "con",
    "corregido",
    "de",
    "el",
    "en",
    "esta",
    "este",
    "fue",
    "la",
    "las",
    "listo",
    "los",
    "para",
    "por",
    "pruebas",
    "que",
    "se",
    "sin",
    "una",
    "y",
    "ya",
}
ENGLISH_WORDS = {
    "a",
    "and",
    "changes",
    "completed",
    "done",
    "for",
    "from",
    "has",
    "fixed",
    "in",
    "is",
    "of",
    "on",
    "that",
    "the",
    "tests",
    "this",
    "to",
    "was",
    "ready",
    "with",
    "without",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _summary_excerpt(value: str, limit: int, tail_chars: int) -> str:
    """Keep the beginning and end of a summary within a character limit."""
    if len(value) <= limit:
        return value

    omitted = len(value)
    for _ in range(10):
        omitted_text = f"{omitted:,}".replace(",", ".")
        separator = f"\n\n[… {omitted_text} caracteres omitidos …]\n\n"
        available = limit - len(separator)
        if available < 2:
            return _truncate(value, limit)
        tail_length = min(max(0, tail_chars), available - 1)
        head_length = available - tail_length
        new_omitted = len(value) - head_length - tail_length
        if new_omitted == omitted:
            tail = value[-tail_length:] if tail_length else ""
            return value[:head_length] + separator + tail
        omitted = new_omitted

    return _truncate(value, limit)


def default_config_path(
    *,
    platform_name: str | None = None,
    environment: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    platform = os.name if platform_name is None else platform_name
    env = os.environ if environment is None else environment
    user_home = Path.home() if home is None else home
    if platform == "nt":
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / "CodexNotifier" / "config.json"
        return user_home / "AppData" / "Local" / "CodexNotifier" / "config.json"
    xdg_config_home = env.get("XDG_CONFIG_HOME", "").strip()
    config_home = Path(xdg_config_home) if xdg_config_home else user_home / ".config"
    return config_home / "codex-notifier" / "config.json"


def config_path() -> Path:
    configured = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return default_config_path()


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    content = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(content, dict):
        raise TypeError(f"{path} debe contener un objeto JSON")
    return content


def _config_section(config: dict[str, Any], name: str) -> dict[str, Any]:
    section = config.get(name, {})
    return section if isinstance(section, dict) else {}


def _config_bool(
    config: dict[str, Any],
    section_name: str,
    key: str,
    default: bool,
) -> bool:
    value = _config_section(config, section_name).get(key)
    return value if isinstance(value, bool) else default


def _config_seconds(
    config: dict[str, Any], section_name: str, key: str, default: float
) -> float:
    value = _config_section(config, section_name).get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    return default


def _config_int(
    config: dict[str, Any],
    section_name: str,
    key: str,
    default: int,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    value = _config_section(config, section_name).get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        value = default
    result = max(minimum, int(value))
    return min(result, maximum) if maximum is not None else result


def _config_text(
    config: dict[str, Any],
    section_name: str,
    key: str,
    default: str = "",
) -> str:
    value = _config_section(config, section_name).get(key)
    return value.strip() if isinstance(value, str) else default


def default_state_dir(
    *,
    platform_name: str | None = None,
    environment: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    platform = os.name if platform_name is None else platform_name
    env = os.environ if environment is None else environment
    user_home = Path.home() if home is None else home
    if platform == "nt":
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / "CodexNotifier"
        return user_home / "AppData" / "Local" / "CodexNotifier"
    xdg_state_home = env.get("XDG_STATE_HOME", "").strip()
    state_home = Path(xdg_state_home) if xdg_state_home else user_home / ".local" / "state"
    return state_home / "codex-notifier"


def state_dir() -> Path:
    configured = os.environ.get(STATE_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return default_state_dir()


def log_dir() -> Path:
    return state_dir() / "logs"


def _safe_error(exc: Exception, *secrets: str) -> str:
    message = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return _truncate(message, MAX_LOG_ERROR_CHARS)


def _cleanup_old_logs(directory: Path, moment: datetime, retention_days: int) -> None:
    oldest = moment.date() - timedelta(days=retention_days - 1)
    for path in directory.glob("notifier-????-??-??.jsonl"):
        try:
            file_date = datetime.strptime(path.stem[9:], "%Y-%m-%d").date()
            if file_date < oldest:
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue


def write_trace_log(
    config: dict[str, Any],
    *,
    moment: datetime,
    chat_id: str,
    duration_seconds: float | None,
    teams_status: str,
    discord_status: str,
    voice_status: str,
    teams_error: str = "",
    discord_error: str = "",
    voice_error: str = "",
) -> None:
    if not _config_bool(config, "logging", "enabled", True):
        return
    retention_days = _config_int(
        config,
        "logging",
        "retention_days",
        DEFAULT_LOG_RETENTION_DAYS,
        minimum=1,
        maximum=MAX_LOG_RETENTION_DAYS,
    )
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs(directory, moment, retention_days)
    entry: dict[str, Any] = {
        "timestamp": moment.isoformat(timespec="seconds"),
        "chat_id": chat_id,
        "duration_seconds": (
            round(duration_seconds, 3) if duration_seconds is not None else None
        ),
        "teams_status": teams_status,
        "discord_status": discord_status,
        "voice_status": voice_status,
    }
    if teams_error:
        entry["teams_error"] = teams_error
    if discord_error:
        entry["discord_error"] = discord_error
    if voice_error:
        entry["voice_error"] = voice_error
    path = directory / f"notifier-{moment.date().isoformat()}.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if os.name != "nt":
        path.chmod(0o600)


def _marker_path(turn_id: str) -> Path:
    digest = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
    return state_dir() / "turns" / f"{digest}.json"


def _cleanup_stale_markers(now: float) -> None:
    marker_dir = state_dir() / "turns"
    if not marker_dir.exists():
        return
    for marker in marker_dir.glob("*.json"):
        try:
            if now - marker.stat().st_mtime > STALE_MARKER_SECONDS:
                marker.unlink(missing_ok=True)
        except OSError:
            continue


def record_start(payload: dict[str, Any], *, now: float | None = None) -> bool:
    """Persist the start of a Codex turn without storing the user prompt."""
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return False
    turn_id = _text(payload.get("turn_id"))
    if not turn_id:
        return False

    timestamp = time.time() if now is None else now
    marker = _marker_path(turn_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "started_at": timestamp,
        "session_id": _text(payload.get("session_id")),
        "turn_id": turn_id,
        "cwd": _text(payload.get("cwd")),
    }
    temporary = marker.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, marker)
    _cleanup_stale_markers(timestamp)
    return True


def consume_start(
    notification: dict[str, Any], *, now: float | None = None
) -> tuple[float | None, dict[str, Any]]:
    """Read and remove the matching marker, returning elapsed seconds."""
    turn_id = _text(notification.get("turn-id") or notification.get("turn_id"))
    if not turn_id:
        return None, {}
    marker = _marker_path(turn_id)
    try:
        content = json.loads(marker.read_text(encoding="utf-8"))
        started_at = float(content["started_at"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None, {}
    finally:
        marker.unlink(missing_ok=True)

    finished_at = time.time() if now is None else now
    return max(0.0, finished_at - started_at), content


def _parse_clock(value: str, default: str) -> int:
    try:
        hours, minutes = value.strip().split(":", 1)
        total = int(hours) * 60 + int(minutes)
        if not 0 <= total < 24 * 60:
            raise ValueError
        return total
    except (AttributeError, TypeError, ValueError):
        fallback_hours, fallback_minutes = default.split(":", 1)
        return int(fallback_hours) * 60 + int(fallback_minutes)


def is_quiet_time(moment: datetime, config: dict[str, Any] | None = None) -> bool:
    settings = {} if config is None else config
    start = _parse_clock(
        _config_text(
            settings,
            "voice",
            "quiet_start",
            DEFAULT_QUIET_START,
        ),
        DEFAULT_QUIET_START,
    )
    end = _parse_clock(
        _config_text(
            settings,
            "voice",
            "quiet_end",
            DEFAULT_QUIET_END,
        ),
        DEFAULT_QUIET_END,
    )
    if start == end:
        return False
    current = moment.hour * 60 + moment.minute
    if start < end:
        return start <= current < end
    return current >= start or current < end


def notification_channels(
    duration_seconds: float | None,
    moment: datetime,
    config: dict[str, Any] | None = None,
) -> tuple[bool, bool, bool]:
    settings = load_config() if config is None else config
    teams_webhook_configured = bool(
        _config_text(settings, "teams", "webhook_url")
    )
    discord_webhook_configured = bool(
        _config_text(settings, "discord", "webhook_url")
    )
    teams_duration_due = (
        _config_bool(
            settings,
            "teams",
            "notify_when_duration_unknown",
            False,
        )
        if duration_seconds is None
        else duration_seconds
        >= _config_seconds(
            settings,
            "teams",
            "minimum_seconds",
            DEFAULT_TEAMS_MIN_SECONDS,
        )
    )
    discord_duration_due = (
        _config_bool(
            settings,
            "discord",
            "notify_when_duration_unknown",
            False,
        )
        if duration_seconds is None
        else duration_seconds
        >= _config_seconds(
            settings,
            "discord",
            "minimum_seconds",
            DEFAULT_DISCORD_MIN_SECONDS,
        )
    )
    voice_duration_due = (
        _config_bool(
            settings,
            "voice",
            "notify_when_duration_unknown",
            True,
        )
        if duration_seconds is None
        else duration_seconds
        >= _config_seconds(
            settings,
            "voice",
            "minimum_seconds",
            DEFAULT_VOICE_MIN_SECONDS,
        )
    )
    teams = _config_bool(
        settings, "teams", "enabled", teams_webhook_configured
    ) and teams_duration_due
    discord = _config_bool(
        settings,
        "discord",
        "enabled",
        discord_webhook_configured,
    ) and discord_duration_due
    voice = (
        _config_bool(settings, "voice", "enabled", True)
        and voice_duration_due
        and not is_quiet_time(moment, settings)
    )
    return teams, discord, voice


def _duration_text(duration_seconds: float | None) -> str:
    if duration_seconds is None:
        return "No disponible"
    total_seconds = max(0, round(duration_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} h")
    if minutes:
        parts.append(f"{minutes} min")
    if seconds or not parts:
        parts.append(f"{seconds} s")
    return " ".join(parts)


def _spoken_duration(duration_seconds: float, language: str) -> str:
    total_seconds = max(0, round(duration_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if language == "en":
        if hours:
            parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
        if minutes:
            parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
        if seconds or not parts:
            parts.append(f"{seconds} second" + ("s" if seconds != 1 else ""))
    else:
        if hours:
            parts.append(f"{hours} hora" + ("s" if hours != 1 else ""))
        if minutes:
            parts.append(f"{minutes} minuto" + ("s" if minutes != 1 else ""))
        if seconds or not parts:
            parts.append(f"{seconds} segundo" + ("s" if seconds != 1 else ""))
    return " ".join(parts)


def detect_language(text: str, configured: str = "auto") -> str:
    requested = configured.strip().lower()
    if requested in {"es", "spanish", "español"}:
        return "es"
    if requested in {"en", "english", "inglés", "ingles"}:
        return "en"

    lowered = text.lower()
    words = re.findall(r"[a-záéíóúüñ]+", lowered)
    spanish_score = sum(word in SPANISH_WORDS for word in words)
    english_score = sum(word in ENGLISH_WORDS for word in words)
    if re.search(r"[áéíóúüñ¿¡]", lowered):
        spanish_score += 2
    return "en" if english_score > spanish_score else "es"


def _chat_title(notification: dict[str, Any]) -> str:
    for key in ("thread-title", "thread_title", "title"):
        title = _text(notification.get(key))
        if title:
            return _truncate(title, MAX_TITLE_CHARS)
    return ""


def build_payload(
    notification: dict[str, Any],
    duration_seconds: float | None,
    marker: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = {} if config is None else config
    completed_at = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M %Z")
    chat_id = _text(notification.get("thread-id")) or _text(marker.get("session_id"))
    chat_title = _chat_title(notification)
    cwd = _text(notification.get("cwd")) or _text(marker.get("cwd"))
    summary_max_chars = _config_int(
        settings,
        "teams",
        "summary_max_chars",
        DEFAULT_SUMMARY_MAX_CHARS,
        minimum=MIN_SUMMARY_MAX_CHARS,
        maximum=MAX_SUMMARY_MAX_CHARS,
    )
    summary_tail_chars = _config_int(
        settings,
        "teams",
        "summary_tail_chars",
        DEFAULT_SUMMARY_TAIL_CHARS,
    )
    summary = _summary_excerpt(
        _text(notification.get("last-assistant-message"))
        or "El turno terminó sin un mensaje final para resumir.",
        summary_max_chars,
        summary_tail_chars,
    )
    facts = [
        {"title": "Estado", "value": "Completado"},
        {"title": "Duración", "value": _duration_text(duration_seconds)},
    ]
    if chat_id:
        facts.append({"title": "ID del chat", "value": chat_id})
    if chat_title:
        facts.append({"title": "Título", "value": chat_title})
    if cwd:
        facts.append({"title": "Proyecto", "value": Path(cwd).name or cwd})
    facts.append({"title": "Fecha y hora", "value": completed_at})

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "msteams": {"width": "Full"},
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": "✅ Turno de Codex completado",
                            "size": "Large",
                            "weight": "Bolder",
                            "color": "Good",
                            "wrap": True,
                        },
                        {"type": "FactSet", "facts": facts},
                        {
                            "type": "TextBlock",
                            "text": "Resumen de lo realizado",
                            "weight": "Bolder",
                            "wrap": True,
                            "separator": True,
                        },
                        {"type": "TextBlock", "text": summary, "wrap": True},
                    ],
                },
            }
        ],
    }


def build_discord_payload(
    notification: dict[str, Any],
    duration_seconds: float | None,
    marker: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = {} if config is None else config
    chat_id = _text(notification.get("thread-id")) or _text(marker.get("session_id"))
    chat_title = _chat_title(notification)
    cwd = _text(notification.get("cwd")) or _text(marker.get("cwd"))
    summary_max_chars = _config_int(
        settings,
        "discord",
        "summary_max_chars",
        DEFAULT_SUMMARY_MAX_CHARS,
        minimum=MIN_SUMMARY_MAX_CHARS,
        maximum=MAX_DISCORD_SUMMARY_MAX_CHARS,
    )
    summary = _truncate(
        _text(notification.get("last-assistant-message"))
        or "El turno terminó sin un mensaje final para resumir.",
        summary_max_chars,
    )
    fields = [
        {"name": "Estado", "value": "Completado", "inline": True},
        {
            "name": "Duración",
            "value": _duration_text(duration_seconds),
            "inline": True,
        },
    ]
    if cwd:
        fields.append(
            {
                "name": "Proyecto",
                "value": _truncate(Path(cwd).name or cwd, 100),
                "inline": True,
            }
        )
    if chat_title:
        fields.append({"name": "Título", "value": chat_title, "inline": False})
    if chat_id:
        fields.append(
            {"name": "ID del chat", "value": _truncate(chat_id, 200), "inline": False}
        )
    return {
        "username": "Codex Notifier",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": "✅ Turno de Codex completado",
                "description": summary,
                "color": 3061878,
                "fields": fields,
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        ],
    }


def _send_webhook_notification(
    service: str, webhook_url: str, payload: dict[str, Any]
) -> None:
    request = Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Codex-Notifier/1.0",
        },
        method="POST",
    )
    for attempt in range(2):
        try:
            with urlopen(request, timeout=5) as response:
                if 200 <= response.status < 300:
                    return
                raise RuntimeError(f"{service} respondió con HTTP {response.status}")
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 1:
                raise RuntimeError(f"{service} respondió con HTTP {exc.code}") from exc
        except URLError as exc:
            if attempt == 1:
                raise RuntimeError(
                    f"No se pudo conectar con {service}: {exc.reason}"
                ) from exc
        time.sleep(1)


def send_teams_notification(webhook_url: str, payload: dict[str, Any]) -> None:
    _send_webhook_notification("Teams", webhook_url, payload)


def send_discord_notification(webhook_url: str, payload: dict[str, Any]) -> None:
    parts = urlsplit(webhook_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["wait"] = "true"
    confirmed_url = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
    _send_webhook_notification("Discord", confirmed_url, payload)


def _voice_message(
    notification: dict[str, Any],
    duration_seconds: float | None,
    marker: dict[str, Any],
    language: str,
) -> str:
    cwd = _text(notification.get("cwd")) or _text(marker.get("cwd"))
    project = _truncate(Path(cwd).name, 60) if cwd else "actual"
    title = _chat_title(notification)
    extra = ""
    if title:
        extra = f" Task: {title}." if language == "en" else f" Tarea: {title}."
    if language == "en":
        if duration_seconds is None:
            base = (
                f"Codex finished the task for project {project}. "
                "The duration could not be determined."
            )
        else:
            duration = _spoken_duration(duration_seconds, language)
            base = f"Codex finished the task for project {project} after {duration}."
        return base + extra
    if duration_seconds is None:
        base = (
            f"Codex terminó la tarea del proyecto {project}. "
            "No se pudo determinar la duración."
        )
    else:
        duration = _spoken_duration(duration_seconds, language)
        base = f"Codex terminó la tarea del proyecto {project} después de {duration}."
    return base + extra


def _configured_volume(voice_config: dict[str, Any]) -> float | None:
    value = voice_config.get("volume")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return min(1.0, max(0.0, float(value)))


def speak_windows(
    message: str,
    *,
    language: str = "es",
    preferred_voice: str = "",
    volume: float | None = None,
) -> None:
    if os.name != "nt":
        raise RuntimeError("La voz local solo está disponible en Windows.")
    encoded = base64.b64encode(message.encode("utf-8")).decode("ascii")
    environment = os.environ.copy()
    environment["CODEX_NOTIFIER_SPEECH_B64"] = encoded
    environment["CODEX_NOTIFIER_SPEECH_LANGUAGE"] = language
    environment["CODEX_NOTIFIER_SPEECH_VOICE"] = preferred_voice
    environment["CODEX_NOTIFIER_SPEECH_VOLUME"] = (
        "" if volume is None else str(round(min(1.0, max(0.0, volume)) * 100))
    )
    script = (
        "$bytes=[Convert]::FromBase64String($env:CODEX_NOTIFIER_SPEECH_B64);"
        "$text=[Text.Encoding]::UTF8.GetString($bytes);"
        "$voice=New-Object -ComObject SAPI.SpVoice;"
        "$wantedName=$env:CODEX_NOTIFIER_SPEECH_VOICE;"
        "$wantedLanguage=$env:CODEX_NOTIFIER_SPEECH_LANGUAGE;"
        "$wantedVolume=$env:CODEX_NOTIFIER_SPEECH_VOLUME;"
        "$selected=$null;"
        "foreach($candidate in $voice.GetVoices()){"
        "$description=$candidate.GetDescription();"
        "if($wantedName -and $description -like ('*'+$wantedName+'*')){"
        "$selected=$candidate;break}"
        "if(-not $selected){try{"
        "$lcid=[Convert]::ToInt32($candidate.GetAttribute('Language'),16);"
        "$culture=[Globalization.CultureInfo]::GetCultureInfo($lcid);"
        "if($culture.TwoLetterISOLanguageName -eq $wantedLanguage){"
        "$selected=$candidate}}catch{}}};"
        "if($selected){$voice.Voice=$selected};"
        "if($wantedVolume -ne ''){$voice.Volume=[int]$wantedVolume};"
        "[void]$voice.Speak($text)"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        check=True,
        timeout=SPEECH_TIMEOUT_SECONDS,
    )


def _configured_voice(voice_config: dict[str, Any], language: str) -> str:
    key = "spanish_voice" if language == "es" else "english_voice"
    return _text(voice_config.get(key))


def _resolve_executable(configured: str, default: str) -> str | None:
    requested = configured or default
    if Path(requested).expanduser().parent != Path("."):
        path = Path(requested).expanduser()
        return str(path) if path.is_file() else None
    return shutil.which(requested)


def speak_piper(
    message: str, *, language: str = "es", voice_config: dict[str, Any]
) -> None:
    model_name = _configured_voice(voice_config, language)
    if not model_name:
        key = "spanish_voice" if language == "es" else "english_voice"
        raise RuntimeError(f"Configura voice.{key} para usar Piper.")
    model = Path(model_name).expanduser()
    if not model.is_file():
        raise RuntimeError(f"No se encontró el modelo de Piper: {model}")

    configured_executable = _text(voice_config.get("piper_executable"))
    piper = _resolve_executable(configured_executable, "piper")
    if not piper:
        raise RuntimeError(
            "No se encontró Piper. Instala piper-tts o configura "
            "voice.piper_executable."
        )

    powershell = shutil.which("powershell.exe")
    wslpath = shutil.which("wslpath")
    player = None
    if not (powershell and wslpath):
        player = next(
            (
                (name, shutil.which(name))
                for name in ("paplay", "pw-play", "aplay", "ffplay")
                if shutil.which(name)
            ),
            None,
        )
    if not (powershell and wslpath) and not player:
        raise RuntimeError(
            "No se encontró un reproductor compatible: instala paplay, pw-play, "
            "aplay o ffplay. En WSL también se admite powershell.exe."
        )

    with tempfile.TemporaryDirectory(prefix="codex-notifier-") as temporary_dir:
        wav_path = Path(temporary_dir) / "speech.wav"
        piper_command = [piper, "-m", str(model), "-f", str(wav_path)]
        volume = _configured_volume(voice_config)
        if volume is not None:
            piper_command.extend(["--volume", str(volume)])
        piper_command.extend(["--", message])
        subprocess.run(
            piper_command,
            cwd=temporary_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=SPEECH_TIMEOUT_SECONDS,
        )
        if powershell and wslpath:
            windows_path = subprocess.run(
                [wslpath, "-w", str(wav_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout.strip()
            encoded_path = base64.b64encode(windows_path.encode("utf-8")).decode(
                "ascii"
            )
            script = (
                "$ErrorActionPreference='Stop';"
                f"$bytes=[Convert]::FromBase64String('{encoded_path}');"
                "$path=[Text.Encoding]::UTF8.GetString($bytes);"
                "$player=New-Object System.Media.SoundPlayer $path;"
                "$player.Load();$player.PlaySync()"
            )
            subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=SPEECH_TIMEOUT_SECONDS,
            )
        else:
            player_name, player_path = player
            player_command = (
                [player_path, "-nodisp", "-autoexit", str(wav_path)]
                if player_name == "ffplay"
                else [player_path, str(wav_path)]
            )
            subprocess.run(
                player_command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=SPEECH_TIMEOUT_SECONDS,
            )


def speak_linux(
    message: str,
    *,
    language: str = "es",
    voice_config: dict[str, Any] | None = None,
) -> None:
    settings = {} if voice_config is None else voice_config
    if _text(settings.get("piper_executable")):
        speak_piper(message, language=language, voice_config=settings)
        return

    speech_dispatcher = shutil.which("spd-say")
    if speech_dispatcher:
        command = [speech_dispatcher, "-w", "-l", language, message]
    else:
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        if not espeak:
            raise RuntimeError(
                "Instala speech-dispatcher (spd-say) o espeak-ng para usar voz en Linux."
            )
        command = [espeak, "-v", language, message]
    subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=SPEECH_TIMEOUT_SECONDS,
    )


def speak(
    message: str,
    *,
    language: str = "es",
    preferred_voice: str = "",
    voice_config: dict[str, Any] | None = None,
) -> None:
    settings = {} if voice_config is None else voice_config
    if os.name == "nt":
        speak_windows(
            message,
            language=language,
            preferred_voice=preferred_voice,
            volume=_configured_volume(settings),
        )
        return
    if os.name == "posix":
        speak_linux(message, language=language, voice_config=settings)
        return
    raise RuntimeError(f"La voz local no es compatible con la plataforma {os.name}.")


def handle_completion(notification: dict[str, Any], *, now: float | None = None) -> None:
    if notification.get("type") != "agent-turn-complete":
        return
    duration, marker = consume_start(notification, now=now)
    try:
        config = load_config()
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        config = {}

    moment = datetime.now().astimezone()
    chat_id = (
        _text(notification.get("thread-id") or notification.get("thread_id"))
        or _text(marker.get("session_id"))
    )
    teams_due, discord_due, voice_due = notification_channels(
        duration, moment, config
    )
    teams_status = "not_due"
    discord_status = "not_due"
    voice_status = "not_due"
    teams_error = ""
    discord_error = ""
    voice_error = ""

    # Voice runs first and synchronously so a slow or failed webhook cannot
    # delay or cancel the local notification.
    if voice_due:
        try:
            voice_config = _config_section(config, "voice")
            language = detect_language(
                _text(notification.get("last-assistant-message")),
                _text(voice_config.get("language")) or "auto",
            )
            preferred_voice = _text(
                voice_config.get(
                    "spanish_voice" if language == "es" else "english_voice"
                )
            )
            speak(
                _voice_message(notification, duration, marker, language),
                language=language,
                preferred_voice=preferred_voice,
                voice_config=voice_config,
            )
            voice_status = "sent"
        except Exception as exc:
            voice_status = "failed"
            voice_error = _safe_error(exc)
            print(f"No se pudo reproducir el aviso de voz: {exc}", file=sys.stderr)

    if teams_due:
        webhook_url = _config_text(config, "teams", "webhook_url")
        if webhook_url and urlsplit(webhook_url).scheme == "https":
            try:
                send_teams_notification(
                    webhook_url,
                    build_payload(notification, duration, marker, config),
                )
                teams_status = "sent"
            except Exception as exc:
                teams_status = "failed"
                teams_error = _safe_error(exc, webhook_url)
                print(f"No se pudo enviar la notificación a Teams: {exc}", file=sys.stderr)
        else:
            teams_status = "failed"
            teams_error = (
                "El webhook de Teams debe usar HTTPS."
                if webhook_url
                else "No hay un webhook de Teams configurado."
            )
            print(teams_error, file=sys.stderr)

    if discord_due:
        webhook_url = _config_text(config, "discord", "webhook_url")
        if webhook_url and urlsplit(webhook_url).scheme == "https":
            try:
                send_discord_notification(
                    webhook_url,
                    build_discord_payload(notification, duration, marker, config),
                )
                discord_status = "sent"
            except Exception as exc:
                discord_status = "failed"
                discord_error = _safe_error(exc, webhook_url)
                print(
                    f"No se pudo enviar la notificación a Discord: {exc}",
                    file=sys.stderr,
                )
        else:
            discord_status = "failed"
            discord_error = (
                "El webhook de Discord debe usar HTTPS."
                if webhook_url
                else "No hay un webhook de Discord configurado."
            )
            print(discord_error, file=sys.stderr)

    try:
        write_trace_log(
            config,
            moment=moment,
            chat_id=chat_id,
            duration_seconds=duration,
            teams_status=teams_status,
            discord_status=discord_status,
            voice_status=voice_status,
            teams_error=teams_error,
            discord_error=discord_error,
            voice_error=voice_error,
        )
    except OSError as exc:
        print(f"No se pudo escribir el log del notificador: {exc}", file=sys.stderr)


def _read_json_stdin() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("el payload debe ser un objeto JSON")
    return payload


def _read_json_argument(index: int) -> dict[str, Any]:
    payload = json.loads(sys.argv[index])
    if not isinstance(payload, dict):
        raise TypeError("el payload debe ser un objeto JSON")
    return payload


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if command == "record-start":
            record_start(_read_json_stdin())
            return 0
        if command == "notify" and len(sys.argv) == 3:
            handle_completion(_read_json_argument(2))
            return 0
        if command == "voice-test":
            config = load_config()
            requested = sys.argv[2] if len(sys.argv) > 2 else "es"
            language = detect_language("", requested)
            voice_config = _config_section(config, "voice")
            preferred_voice = _text(
                voice_config.get(
                    "spanish_voice" if language == "es" else "english_voice"
                )
            )
            message = (
                "The Codex voice notification is working."
                if language == "en"
                else "La notificación por voz de Codex está funcionando."
            )
            speak(
                message,
                language=language,
                preferred_voice=preferred_voice,
                voice_config=voice_config,
            )
            return 0
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Payload de Codex inválido: {exc}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"Error del notificador: {exc}", file=sys.stderr)
        return 0

    print(
        "Uso: notifier.py record-start | notify '<json>' | voice-test [es|en]",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
