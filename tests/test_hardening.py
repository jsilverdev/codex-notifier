from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from codex_notifier.channels import webhook
from codex_notifier.channels import voice
from codex_notifier import cli
from codex_notifier.files import atomic_write_text
import notifier


class HardeningTests(unittest.TestCase):
    def test_summary_disabled_is_absent_from_both_serialized_payloads(self) -> None:
        secret = "SENSITIVE-ASSISTANT-SUMMARY-42"
        notification = {"last-assistant-message": secret, "thread-id": "chat"}
        teams = json.dumps(notifier.build_payload(notification, 10, {}, {"teams": {"include_summary": False}}))
        discord = json.dumps(notifier.build_discord_payload(notification, 10, {}, {"discord": {"include_summary": False}}))
        self.assertNotIn(secret, teams)
        self.assertNotIn(secret, discord)
        self.assertNotIn("Resumen de lo realizado", teams)
        self.assertNotIn("description", discord)

    @patch("notifier.send_teams_notification")
    def test_disabled_summary_is_not_written_to_channel_error_logs(self, send) -> None:
        secret = "SENSITIVE-ASSISTANT-SUMMARY-43"
        send.side_effect = RuntimeError(secret)
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {
                notifier.STATE_DIR_ENV: temporary,
                notifier.CONFIG_PATH_ENV: str(Path(temporary) / "config.json"),
            }):
                Path(temporary, "config.json").write_text(json.dumps({
                    "teams": {"enabled": True, "minimum_seconds": 0,
                              "notify_when_duration_unknown": True,
                              "include_summary": False,
                              "webhook_url": "https://example.invalid/hook"},
                    "voice": {"enabled": False},
                }))
                notifier.handle_completion({
                    "type": "agent-turn-complete", "thread-id": "chat",
                    "last-assistant-message": secret,
                })
                log = next(Path(temporary, "logs").glob("*.jsonl"))
                self.assertNotIn(secret, log.read_text())

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not Windows semantics")
    def test_state_directory_and_marker_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {notifier.STATE_DIR_ENV: temporary}):
                notifier.record_start({
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "session",
                    "turn_id": "turn",
                    "cwd": "/work/project",
                }, now=1)
                turns = Path(temporary) / "turns"
                marker = next(turns.glob("*.json"))
                self.assertEqual(turns.stat().st_mode & 0o777, 0o700)
                self.assertEqual(marker.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not Windows semantics")
    def test_atomic_write_sets_private_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            atomic_write_text(path, "{}\n", mode=0o600, platform_name="posix")
            self.assertEqual(path.read_text(), "{}\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @patch("codex_notifier.channels.webhook.time.sleep")
    @patch("codex_notifier.channels.webhook.urlopen")
    def test_retry_after_is_used_for_transient_http_error(self, urlopen, sleep) -> None:
        from urllib.error import HTTPError
        error = HTTPError("https://example.invalid", 503, "busy", {"Retry-After": "2"}, None)
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        urlopen.side_effect = [error, response]
        webhook.send_webhook_notification("Teams", "https://example.invalid", {})
        sleep.assert_called_once_with(2.0)

    @patch("codex_notifier.channels.voice.speak_sapi_windows")
    @patch("codex_notifier.channels.voice.speak_piper")
    @patch("codex_notifier.channels.voice.os.name", "nt")
    def test_windows_voice_defaults_to_sapi_and_uses_piper_when_configured(self, piper, sapi) -> None:
        voice.speak("hello", language="en", voice_config={})
        sapi.assert_called_once()
        piper.assert_not_called()
        sapi.reset_mock()
        voice.speak("hello", language="en", voice_config={"piper_executable": "piper"})
        piper.assert_called_once()
        sapi.assert_not_called()

    @patch("codex_notifier.channels.voice.speak_sapi_windows")
    @patch("codex_notifier.channels.voice.speak_piper", side_effect=RuntimeError("bad Piper"))
    @patch("codex_notifier.channels.voice.os.name", "nt")
    def test_explicit_piper_failure_does_not_fallback_to_sapi(self, piper, sapi) -> None:
        with self.assertRaisesRegex(RuntimeError, "bad Piper"):
            voice.speak("hello", voice_config={"piper_executable": "piper"})
        sapi.assert_not_called()

    def test_manual_cli_errors_have_nonzero_exit_codes(self) -> None:
        self.assertNotEqual(cli.main(["unknown-command"]), 0)
        self.assertNotEqual(cli.main(["voice-test", "fr"]), 0)


if __name__ == "__main__":
    unittest.main()
