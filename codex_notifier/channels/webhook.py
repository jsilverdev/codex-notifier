"""Bounded webhook retry transport. URL policy is intentionally caller-owned."""

from __future__ import annotations

import json
import time
import email.utils
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_DELAY_SECONDS = 5.0
RETRY_DELAY_SECONDS = 1.0


def _retry_after(value: str | None) -> float:
    if not value:
        return RETRY_DELAY_SECONDS
    try:
        return min(MAX_RETRY_DELAY_SECONDS, max(0.0, float(value)))
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value).timestamp()
            return min(MAX_RETRY_DELAY_SECONDS, max(0.0, when - time.time()))
        except (TypeError, ValueError, OverflowError):
            return RETRY_DELAY_SECONDS


def send_webhook_notification(service: str, webhook_url: str, payload: dict) -> None:
    request = Request(webhook_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8",
                               "User-Agent": "Codex-Notifier/1.0"}, method="POST")
    for attempt in range(2):
        try:
            with urlopen(request, timeout=5) as response:
                if 200 <= response.status < 300:
                    return
                if response.status not in RETRYABLE_STATUSES or attempt == 1:
                    raise RuntimeError(f"{service} respondió con HTTP {response.status}")
                headers = response.headers or {}
                delay = _retry_after(headers.get("Retry-After"))
        except HTTPError as exc:
            if exc.code not in RETRYABLE_STATUSES or attempt == 1:
                raise RuntimeError(f"{service} respondió con HTTP {exc.code}") from exc
            headers = exc.headers or {}
            delay = _retry_after(headers.get("Retry-After"))
        except URLError as exc:
            if attempt == 1:
                raise RuntimeError(f"No se pudo conectar con {service}: {exc.reason}") from exc
            delay = RETRY_DELAY_SECONDS
        if delay:
            time.sleep(delay)
