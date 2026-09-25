"""Discord webhook transport."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .webhook import send_webhook_notification


def send(webhook_url: str, payload: dict) -> None:
    parts = urlsplit(webhook_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["wait"] = "true"
    confirmed_url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                                urlencode(query), parts.fragment))
    send_webhook_notification("Discord", confirmed_url, payload)


send_discord_notification = send
