from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
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
        teams, discord, voice = notifier.notification_channels(
            29, datetime(2026, 9, 23, 12, 0)
        )
        self.assertFalse(teams)
        self.assertFalse(discord)
        self.assertFalse(voice)

    def test_voice_starts_at_thirty_seconds(self) -> None:
        _, _, voice = notifier.notification_channels(
            30, datetime(2026, 9, 23, 12, 0)
        )

        self.assertTrue(voice)

    def test_teams_defaults_to_disabled_without_webhook(self) -> None:
        teams, _, _ = notifier.notification_channels(
            1000, datetime(2026, 9, 23, 12, 0), {}
        )

        self.assertFalse(teams)

    def test_medium_turn_only_uses_voice(self) -> None:
        teams, discord, voice = notifier.notification_channels(
            180, datetime(2026, 9, 23, 12, 0)
        )
        self.assertFalse(teams)
        self.assertFalse(discord)
        self.assertTrue(voice)

    def test_long_turn_uses_teams_and_voice(self) -> None:
        config = {
            "teams": {
                "enabled": True,
                "minimum_seconds": 300,
                "webhook_url": "https://example.invalid/teams",
            }
        }
        teams, discord, voice = notifier.notification_channels(
            301, datetime(2026, 9, 23, 12, 0), config
        )
        self.assertTrue(teams)
        self.assertFalse(discord)
        self.assertTrue(voice)

    def test_quiet_hours_only_suppress_voice(self) -> None:
        config = {
            "teams": {
                "enabled": True,
                "minimum_seconds": 300,
                "webhook_url": "https://example.invalid/teams",
            }
        }
        teams, discord, voice = notifier.notification_channels(
            301, datetime(2026, 9, 23, 23, 0), config
        )
        self.assertTrue(teams)
        self.assertFalse(discord)
        self.assertFalse(voice)

    def test_unknown_duration_notifies_voice_only_by_default(self) -> None:
        teams, discord, voice = notifier.notification_channels(
            None, datetime(2026, 9, 23, 12, 0), {}
        )

        self.assertFalse(teams)
        self.assertFalse(discord)
        self.assertTrue(voice)

    def test_unknown_duration_options_are_independent(self) -> None:
        config = {
            "teams": {
                "enabled": True,
                "notify_when_duration_unknown": True,
                "webhook_url": "https://example.invalid/teams",
            },
            "discord": {
                "enabled": True,
                "notify_when_duration_unknown": True,
                "webhook_url": "https://example.invalid/discord",
            },
            "voice": {
                "enabled": True,
                "notify_when_duration_unknown": False,
                "quiet_start": "00:00",
                "quiet_end": "00:00",
            },
        }

        teams, discord, voice = notifier.notification_channels(
            None, datetime(2026, 9, 23, 12, 0), config
        )

        self.assertTrue(teams)
        self.assertTrue(discord)
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

    def test_discord_payload_contains_context_and_unknown_duration(self) -> None:
        payload = notifier.build_discord_payload(
            {
                "thread-id": "thread-1",
                "thread-title": "Agregar Discord",
                "cwd": r"C:\work\demo",
                "last-assistant-message": "Se agregó el nuevo canal.",
            },
            None,
            {},
        )

        embed = payload["embeds"][0]
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(embed["description"], "Se agregó el nuevo canal.")
        self.assertIn(
            {"name": "Duración", "value": "No disponible", "inline": True},
            embed["fields"],
        )
        self.assertIn(
            {"name": "Proyecto", "value": "demo", "inline": True},
            embed["fields"],
        )

    def test_discord_summary_respects_embed_description_limit(self) -> None:
        payload = notifier.build_discord_payload(
            {"last-assistant-message": "a" * 5000},
            300,
            {},
            {"discord": {"summary_max_chars": 5000}},
        )

        self.assertEqual(
            len(payload["embeds"][0]["description"]),
            notifier.MAX_DISCORD_SUMMARY_MAX_CHARS,
        )

    @patch("notifier._send_webhook_notification")
    def test_discord_requests_delivery_confirmation(self, send_webhook) -> None:
        notifier.send_discord_notification(
            "https://discord.invalid/hook?thread_id=123", {"embeds": []}
        )

        sent_url = send_webhook.call_args.args[1]
        self.assertIn("thread_id=123", sent_url)
        self.assertIn("wait=true", sent_url)

    def test_long_summary_keeps_configured_beginning_and_end(self) -> None:
        message = "INICIO-" + ("x" * 180) + "-FINAL"
        payload = notifier.build_payload(
            {"last-assistant-message": message},
            300,
            {},
            {"teams": {"summary_max_chars": 100, "summary_tail_chars": 20}},
        )

        summary = payload["attachments"][0]["content"]["body"][-1]["text"]
        self.assertEqual(len(summary), 100)
        self.assertTrue(summary.startswith("INICIO-"))
        self.assertTrue(summary.endswith("-FINAL"))
        self.assertIn("caracteres omitidos", summary)

    def test_summary_limits_are_bounded_for_teams(self) -> None:
        message = "a" * 7000
        payload = notifier.build_payload(
            {"last-assistant-message": message},
            300,
            {},
            {"teams": {"summary_max_chars": 50000, "summary_tail_chars": 1000}},
        )

        summary = payload["attachments"][0]["content"]["body"][-1]["text"]
        self.assertEqual(len(summary), notifier.MAX_SUMMARY_MAX_CHARS)

    def test_summary_tail_can_be_disabled(self) -> None:
        summary = notifier._summary_excerpt("abcdefghij" * 20, 100, 0)

        self.assertEqual(len(summary), 100)
        self.assertTrue(summary.startswith("abcdefghij"))
        self.assertFalse(summary.endswith("abcdefghij"))

    def test_config_file_controls_notification_channels(self) -> None:
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

        teams, discord, voice = notifier.notification_channels(
            30, datetime(2026, 9, 23, 12, 0)
        )

        self.assertTrue(teams)
        self.assertFalse(discord)
        self.assertTrue(voice)

    def test_channel_environment_variables_are_ignored(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CODEX_NOTIFIER_VOICE_ENABLED": "0",
                "CODEX_NOTIFIER_VOICE_MIN_SECONDS": "999",
            },
        ):
            _, _, voice = notifier.notification_channels(
                30, datetime(2026, 9, 23, 12, 0), {}
            )

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

    def test_voice_message_adds_the_chat_title_without_the_description(self) -> None:
        message = notifier._voice_message(
            {
                "cwd": r"C:\work\demo",
                "thread-title": "Agregar notificaciones de Discord",
                "last-assistant-message": "Descripción que no debe leerse.",
            },
            45,
            {},
            "es",
        )

        self.assertIn("Tarea: Agregar notificaciones de Discord.", message)
        self.assertNotIn("Descripción que no debe leerse", message)

    def test_voice_message_explains_unknown_duration(self) -> None:
        message = notifier._voice_message(
            {"cwd": r"C:\work\demo", "last-assistant-message": "Cambio listo."},
            None,
            {},
            "es",
        )

        self.assertIn("No se pudo determinar la duración", message)
        self.assertNotIn("Cambio listo", message)

    @patch("notifier._voice.PLATFORM_NAME", "nt")
    @patch("notifier.subprocess.run")
    def test_speech_waits_and_passes_language_and_preferred_voice(self, run) -> None:
        notifier.speak_windows(
            "Prueba",
            language="es",
            preferred_voice="Microsoft Helena Desktop",
            volume=0.65,
        )

        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["CODEX_NOTIFIER_SPEECH_LANGUAGE"], "es")
        self.assertEqual(
            environment["CODEX_NOTIFIER_SPEECH_VOICE"],
            "Microsoft Helena Desktop",
        )
        self.assertEqual(environment["CODEX_NOTIFIER_SPEECH_VOLUME"], "65")
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(
            run.call_args.kwargs["timeout"], notifier.SPEECH_TIMEOUT_SECONDS
        )

    @patch("notifier.tempfile.TemporaryDirectory")
    @patch("notifier.subprocess.run")
    @patch("notifier.shutil.which")
    @patch("notifier._voice.PLATFORM_NAME", "nt")
    def test_windows_piper_uses_powershell_soundplayer(
        self, which, run, temporary_directory
    ) -> None:
        model = Path(self.temp_dir.name) / "es_ES-test-medium.onnx"
        model.touch()
        temporary_directory.return_value.__enter__.return_value = self.temp_dir.name
        which.side_effect = lambda name: "/opt/piper/bin/piper" if name == "piper" else None

        notifier.speak(
            "Tarea terminada",
            language="es",
            voice_config={
                "piper_executable": "piper",
                "spanish_voice": str(model),
            },
        )

        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0][0], "powershell.exe")
        self.assertIn("System.Media.SoundPlayer", run.call_args_list[1].args[0][-1])
        self.assertIn("$player.Load();$player.PlaySync()", run.call_args_list[1].args[0][-1])

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

        log_path = next(notifier.log_dir().glob("notifier-*.jsonl"))
        entry = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(entry["chat_id"], "thread-file-config")
        self.assertEqual(entry["duration_seconds"], 100)
        self.assertEqual(entry["teams_status"], "sent")
        self.assertEqual(entry["voice_status"], "sent")
        serialized = json.dumps(entry)
        self.assertNotIn("La tarea está lista", serialized)
        self.assertNotIn("example.invalid", serialized)
        self.assertNotIn(r"C:\work\demo", serialized)

    @patch("notifier.speak")
    @patch("notifier.send_teams_notification")
    def test_teams_failure_does_not_prevent_voice(self, send_teams, speak) -> None:
        config = {
            "teams": {
                "enabled": True,
                "minimum_seconds": 0,
                "webhook_url": "https://secret.invalid/webhook-token",
            },
            "voice": {
                "enabled": True,
                "minimum_seconds": 0,
                "quiet_start": "00:00",
                "quiet_end": "00:00",
            },
        }
        notifier.config_path().write_text(json.dumps(config), encoding="utf-8")
        notifier.record_start(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "thread-failure",
                "turn_id": "turn-failure",
            },
            now=100,
        )
        send_teams.side_effect = RuntimeError(
            "falló https://secret.invalid/webhook-token"
        )

        notifier.handle_completion(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-failure",
                "turn-id": "turn-failure",
                "last-assistant-message": "Listo.",
            },
            now=200,
        )

        speak.assert_called_once()
        log_path = next(notifier.log_dir().glob("notifier-*.jsonl"))
        entry = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(entry["teams_status"], "failed")
        self.assertEqual(entry["voice_status"], "sent")
        self.assertIn("[redacted]", entry["teams_error"])
        self.assertNotIn("webhook-token", entry["teams_error"])

    @patch("notifier.send_discord_notification")
    def test_completion_sends_discord_embed(self, send_discord) -> None:
        config = {
            "teams": {"enabled": False},
            "discord": {
                "enabled": True,
                "minimum_seconds": 30,
                "webhook_url": "https://discord.invalid/webhook-token",
            },
            "voice": {"enabled": False},
        }
        notifier.config_path().write_text(json.dumps(config), encoding="utf-8")
        notifier.record_start(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "thread-discord",
                "turn_id": "turn-discord",
                "cwd": r"C:\work\demo",
            },
            now=100,
        )

        notifier.handle_completion(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-discord",
                "turn-id": "turn-discord",
                "cwd": r"C:\work\demo",
                "last-assistant-message": "Discord listo.",
            },
            now=140,
        )

        self.assertEqual(
            send_discord.call_args.args[0],
            "https://discord.invalid/webhook-token",
        )
        self.assertEqual(
            send_discord.call_args.args[1]["embeds"][0]["description"],
            "Discord listo.",
        )
        log_path = next(notifier.log_dir().glob("notifier-*.jsonl"))
        entry = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(entry["discord_status"], "sent")
        self.assertNotIn("discord.invalid", json.dumps(entry))

    @patch("notifier.speak")
    @patch("notifier.send_teams_notification")
    def test_voice_failure_does_not_prevent_teams(self, send_teams, speak) -> None:
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
            },
        }
        notifier.config_path().write_text(json.dumps(config), encoding="utf-8")
        notifier.record_start(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "thread-voice-failure",
                "turn_id": "turn-voice-failure",
            },
            now=100,
        )
        speak.side_effect = RuntimeError("SAPI no respondió")

        notifier.handle_completion(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-voice-failure",
                "turn-id": "turn-voice-failure",
                "last-assistant-message": "Listo.",
            },
            now=200,
        )

        send_teams.assert_called_once()
        log_path = next(notifier.log_dir().glob("notifier-*.jsonl"))
        entry = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(entry["teams_status"], "sent")
        self.assertEqual(entry["voice_status"], "failed")
        self.assertIn("SAPI no respondió", entry["voice_error"])

    @patch("notifier.speak")
    def test_missing_start_marker_notifies_voice_and_is_logged(self, speak) -> None:
        notifier.config_path().write_text(
            json.dumps(
                {
                    "voice": {
                        "enabled": True,
                        "notify_when_duration_unknown": True,
                        "quiet_start": "00:00",
                        "quiet_end": "00:00",
                    }
                }
            ),
            encoding="utf-8",
        )
        notifier.handle_completion(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-without-marker",
                "turn-id": "missing-turn",
                "last-assistant-message": "La tarea quedó lista.",
            }
        )

        speak.assert_called_once()
        self.assertIn(
            "No se pudo determinar la duración", speak.call_args.args[0]
        )
        log_path = next(notifier.log_dir().glob("notifier-*.jsonl"))
        entry = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(entry["chat_id"], "thread-without-marker")
        self.assertIsNone(entry["duration_seconds"])
        self.assertEqual(entry["teams_status"], "not_due")
        self.assertEqual(entry["discord_status"], "not_due")
        self.assertEqual(entry["voice_status"], "sent")

    def test_daily_log_cleanup_honors_retention(self) -> None:
        directory = notifier.log_dir()
        directory.mkdir(parents=True)
        old_log = directory / "notifier-2026-09-20.jsonl"
        recent_log = directory / "notifier-2026-09-22.jsonl"
        old_log.write_text("old\n", encoding="utf-8")
        recent_log.write_text("recent\n", encoding="utf-8")

        notifier.write_trace_log(
            {"logging": {"enabled": True, "retention_days": 2}},
            moment=datetime(2026, 9, 23, 12, 0),
            chat_id="thread-log",
            duration_seconds=129.25,
            teams_status="not_due",
            discord_status="not_due",
            voice_status="sent",
        )

        self.assertFalse(old_log.exists())
        self.assertTrue(recent_log.exists())
        today_log = directory / "notifier-2026-09-23.jsonl"
        entry = json.loads(today_log.read_text(encoding="utf-8"))
        self.assertEqual(entry["duration_seconds"], 129.25)

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

    @patch("notifier.subprocess.run")
    @patch("notifier.shutil.which")
    def test_linux_speech_prefers_spd_say(self, which, run) -> None:
        which.side_effect = lambda name: "/usr/bin/spd-say" if name == "spd-say" else None

        notifier.speak_linux("Tarea terminada", language="es")

        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/spd-say", "-w", "-l", "es", "Tarea terminada"],
        )
        self.assertTrue(run.call_args.kwargs["check"])

    @patch("notifier.subprocess.run")
    @patch("notifier.shutil.which")
    def test_linux_speech_falls_back_to_espeak(self, which, run) -> None:
        paths = {"espeak-ng": "/usr/bin/espeak-ng"}
        which.side_effect = lambda name: paths.get(name)

        notifier.speak_linux("Task complete", language="en")

        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/espeak-ng", "-v", "en", "Task complete"],
        )

    @patch("notifier._voice.speak_piper")
    @patch("notifier._voice.PLATFORM_NAME", "posix")
    def test_speak_selects_piper_before_linux_fallback(self, piper) -> None:
        config = {"piper_executable": "piper"}

        notifier.speak("Task complete", language="en", voice_config=config)

        piper.assert_called_once_with("Task complete", language="en", voice_config=config)

    @patch("notifier.tempfile.TemporaryDirectory")
    @patch("notifier.subprocess.run")
    @patch("notifier.shutil.which")
    def test_linux_piper_uses_language_model_and_pw_play(
        self, which, run, temporary_directory
    ) -> None:
        model = Path(self.temp_dir.name) / "es_ES-test-medium.onnx"
        model.touch()
        wav = Path(self.temp_dir.name) / "speech.wav"
        temporary_directory.return_value.__enter__.return_value = self.temp_dir.name
        which.side_effect = lambda name: {
            "piper": "/opt/piper/bin/piper",
            "pw-play": "/usr/bin/pw-play",
        }.get(name)

        with patch("notifier._voice.play_wav") as play_wav:
            notifier.speak_piper(
                "Tarea terminada",
                language="es",
                voice_config={
                    "piper_executable": "piper",
                    "spanish_voice": str(model),
                },
            )
            play_wav.assert_called_once_with(wav)

        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/opt/piper/bin/piper",
                "-m",
                str(model),
                "-f",
                str(wav),
                "--",
                "Tarea terminada",
            ],
        )
        with patch("notifier._voice.PLATFORM_NAME", "posix"):
            notifier._voice.play_wav(wav)
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/usr/bin/pw-play", str(wav)],
        )

    def test_linux_piper_requires_the_voice_for_the_requested_language(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "english_voice"):
            notifier.speak(
                "Task complete",
                language="en",
                voice_config={"piper_executable": "piper"},
            )

    @patch("notifier.tempfile.TemporaryDirectory")
    @patch("notifier.subprocess.run")
    @patch("notifier.shutil.which")
    def test_linux_piper_passes_configured_volume(
        self, which, run, temporary_directory
    ) -> None:
        model = Path(self.temp_dir.name) / "es_MX-test-high.onnx"
        model.touch()
        temporary_directory.return_value.__enter__.return_value = self.temp_dir.name
        which.side_effect = lambda name: {
            "piper": "/opt/piper/bin/piper",
            "pw-play": "/usr/bin/pw-play",
        }.get(name)

        with patch("notifier._voice.play_wav"):
            notifier.speak_piper(
                "Tarea terminada",
                language="es",
                voice_config={
                    "piper_executable": "piper",
                    "spanish_voice": str(model),
                    "volume": 0.65,
                },
            )

        self.assertIn("--volume", run.call_args_list[0].args[0])
        self.assertIn("0.65", run.call_args_list[0].args[0])

    @patch("notifier.tempfile.TemporaryDirectory")
    @patch("notifier.subprocess.run")
    @patch("notifier.shutil.which")
    def test_linux_piper_uses_windows_audio_in_wsl(
        self, which, run, temporary_directory
    ) -> None:
        model = Path(self.temp_dir.name) / "en_US-test-high.onnx"
        model.touch()
        wav = Path(self.temp_dir.name) / "speech.wav"
        temporary_directory.return_value.__enter__.return_value = self.temp_dir.name
        which.side_effect = lambda name: {
            "piper": "/opt/piper/bin/piper",
            "powershell.exe": "/mnt/c/Windows/System32/powershell.exe",
            "wslpath": "/usr/bin/wslpath",
        }.get(name)
        run.side_effect = [
            SimpleNamespace(),
            SimpleNamespace(stdout=r"C:\\wsl.localhost\Debian\tmp\speech 'demo'.wav" + "\n"),
            SimpleNamespace(),
        ]

        with patch("notifier._voice.play_wav") as play_wav:
            notifier.speak_piper(
                "Task complete",
                language="en",
                voice_config={
                    "piper_executable": "piper",
                    "english_voice": str(model),
                },
            )
            play_wav.assert_called_once_with(wav)

        with patch("notifier._voice.PLATFORM_NAME", "posix"):
            notifier._voice.play_wav(wav)

        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/usr/bin/wslpath", "-w", str(wav)],
        )
        powershell_call = run.call_args_list[2]
        self.assertEqual(
            powershell_call.args[0][0],
            "/mnt/c/Windows/System32/powershell.exe",
        )
        powershell_script = powershell_call.args[0][-1]
        self.assertIn("FromBase64String($env:CODEX_NOTIFIER_WAV_B64)", powershell_script)
        self.assertEqual(
            base64.b64decode(
                powershell_call.kwargs["env"]["CODEX_NOTIFIER_WAV_B64"]
            ).decode("utf-8"),
            r"C:\\wsl.localhost\Debian\tmp\speech 'demo'.wav",
        )

    @patch("notifier.shutil.which", return_value=None)
    def test_linux_piper_reports_missing_wav_player(self, which) -> None:
        wav_path = Path(self.temp_dir.name) / "speech.wav"
        with patch("notifier._voice.PLATFORM_NAME", "posix"):
            with self.assertRaisesRegex(RuntimeError, "reproductor compatible"):
                notifier._voice.play_wav(wav_path)


if __name__ == "__main__":
    unittest.main()
