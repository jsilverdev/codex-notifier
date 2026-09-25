"""Command dispatch and completion orchestration."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

from .channels import discord, teams
from .channels.voice import detect_language, speak
from .config import (DEFAULT_DISCORD_MIN_SECONDS, DEFAULT_QUIET_END,
                     DEFAULT_QUIET_START, DEFAULT_TEAMS_MIN_SECONDS,
                     DEFAULT_VOICE_MIN_SECONDS, config_bool, config_section,
                     config_seconds, config_text, load_config)
from .payloads import build_discord_payload, build_payload, project_name
from .state import consume_start, record_start, text
from .trace_logging import safe_error, write_trace_log


def parse_clock(value: str, default: str) -> int:
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
    start = parse_clock(config_text(settings, "voice", "quiet_start", DEFAULT_QUIET_START), DEFAULT_QUIET_START)
    end = parse_clock(config_text(settings, "voice", "quiet_end", DEFAULT_QUIET_END), DEFAULT_QUIET_END)
    if start == end:
        return False
    current = moment.hour * 60 + moment.minute
    return start <= current < end if start < end else current >= start or current < end


def notification_channels(duration_seconds: float | None, moment: datetime,
                          config: dict[str, Any] | None = None) -> tuple[bool, bool, bool]:
    settings = load_config() if config is None else config
    def due(section: str, default: float, unknown: bool) -> bool:
        return (config_bool(settings, section, "notify_when_duration_unknown", unknown)
                if duration_seconds is None else duration_seconds >= config_seconds(settings, section, "minimum_seconds", default))
    teams_due = due("teams", DEFAULT_TEAMS_MIN_SECONDS, False)
    discord_due = due("discord", DEFAULT_DISCORD_MIN_SECONDS, False)
    voice_due = due("voice", DEFAULT_VOICE_MIN_SECONDS, True)
    teams_enabled = config_bool(settings, "teams", "enabled", bool(config_text(settings, "teams", "webhook_url")))
    discord_enabled = config_bool(settings, "discord", "enabled", bool(config_text(settings, "discord", "webhook_url")))
    return (teams_enabled and teams_due, discord_enabled and discord_due,
            config_bool(settings, "voice", "enabled", True) and voice_due and not is_quiet_time(moment, settings))


def spoken_duration(duration_seconds: float, language: str) -> str:
    total_seconds = max(0, round(duration_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if language == "en":
        parts = ([f"{hours} hour" + ("s" if hours != 1 else "")] if hours else [])
        if minutes:
            parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
        if seconds or not parts:
            parts.append(f"{seconds} second" + ("s" if seconds != 1 else ""))
        return " ".join(parts)
    parts = ([f"{hours} hora" + ("s" if hours != 1 else "")] if hours else [])
    if minutes:
        parts.append(f"{minutes} minuto" + ("s" if minutes != 1 else ""))
    if seconds or not parts:
        parts.append(f"{seconds} segundo" + ("s" if seconds != 1 else ""))
    return " ".join(parts)


def voice_message(notification: dict[str, Any], duration_seconds: float | None,
                  marker: dict[str, Any], language: str) -> str:
    cwd = text(notification.get("cwd")) or text(marker.get("cwd"))
    project = (project_name(cwd)[:60] if cwd else "actual")
    title = text(notification.get("thread-title") or notification.get("thread_title") or notification.get("title"))
    extra = (f" Task: {title}." if language == "en" else f" Tarea: {title}.") if title else ""
    if language == "en":
        base = (f"Codex finished the task for project {project}. The duration could not be determined."
                if duration_seconds is None else f"Codex finished the task for project {project} after {spoken_duration(duration_seconds, language)}.")
    else:
        base = (f"Codex terminó la tarea del proyecto {project}. No se pudo determinar la duración."
                if duration_seconds is None else f"Codex terminó la tarea del proyecto {project} después de {spoken_duration(duration_seconds, language)}.")
    return base + extra


def handle_completion(notification: dict[str, Any], *, now: float | None = None,
                      load=load_config, consume=consume_start,
                      speak_fn=speak, send_teams_fn=teams.send,
                      send_discord_fn=discord.send, log_fn=write_trace_log) -> None:
    if notification.get("type") != "agent-turn-complete":
        return
    duration, marker = consume(notification, now=now)
    try:
        config = load()
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        config = {}
    moment = datetime.now().astimezone()
    chat_id = text(notification.get("thread-id") or notification.get("thread_id")) or text(marker.get("session_id"))
    teams_due, discord_due, voice_due = notification_channels(duration, moment, config)
    statuses = {"teams": "not_due", "discord": "not_due", "voice": "not_due"}
    errors = {"teams": "", "discord": "", "voice": ""}
    summary_secret = text(notification.get("last-assistant-message"))
    if voice_due:
        try:
            voice_config = config_section(config, "voice")
            language = detect_language(text(notification.get("last-assistant-message")), text(voice_config.get("language")) or "auto")
            preferred = text(voice_config.get("spanish_voice" if language == "es" else "english_voice"))
            speak_fn(voice_message(notification, duration, marker, language), language=language,
                     preferred_voice=preferred, voice_config=voice_config)
            statuses["voice"] = "sent"
        except Exception as exc:
            statuses["voice"] = "failed"
            errors["voice"] = safe_error(exc, summary_secret)
            print(f"No se pudo reproducir el aviso de voz: {errors['voice']}", file=sys.stderr)
    for name, due_flag, sender, builder in (("teams", teams_due, send_teams_fn, build_payload),
                                             ("discord", discord_due, send_discord_fn, build_discord_payload)):
        if not due_flag:
            continue
        webhook_url = config_text(config, name, "webhook_url")
        if webhook_url and urlsplit(webhook_url).scheme == "https":
            try:
                sender(webhook_url, builder(notification, duration, marker, config))
                statuses[name] = "sent"
            except Exception as exc:
                statuses[name] = "failed"
                errors[name] = safe_error(exc, webhook_url, summary_secret)
                print(f"No se pudo enviar la notificación a {name.title()}: {errors[name]}", file=sys.stderr)
        else:
            statuses[name] = "failed"
            errors[name] = f"El webhook de {name.title()} debe usar HTTPS." if webhook_url else f"No hay un webhook de {name.title()} configurado."
            print(errors[name], file=sys.stderr)
    try:
        log_fn(config, moment=moment, chat_id=chat_id, duration_seconds=duration,
               teams_status=statuses["teams"], discord_status=statuses["discord"],
               voice_status=statuses["voice"], teams_error=errors["teams"],
               discord_error=errors["discord"], voice_error=errors["voice"])
    except OSError as exc:
        print(f"No se pudo escribir el log del notificador: {exc}", file=sys.stderr)


def _json_stdin() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("el payload debe ser un objeto JSON")
    return payload


def _json_argument(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise TypeError("el payload debe ser un objeto JSON")
    return payload


def main(argv: list[str] | None = None, *, speak_fn=speak) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    command = arguments[0] if arguments else ""
    try:
        if command == "record-start" and len(arguments) == 1:
            record_start(_json_stdin())
            return 0
        if command == "notify" and len(arguments) == 2:
            handle_completion(_json_argument(arguments[1]))
            return 0
        if command == "voice-test" and len(arguments) in {1, 2}:
            requested = arguments[1] if len(arguments) == 2 else "es"
            if requested.lower() not in {"es", "en"}:
                raise ValueError("voice-test requiere es o en")
            config = load_config()
            language = detect_language("", requested)
            voice_config = config_section(config, "voice")
            preferred = text(voice_config.get("spanish_voice" if language == "es" else "english_voice"))
            message = "The Codex voice notification is working." if language == "en" else "La notificación por voz de Codex está funcionando."
            speak_fn(message, language=language, preferred_voice=preferred, voice_config=voice_config)
            return 0
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Payload o sintaxis inválida: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error del notificador: {exc}", file=sys.stderr)
        return 1 if command == "voice-test" else 0
    print("Uso: notifier.py record-start | notify '<json>' | voice-test [es|en]", file=sys.stderr)
    return 2
