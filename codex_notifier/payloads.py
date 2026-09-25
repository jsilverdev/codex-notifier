"""Webhook payload construction without retaining assistant content."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .config import (DEFAULT_SUMMARY_MAX_CHARS, DEFAULT_SUMMARY_TAIL_CHARS,
                     MAX_DISCORD_SUMMARY_MAX_CHARS, MAX_SUMMARY_MAX_CHARS,
                     MIN_SUMMARY_MAX_CHARS, config_bool, config_int)
from .state import text

MAX_TITLE_CHARS = 120


def project_name(cwd: str) -> str:
    """Return the final component of a Windows or POSIX working directory."""
    path_type = PureWindowsPath if "\\" in cwd else PurePosixPath
    return path_type(cwd).name or cwd


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def summary_excerpt(value: str, limit: int, tail_chars: int) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value)
    for _ in range(10):
        omitted_text = f"{omitted:,}".replace(",", ".")
        separator = f"\n\n[… {omitted_text} caracteres omitidos …]\n\n"
        available = limit - len(separator)
        if available < 2:
            return truncate(value, limit)
        tail_length = min(max(0, tail_chars), available - 1)
        head_length = available - tail_length
        new_omitted = len(value) - head_length - tail_length
        if new_omitted == omitted:
            tail = value[-tail_length:] if tail_length else ""
            return value[:head_length] + separator + tail
        omitted = new_omitted
    return truncate(value, limit)


def duration_text(duration_seconds: float | None) -> str:
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


def chat_title(notification: dict[str, Any]) -> str:
    for key in ("thread-title", "thread_title", "title"):
        title = text(notification.get(key))
        if title:
            return truncate(title, MAX_TITLE_CHARS)
    return ""


def build_payload(notification: dict[str, Any], duration_seconds: float | None,
                  marker: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = {} if config is None else config
    completed_at = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M %Z")
    chat_id = text(notification.get("thread-id")) or text(marker.get("session_id"))
    title = chat_title(notification)
    cwd = text(notification.get("cwd")) or text(marker.get("cwd"))
    facts = [
        {"title": "Estado", "value": "Completado"},
        {"title": "Duración", "value": duration_text(duration_seconds)},
    ]
    if chat_id:
        facts.append({"title": "ID del chat", "value": chat_id})
    if title:
        facts.append({"title": "Título", "value": title})
    if cwd:
        facts.append({"title": "Proyecto", "value": project_name(cwd)})
    facts.append({"title": "Fecha y hora", "value": completed_at})
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": "✅ Turno de Codex completado", "size": "Large",
         "weight": "Bolder", "color": "Good", "wrap": True},
        {"type": "FactSet", "facts": facts},
    ]
    if config_bool(settings, "teams", "include_summary", True):
        maximum = config_int(settings, "teams", "summary_max_chars",
                             DEFAULT_SUMMARY_MAX_CHARS, minimum=MIN_SUMMARY_MAX_CHARS,
                             maximum=MAX_SUMMARY_MAX_CHARS)
        tail = config_int(settings, "teams", "summary_tail_chars", DEFAULT_SUMMARY_TAIL_CHARS)
        summary = summary_excerpt(text(notification.get("last-assistant-message")) or
                                  "El turno terminó sin un mensaje final para resumir.", maximum, tail)
        body.extend([
            {"type": "TextBlock", "text": "Resumen de lo realizado", "weight": "Bolder",
             "wrap": True, "separator": True},
            {"type": "TextBlock", "text": summary, "wrap": True},
        ])
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None,
        "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                     "type": "AdaptiveCard", "version": "1.2", "msteams": {"width": "Full"},
                     "body": body},
    }]}


def build_discord_payload(notification: dict[str, Any], duration_seconds: float | None,
                          marker: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = {} if config is None else config
    chat_id = text(notification.get("thread-id")) or text(marker.get("session_id"))
    title = chat_title(notification)
    cwd = text(notification.get("cwd")) or text(marker.get("cwd"))
    fields = [{"name": "Estado", "value": "Completado", "inline": True},
              {"name": "Duración", "value": duration_text(duration_seconds), "inline": True}]
    if cwd:
        fields.append({"name": "Proyecto", "value": truncate(project_name(cwd), 100), "inline": True})
    if title:
        fields.append({"name": "Título", "value": title, "inline": False})
    if chat_id:
        fields.append({"name": "ID del chat", "value": truncate(chat_id, 200), "inline": False})
    embed: dict[str, Any] = {"title": "✅ Turno de Codex completado", "color": 3061878,
                             "fields": fields,
                             "timestamp": datetime.now().astimezone().isoformat(timespec="seconds")}
    if config_bool(settings, "discord", "include_summary", True):
        maximum = config_int(settings, "discord", "summary_max_chars",
                             DEFAULT_SUMMARY_MAX_CHARS, minimum=MIN_SUMMARY_MAX_CHARS,
                             maximum=MAX_DISCORD_SUMMARY_MAX_CHARS)
        embed["description"] = truncate(text(notification.get("last-assistant-message")) or
                                         "El turno terminó sin un mensaje final para resumir.", maximum)
    return {"username": "Codex Notifier", "allowed_mentions": {"parse": []}, "embeds": [embed]}
