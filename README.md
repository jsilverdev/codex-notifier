# Codex Notifier

[Versión en español](README.es.md)

Completion notifications for Codex on Windows and Linux. It can send an
Adaptive Card to Microsoft Teams, post an embed to Discord, and play a local
voice alert after a configurable amount of time.

Codex Notifier uses only the Python standard library. Channel settings live in
one JSON file; no duplicated environment-variable configuration is required.

## Default behavior

The installer copies `config.example.json` when no local configuration exists.

| Channel | Enabled | Minimum duration | Notify if duration is unknown |
| --- | --- | ---: | --- |
| Teams | No | 300 seconds | No |
| Discord | No | 300 seconds | No |
| Voice | Yes | 30 seconds | Yes |

Voice alerts are muted from 23:00 to 07:00. They mention the project, duration,
and chat title when Codex provides one. Teams and Discord include the final
assistant message as a bounded summary by default; set `include_summary` to
`false` in either channel to omit assistant-response text completely.

Each channel runs independently: a failed webhook does not prevent voice or the
other webhook from running. The notifier keeps a privacy-conscious daily trace
log and never stores the prompt or final assistant message.

## Requirements

- Python 3.10 or later.
- Codex with an accessible `~/.codex/config.toml`.
- Windows voice: SAPI, normally included with Windows; Piper is optional.
- Linux voice: Piper (recommended), `spd-say`, `espeak-ng`, or `espeak`.

Teams and Discord do not require a local speech engine.

## Install

Run the cross-platform installer once:

```shell
python install.py
```

The installer:

1. Configures Codex `notify` in `~/.codex/config.toml`.
2. Adds or updates the `UserPromptSubmit` hook in `~/.codex/hooks.json` without
   removing unrelated hooks.
3. Copies `config.example.json` to the local configuration path when that file
   does not already exist.

Existing Codex files are backed up before modification. Writes use temporary
files and atomic replacement, so a failed write leaves the destination intact.
An existing notifier configuration is preserved unless `--force-config` is explicitly used. After
installing or changing the hook, open a new chat and use `/hooks` to review and
trust it.

The installer changes only a root-level `notify` setting in `config.toml`;
`notify` keys inside TOML sections and unrelated hooks are preserved. State
directories are private on POSIX and marker/log/config files are written with
owner-only permissions where that platform supports them.

Installer options:

```text
--codex-home PATH          use a different CODEX_HOME
--config-path PATH         use a different notifier config.json
--python-executable PATH   use a specific Python 3 executable
--force-config             replace config.json from the example, with backup
```

`--force-config` replaces local webhook URLs and preferences with the example
defaults.

## Configuration

Default locations:

- Windows: `%LOCALAPPDATA%\CodexNotifier\config.json`
- Linux with `XDG_CONFIG_HOME`: `$XDG_CONFIG_HOME/codex-notifier/config.json`
- Other Linux systems: `~/.config/codex-notifier/config.json`

`config.json` is the source of truth for all channel behavior:

```json
{
  "teams": {
    "enabled": false,
    "minimum_seconds": 300,
    "notify_when_duration_unknown": false,
    "include_summary": true,
    "summary_max_chars": 1800,
    "summary_tail_chars": 600,
    "webhook_url": ""
  },
  "discord": {
    "enabled": false,
    "minimum_seconds": 300,
    "notify_when_duration_unknown": false,
    "include_summary": true,
    "summary_max_chars": 1800,
    "webhook_url": ""
  },
  "voice": {
    "enabled": true,
    "minimum_seconds": 30,
    "notify_when_duration_unknown": true,
    "quiet_start": "23:00",
    "quiet_end": "07:00",
    "language": "auto",
    "volume": null,
    "piper_executable": "",
    "spanish_voice": "",
    "english_voice": ""
  },
  "logging": {
    "enabled": true,
    "retention_days": 7
  }
}
```

`minimum_seconds` is inclusive. `notify_when_duration_unknown` bypasses only
the duration threshold: the channel must still be enabled, webhook channels
still need a valid HTTPS URL, and voice still respects quiet hours.
`include_summary` defaults to `true` for compatibility. With `false`, Teams
omits its summary section and Discord omits the embed description; metadata is
still sent. Teams and Discord continue accepting any HTTPS webhook URL.

The installer does not merge new keys into an existing configuration. When
upgrading, compare your local file with `config.example.json` and add any new
keys manually.

### Teams

Teams uses an Adaptive Card. Its summary preserves the beginning and a
configurable tail when truncation is required; `summary_max_chars` is bounded
between 100 and 6,000 characters. `summary_tail_chars` controls how much of the
end is retained.

### Discord

Discord uses an embed, requests delivery confirmation, disables mentions from
summary text, and caps `summary_max_chars` at the 4,096-character embed
description limit.

### Voice

`language` accepts:

- `auto`: infer Spanish or English from the final assistant message.
- `es`: always speak Spanish.
- `en`: always speak English.

The final assistant message is used only for language detection and is not read
aloud. The spoken alert contains the project, duration, and chat title when
available.

On Linux, `spanish_voice` and `english_voice` contain the ONNX model paths.
`piper_executable` accepts either a command name or a full path. A non-empty
`piper_executable` enables Piper; when it is empty the notifier tries `spd-say`
and then `espeak-ng`/`espeak`. On WSL, the WAV is played through PowerShell so
it uses the Windows audio device directly.

`volume` is optional and accepts values from `0.0` (silent) to `1.0` (full
volume). `null` preserves the default volume. The option applies to Piper on
Linux/WSL and SAPI on Windows.

On Windows, an empty `piper_executable` keeps the existing SAPI behavior:
`spanish_voice` and `english_voice` are optional fragments of installed SAPI
voice names. If `piper_executable` is non-empty, it is resolved through PATH or
as an expanded path to `piper.exe`, and those fields instead name existing
Spanish/English `.onnx` models. Piper failures are reported as errors and do
not silently fall back to SAPI. SAPI remains the zero-configuration default.

Set `quiet_start` and `quiet_end` to the same time to disable quiet hours.

## Path overrides

Two environment variables remain because they select files rather than
duplicate channel settings:

| Variable | Purpose |
| --- | --- |
| `CODEX_NOTIFIER_CONFIG` | Override the `config.json` path |
| `CODEX_NOTIFIER_STATE_DIR` | Override the temporary state and log directory |

The installer also accepts `--config-path`, which is usually clearer for a
one-time installation.

## Trace log

The notifier writes one JSON Lines file per day under its state directory:

- Windows: `%LOCALAPPDATA%\CodexNotifier\logs\notifier-YYYY-MM-DD.jsonl`
- Linux with `XDG_STATE_HOME`:
  `$XDG_STATE_HOME/codex-notifier/logs/notifier-YYYY-MM-DD.jsonl`
- Other Linux systems:
  `~/.local/state/codex-notifier/logs/notifier-YYYY-MM-DD.jsonl`

Entries contain only the timestamp, chat ID, duration, channel statuses, and
bounded error details. Webhook URLs are redacted. Prompts, responses, and
project paths are not logged. `retention_days` is bounded between 1 and 365;
set `logging.enabled` to `false` to disable the log.

## Test voice

```shell
python notifier.py voice-test es
python notifier.py voice-test en
```

Each command selects the matching Piper model when Piper is configured. On
Windows without Piper it tests SAPI; native Linux plays a Piper WAV with
`paplay`, `pw-play`, `aplay`, or `ffplay`.

These commands play audio; automated tests mock speech and webhook calls.

`notify` returns success to Codex even when an individual channel fails; those
failures are logged independently. Invalid manual syntax returns non-zero, and
`voice-test` returns non-zero when the selected voice cannot execute.

## Run tests

```powershell
python -m unittest discover -s tests -v
```

GitHub Actions runs this suite on Ubuntu and Windows with Python 3.10 and
Python 3.13.

## How it works

The `UserPromptSubmit` hook records a start marker without storing the prompt.
Codex later invokes `notify` with an `agent-turn-complete` event. The notifier
matches both events by `turn_id`, calculates and consumes the duration marker,
then evaluates each channel independently. When the marker is missing, each
channel follows its own `notify_when_duration_unknown` setting.
