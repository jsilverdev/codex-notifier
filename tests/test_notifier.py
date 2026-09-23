from __future__ import annotations

import os
import sys
import tempfile
import unittest
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import notifier  # noqa: E402


class NotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.environment = patch.dict(
            os.environ,
            {
                notifier.STATE_DIR_ENV: self.temp_dir.name,
                notifier.CONFIG_PATH_ENV: str(Path(self.temp_dir.name) / "config.json"),
                notifier.TEAMS_ENABLED_ENV: "1",
                notifier.TEAMS_MIN_SECONDS_ENV: "300",
                notifier.VOICE_ENABLED_ENV: "1",
                notifier.VOICE_MIN_SECONDS_ENV: "65",
                notifier.QUIET_START_ENV: "23:00",
                notifier.QUIET_END_ENV: "07:00",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_dir.cleanup()

    def test_records_and_consumes_a_turn_without_prompt_content(self) -> None:
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "thread-1",
            "turn_id": "turn-1",
            "cwd": r"C:\work\demo",
            "prompt": "do not persist this",
        }
        self.assertTrue(notifier.record_start(payload, now=100.0))

        duration, marker = notifier.consume_start(
            {"turn-id": "turn-1"}, now=245.5
        )

        self.assertEqual(duration, 145.5)
        self.assertNotIn("prompt", marker)
        self.assertEqual(marker["session_id"], "thread-1")
        self.assertFalse(notifier._marker_path("turn-1").exists())

    def test_short_turn_has_no_channels(self) -> None:
        teams, voice = notifier.notification_channels(
            64, datetime(2026, 9, 23, 12, 0)
        )
        self.assertFalse(teams)
        self.assertFalse(voice)

    def test_teams_defaults_to_disabled_without_webhook(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            teams, _ = notifier.notification_channels(
                1000, datetime(2026, 9, 23, 12, 0), {}
            )

        self.assertFalse(teams)

    def test_medium_turn_only_uses_voice(self) -> None:
        teams, voice = notifier.notification_channels(
            180, datetime(2026, 9, 23, 12, 0)
        )
        self.assertFalse(teams)
        self.assertTrue(voice)

    def test_long_turn_uses_teams_and_voice(self) -> None:
        teams, voice = notifier.notification_channels(
            301, datetime(2026, 9, 23, 12, 0)
        )
        self.assertTrue(teams)
        self.assertTrue(voice)

    def test_quiet_hours_only_suppress_voice(self) -> None:
        teams, voice = notifier.notification_channels(
            301, datetime(2026, 9, 23, 23, 0)
        )
        self.assertTrue(teams)
        self.assertFalse(voice)

    def test_payload_contains_duration_and_summary(self) -> None:
        payload = notifier.build_payload(
            {
                "thread-id": "thread-1",
                "cwd": r"C:\work\demo",
                "last-assistant-message": "Listo.",
            },
            185,
            {},
        )
        content = payload["attachments"][0]["content"]
        facts = content["body"][1]["facts"]
        self.assertIn({"title": "Duración", "value": "3 min 5 s"}, facts)
        self.assertEqual(content["body"][-1]["text"], "Listo.")

    def test_config_file_takes_precedence_over_environment(self) -> None:
        config = {
            "teams": {"enabled": True, "minimum_seconds": 10},
            "voice": {
                "enabled": True,
                "minimum_seconds": 20,
                "quiet_start": "23:00",
                "quiet_end": "07:00",
            },
        }
        notifier.config_path().write_text(json.dumps(config), encoding="utf-8")

        teams, voice = notifier.notification_channels(
            30, datetime(2026, 9, 23, 12, 0)
        )

        self.assertTrue(teams)
        self.assertTrue(voice)

    def test_detects_spanish_and_english(self) -> None:
        self.assertEqual(
            notifier.detect_language("La tarea ya está lista para revisar."), "es"
        )
        self.assertEqual(
            notifier.detect_language("The task is ready for review."), "en"
        )
        self.assertEqual(notifier.detect_language("Done", "es"), "es")

    def test_voice_message_is_localized(self) -> None:
        notification = {"cwd": r"C:\work\demo"}
        marker: dict[str, object] = {}
        self.assertIn(
            "terminó la tarea",
            notifier._voice_message(notification, 301, marker, "es"),
        )
        self.assertIn(
            "finished the task",
            notifier._voice_message(notification, 301, marker, "en"),
        )

    @patch("notifier.subprocess.Popen")
    def test_speech_passes_language_and_preferred_voice(self, popen) -> None:
        notifier.speak_windows(
            "Prueba", language="es", preferred_voice="Microsoft Helena Desktop"
        )

        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["CODEX_NOTIFIER_SPEECH_LANGUAGE"], "es")
        self.assertEqual(
            environment["CODEX_NOTIFIER_SPEECH_VOICE"],
            "Microsoft Helena Desktop",
        )

    @patch("notifier.speak")
    @patch("notifier.send_teams_notification")
    def test_completion_uses_file_webhook_and_spanish_voice(
        self, send_teams, speak
    ) -> None:
        config = {
            "teams": {
                "enabled": True,
                "minimum_seconds": 0,
                "webhook_url": "https://example.invalid/webhook",
            },
            "voice": {
                "enabled": True,
                "minimum_seconds": 0,
                "quiet_start": "00:00",
                "quiet_end": "00:00",
                "language": "auto",
                "spanish_voice": "Microsoft Helena Desktop",
                "english_voice": "Microsoft Zira Desktop",
            },
        }
        notifier.config_path().write_text(json.dumps(config), encoding="utf-8")
        notifier.record_start(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "thread-file-config",
                "turn_id": "turn-file-config",
                "cwd": r"C:\work\demo",
            },
            now=100,
        )

        notifier.handle_completion(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-file-config",
                "turn-id": "turn-file-config",
                "cwd": r"C:\work\demo",
                "last-assistant-message": "La tarea está lista para revisar.",
            },
            now=200,
        )

        self.assertEqual(send_teams.call_args.args[0], "https://example.invalid/webhook")
        self.assertEqual(speak.call_args.kwargs["language"], "es")
        self.assertEqual(
            speak.call_args.kwargs["preferred_voice"],
            "Microsoft Helena Desktop",
        )

    def test_linux_uses_xdg_paths(self) -> None:
        home = Path("/home/tester")
        self.assertEqual(
            notifier.default_config_path(
                platform_name="posix",
                environment={"XDG_CONFIG_HOME": "/custom/config"},
                home=home,
            ),
            Path("/custom/config/codex-notifier/config.json"),
        )
        self.assertEqual(
            notifier.default_state_dir(
                platform_name="posix", environment={}, home=home
            ),
            Path("/home/tester/.local/state/codex-notifier"),
        )

    @patch("notifier.subprocess.Popen")
    @patch("notifier.shutil.which")
    def test_linux_speech_prefers_spd_say(self, which, popen) -> None:
        which.side_effect = lambda name: "/usr/bin/spd-say" if name == "spd-say" else None

        notifier.speak_linux("Tarea terminada", language="es")

        self.assertEqual(
            popen.call_args.args[0],
            ["/usr/bin/spd-say", "-l", "es", "Tarea terminada"],
        )

    @patch("notifier.subprocess.Popen")
    @patch("notifier.shutil.which")
    def test_linux_speech_falls_back_to_espeak(self, which, popen) -> None:
        paths = {"espeak-ng": "/usr/bin/espeak-ng"}
        which.side_effect = lambda name: paths.get(name)

        notifier.speak_linux("Task complete", language="en")

        self.assertEqual(
            popen.call_args.args[0],
            ["/usr/bin/espeak-ng", "-v", "en", "Task complete"],
        )


if __name__ == "__main__":
    unittest.main()
