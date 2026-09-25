"""Private turn-marker lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .config import state_dir
from .files import atomic_write_text, secure_directory

STALE_MARKER_SECONDS = 48 * 60 * 60


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False).strip()


def marker_path(turn_id: str) -> Path:
    digest = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
    return state_dir() / "turns" / f"{digest}.json"


def cleanup_stale_markers(now: float) -> None:
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
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return False
    turn_id = text(payload.get("turn_id"))
    if not turn_id:
        return False
    timestamp = time.time() if now is None else now
    marker = marker_path(turn_id)
    secure_directory(state_dir())
    secure_directory(marker.parent)
    content = {
        "started_at": timestamp,
        "session_id": text(payload.get("session_id")),
        "turn_id": turn_id,
        "cwd": text(payload.get("cwd")),
    }
    atomic_write_text(marker, json.dumps(content, ensure_ascii=False), mode=0o600)
    cleanup_stale_markers(timestamp)
    return True


def consume_start(notification: dict[str, Any], *, now: float | None = None) -> tuple[float | None, dict[str, Any]]:
    turn_id = text(notification.get("turn-id") or notification.get("turn_id"))
    if not turn_id:
        return None, {}
    marker = marker_path(turn_id)
    try:
        content = json.loads(marker.read_text(encoding="utf-8"))
        started_at = float(content["started_at"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None, {}
    finally:
        marker.unlink(missing_ok=True)
    finished_at = time.time() if now is None else now
    return max(0.0, finished_at - started_at), content
