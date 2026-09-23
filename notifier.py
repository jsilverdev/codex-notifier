"""Filtered Codex completion notifications for Microsoft Teams and Windows.

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
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


WEBHOOK_ENV = "CODEX_TEAMS_WEBHOOK_URL"
TEAMS_ENABLED_ENV = "CODEX_NOTIFIER_TEAMS_ENABLED"
TEAMS_MIN_SECONDS_ENV = "CODEX_NOTIFIER_TEAMS_MIN_SECONDS"
VOICE_ENABLED_ENV = "CODEX_NOTIFIER_VOICE_ENABLED"
VOICE_MIN_SECONDS_ENV = "CODEX_NOTIFIER_VOICE_MIN_SECONDS"
QUIET_START_ENV = "CODEX_NOTIFIER_QUIET_START"
QUIET_END_ENV = "CODEX_NOTIFIER_QUIET_END"
STATE_DIR_ENV = "CODEX_NOTIFIER_STATE_DIR"
CONFIG_PATH_ENV = "CODEX_NOTIFIER_CONFIG"

DEFAULT_TEAMS_MIN_SECONDS = 300.0
DEFAULT_VOICE_MIN_SECONDS = 65.0
DEFAULT_QUIET_START = "23:00"
DEFAULT_QUIET_END = "07:00"
STALE_MARKER_SECONDS = 48 * 60 * 60
MAX_TITLE_CHARS = 120
MAX_SUMMARY_CHARS = 1800

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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_seconds(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


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
    config: dict[str, Any], section_name: str, key: str, env_name: str, default: bool
) -> bool:
    value = _config_section(config, section_name).get(key)
    if isinstance(value, bool):
        return value
    return _env_bool(env_name, default)


def _config_seconds(
    config: dict[str, Any], section_name: str, key: str, env_name: str, default: float
) -> float:
    value = _config_section(config, section_name).get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    return _env_seconds(env_name, default)


def _config_text(
    config: dict[str, Any],
    section_name: str,
    key: str,
    env_name: str | None,
    default: str = "",
) -> str:
    value = _config_section(config, section_name).get(key)
    if isinstance(value, str):
        return value.strip()
    if env_name:
        environment_value = os.environ.get(env_name)
        if environment_value is not None:
            return environment_value.strip()
    return default


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
            QUIET_START_ENV,
            DEFAULT_QUIET_START,
        ),
        DEFAULT_QUIET_START,
    )
    end = _parse_clock(
        _config_text(
            settings,
            "voice",
            "quiet_end",
            QUIET_END_ENV,
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
    duration_seconds: float,
    moment: datetime,
    config: dict[str, Any] | None = None,
) -> tuple[bool, bool]:
    settings = load_config() if config is None else config
    webhook_configured = bool(
        _config_text(settings, "teams", "webhook_url", WEBHOOK_ENV)
    )
    teams = _config_bool(
        settings, "teams", "enabled", TEAMS_ENABLED_ENV, webhook_configured
    ) and duration_seconds >= _config_seconds(
        settings,
        "teams",
        "minimum_seconds",
        TEAMS_MIN_SECONDS_ENV,
        DEFAULT_TEAMS_MIN_SECONDS,
    )
    voice = (
        _config_bool(settings, "voice", "enabled", VOICE_ENABLED_ENV, True)
        and duration_seconds
        >= _config_seconds(
            settings,
            "voice",
            "minimum_seconds",
            VOICE_MIN_SECONDS_ENV,
            DEFAULT_VOICE_MIN_SECONDS,
        )
        and not is_quiet_time(moment, settings)
    )
    return teams, voice


def _duration_text(duration_seconds: float) -> str:
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
    notification: dict[str, Any], duration_seconds: float, marker: dict[str, Any]
) -> dict[str, Any]:
    completed_at = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M %Z")
    chat_id = _text(notification.get("thread-id")) or _text(marker.get("session_id"))
    chat_title = _chat_title(notification)
    cwd = _text(notification.get("cwd")) or _text(marker.get("cwd"))
    summary = _truncate(
        _text(notification.get("last-assistant-message"))
        or "El turno terminó sin un mensaje final para resumir.",
        MAX_SUMMARY_CHARS,
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


def send_teams_notification(webhook_url: str, payload: dict[str, Any]) -> None:
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
                raise RuntimeError(f"Teams respondió con HTTP {response.status}")
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 1:
                raise RuntimeError(f"Teams respondió con HTTP {exc.code}") from exc
        except URLError as exc:
            if attempt == 1:
                raise RuntimeError(f"No se pudo conectar con Teams: {exc.reason}") from exc
        time.sleep(1)


def _voice_message(
    notification: dict[str, Any],
    duration_seconds: float,
    marker: dict[str, Any],
    language: str,
) -> str:
    cwd = _text(notification.get("cwd")) or _text(marker.get("cwd"))
    project = _truncate(Path(cwd).name, 60) if cwd else "actual"
    duration = _spoken_duration(duration_seconds, language)
    if language == "en":
        return f"Codex finished the task for project {project} after {duration}."
    return f"Codex terminó la tarea del proyecto {project} después de {duration}."


def speak_windows(
    message: str, *, language: str = "es", preferred_voice: str = ""
) -> None:
    if os.name != "nt":
        raise RuntimeError("La voz local solo está disponible en Windows.")
    encoded = base64.b64encode(message.encode("utf-8")).decode("ascii")
    environment = os.environ.copy()
    environment["CODEX_NOTIFIER_SPEECH_B64"] = encoded
    environment["CODEX_NOTIFIER_SPEECH_LANGUAGE"] = language
    environment["CODEX_NOTIFIER_SPEECH_VOICE"] = preferred_voice
    script = (
        "$bytes=[Convert]::FromBase64String($env:CODEX_NOTIFIER_SPEECH_B64);"
        "$text=[Text.Encoding]::UTF8.GetString($bytes);"
        "$voice=New-Object -ComObject SAPI.SpVoice;"
        "$wantedName=$env:CODEX_NOTIFIER_SPEECH_VOICE;"
        "$wantedLanguage=$env:CODEX_NOTIFIER_SPEECH_LANGUAGE;"
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
        "[void]$voice.Speak($text)"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
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
    )


def speak_linux(message: str, *, language: str = "es") -> None:
    speech_dispatcher = shutil.which("spd-say")
    if speech_dispatcher:
        command = [speech_dispatcher, "-l", language, message]
    else:
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        if not espeak:
            raise RuntimeError(
                "Instala speech-dispatcher (spd-say) o espeak-ng para usar voz en Linux."
            )
        command = [espeak, "-v", language, message]
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def speak(
    message: str, *, language: str = "es", preferred_voice: str = ""
) -> None:
    if os.name == "nt":
        speak_windows(
            message, language=language, preferred_voice=preferred_voice
        )
        return
    if os.name == "posix":
        speak_linux(message, language=language)
        return
    raise RuntimeError(f"La voz local no es compatible con la plataforma {os.name}.")


def handle_completion(notification: dict[str, Any], *, now: float | None = None) -> None:
    if notification.get("type") != "agent-turn-complete":
        return
    duration, marker = consume_start(notification, now=now)
    if duration is None:
        return

    try:
        config = load_config()
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        config = {}

    moment = datetime.now().astimezone()
    teams_due, voice_due = notification_channels(duration, moment, config)
    if teams_due:
        webhook_url = _config_text(
            config, "teams", "webhook_url", WEBHOOK_ENV
        )
        if webhook_url and urlsplit(webhook_url).scheme == "https":
            try:
                send_teams_notification(
                    webhook_url, build_payload(notification, duration, marker)
                )
            except Exception as exc:
                print(f"No se pudo enviar la notificación a Teams: {exc}", file=sys.stderr)
        elif webhook_url:
            print("El webhook de Teams debe usar HTTPS.", file=sys.stderr)

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
            )
        except Exception as exc:
            print(f"No se pudo reproducir el aviso de voz: {exc}", file=sys.stderr)


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
                message, language=language, preferred_voice=preferred_voice
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
