"""Microsoft Teams webhook transport."""

from .webhook import send_webhook_notification


def send(webhook_url: str, payload: dict) -> None:
    send_webhook_notification("Teams", webhook_url, payload)


send_teams_notification = send
