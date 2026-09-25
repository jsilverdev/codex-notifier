"""Privacy-safe daily JSONL trace logging."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any

from .config import (DEFAULT_LOG_RETENTION_DAYS, MAX_LOG_RETENTION_DAYS,
                     config_bool, config_int, log_dir, state_dir)
from .files import secure_directory, secure_file

MAX_LOG_ERROR_CHARS = 300


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def safe_error(exc: Exception, *secrets: str) -> str:
    message = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    # Error strings from subprocesses can contain configured model paths.
    message = re.sub(r"[A-Za-z]:\\[^\s,'\]]+", "[path redacted]", message)
    message = re.sub(r"(?<!\w)/(?:[^\s,'\]]+/)+[^\s,'\]]+", "[path redacted]", message)
    return truncate(message, MAX_LOG_ERROR_CHARS)


def cleanup_old_logs(directory, moment: datetime, retention_days: int) -> None:
    oldest = moment.date() - timedelta(days=retention_days - 1)
    for path in directory.glob("notifier-????-??-??.jsonl"):
        try:
            file_date = datetime.strptime(path.stem[9:], "%Y-%m-%d").date()
            if file_date < oldest:
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue


def write_trace_log(config: dict[str, Any], *, moment: datetime, chat_id: str,
                    duration_seconds: float | None, teams_status: str,
                    discord_status: str, voice_status: str,
                    teams_error: str = "", discord_error: str = "",
                    voice_error: str = "") -> None:
    if not config_bool(config, "logging", "enabled", True):
        return
    retention_days = config_int(config, "logging", "retention_days",
                                DEFAULT_LOG_RETENTION_DAYS, minimum=1,
                                maximum=MAX_LOG_RETENTION_DAYS)
    secure_directory(state_dir())
    directory = secure_directory(log_dir())
    cleanup_old_logs(directory, moment, retention_days)
    entry: dict[str, Any] = {
        "timestamp": moment.isoformat(timespec="seconds"),
        "chat_id": chat_id,
        "duration_seconds": round(duration_seconds, 3) if duration_seconds is not None else None,
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
        stream.flush()
        os.fsync(stream.fileno())
    secure_file(path)
