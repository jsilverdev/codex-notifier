from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import install  # noqa: E402


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_default_config_paths_support_windows_and_linux(self) -> None:
        self.assertEqual(
            install.default_local_config_path(
                platform_name="nt",
                environment={"LOCALAPPDATA": r"C:\Local"},
                home=Path(r"C:\Users\test"),
            ),
            Path(r"C:\Local") / "CodexNotifier" / "config.json",
        )
        self.assertEqual(
            install.default_local_config_path(
                platform_name="posix", environment={}, home=Path("/home/test")
            ),
            Path("/home/test/.config/codex-notifier/config.json"),
        )

    def test_example_has_safe_requested_defaults(self) -> None:
        config = json.loads(
            (ROOT / "config.example.json").read_text(encoding="utf-8")
        )

        self.assertFalse(config["teams"]["enabled"])
        self.assertEqual(config["teams"]["minimum_seconds"], 300)
        self.assertEqual(config["teams"]["summary_max_chars"], 1800)
        self.assertEqual(config["teams"]["summary_tail_chars"], 600)
        self.assertEqual(config["teams"]["webhook_url"], "")
        self.assertEqual(config["voice"]["minimum_seconds"], 65)
        self.assertEqual(config["voice"]["quiet_start"], "23:00")
        self.assertEqual(config["voice"]["quiet_end"], "07:00")

    def test_resolver_prefers_system_python_then_mise(self) -> None:
        valid = Mock(returncode=0)
        runner = Mock(return_value=valid)
        locations = {
            "python3": "/usr/bin/python3",
            "mise": "/usr/bin/mise",
        }

        command = install.resolve_python_command(
            platform_name="posix",
            which=lambda name: locations.get(name),
            runner=runner,
            current_executable="/mise/installs/python/current/bin/python",
        )

        self.assertEqual(command, ["/usr/bin/python3"])

    def test_resolver_uses_mise_when_only_mise_python_exists(self) -> None:
        valid = Mock(returncode=0)
        runner = Mock(return_value=valid)
        locations = {
            "python3": "/home/test/.local/share/mise/installs/python/3/bin/python3",
            "python": "/home/test/.local/share/mise/shims/python",
            "mise": "/usr/bin/mise",
        }

        command = install.resolve_python_command(
            platform_name="posix",
            which=lambda name: locations.get(name),
            runner=runner,
            current_executable=locations["python3"],
        )

        self.assertEqual(command, ["/usr/bin/mise", "exec", "--", "python"])

    def test_install_updates_codex_and_copies_example(self) -> None:
        codex_home = self.root / ".codex"
        codex_home.mkdir()
        (codex_home / "config.toml").write_text(
            'model = "test"\n', encoding="utf-8"
        )
        (codex_home / "hooks.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "python other_hook.py",
                                    },
                                    {
                                        "type": "command",
                                        "command": "mise exec -- python old/notifier.py record-start",
                                    },
                                ]
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        notifier = self.root / "project" / "notifier.py"
        notifier.parent.mkdir()
        notifier.write_text("", encoding="utf-8")
        example = notifier.parent / "config.example.json"
        example.write_text(
            '{"teams":{"enabled":false,"webhook_url":""}}\n',
            encoding="utf-8",
        )
        local_config = self.root / ".config" / "codex-notifier" / "config.json"

        result = install.install(
            codex_home=codex_home,
            local_config_path=local_config,
            python_command=["/usr/bin/python3"],
            notifier_path=notifier,
            example_path=example,
            platform_name="posix",
            timestamp="20260923-120000",
        )

        config_text = (codex_home / "config.toml").read_text(encoding="utf-8")
        hooks = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
        handlers = [
            handler
            for group in hooks["hooks"]["UserPromptSubmit"]
            for handler in group["hooks"]
        ]
        commands = [handler["command"] for handler in handlers]
        self.assertIn('notify = ["/usr/bin/python3"', config_text)
        self.assertIn("python other_hook.py", commands)
        self.assertTrue(any("record-start" in command for command in commands))
        self.assertFalse(any("old/notifier.py" in command for command in commands))
        self.assertEqual(local_config.read_text(encoding="utf-8"), example.read_text(encoding="utf-8"))
        self.assertTrue(result["config_changed"])
        self.assertTrue(result["hooks_changed"])
        self.assertTrue(result["local_config_changed"])

    def test_existing_local_config_is_preserved(self) -> None:
        destination = self.root / "config.json"
        destination.write_text('{"keep":true}\n', encoding="utf-8")
        example = self.root / "example.json"
        example.write_text('{"keep":false}\n', encoding="utf-8")

        changed, backup = install.install_local_config(
            example,
            destination,
            force=False,
            platform_name="posix",
            timestamp="20260923-120000",
        )

        self.assertFalse(changed)
        self.assertIsNone(backup)
        self.assertEqual(destination.read_text(encoding="utf-8"), '{"keep":true}\n')


if __name__ == "__main__":
    unittest.main()
