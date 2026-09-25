"""SAPI, Piper, and platform audio playback."""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..state import text

SPEECH_TIMEOUT_SECONDS = 30
SPANISH_WORDS = {"al", "cambios", "completado", "con", "corregido", "de", "el", "en", "esta", "este", "fue", "la", "las", "listo", "los", "para", "por", "pruebas", "que", "se", "sin", "una", "y", "ya"}
ENGLISH_WORDS = {"a", "and", "changes", "completed", "done", "for", "from", "has", "fixed", "in", "is", "of", "on", "that", "the", "tests", "this", "to", "was", "ready", "with", "without"}


def detect_language(value: str, configured: str = "auto") -> str:
    requested = configured.strip().lower()
    if requested in {"es", "spanish", "español"}:
        return "es"
    if requested in {"en", "english", "inglés", "ingles"}:
        return "en"
    lowered = value.lower()
    words = re.findall(r"[a-záéíóúüñ]+", lowered)
    spanish_score = sum(word in SPANISH_WORDS for word in words)
    english_score = sum(word in ENGLISH_WORDS for word in words)
    if re.search(r"[áéíóúüñ¿¡]", lowered):
        spanish_score += 2
    return "en" if english_score > spanish_score else "es"


def configured_volume(voice_config: dict[str, Any]) -> float | None:
    value = voice_config.get("volume")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return min(1.0, max(0.0, float(value)))


def configured_voice(voice_config: dict[str, Any], language: str) -> str:
    return text(voice_config.get("spanish_voice" if language == "es" else "english_voice"))


def resolve_executable(configured: str, default: str) -> str | None:
    requested = configured or default
    expanded = Path(requested).expanduser()
    if expanded.is_absolute() or expanded.parent != Path("."):
        return str(expanded) if expanded.is_file() else None
    return shutil.which(requested)


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def speak_sapi_windows(message: str, *, language: str, preferred_voice: str = "",
                       volume: float | None = None) -> None:
    if os.name != "nt":
        raise RuntimeError("La voz local solo está disponible en Windows.")
    environment = os.environ.copy()
    environment["CODEX_NOTIFIER_SPEECH_B64"] = base64.b64encode(message.encode("utf-8")).decode("ascii")
    environment["CODEX_NOTIFIER_SPEECH_LANGUAGE"] = language
    environment["CODEX_NOTIFIER_SPEECH_VOICE"] = preferred_voice
    environment["CODEX_NOTIFIER_SPEECH_VOLUME"] = "" if volume is None else str(round(min(1.0, max(0.0, volume)) * 100))
    script = (
        "$bytes=[Convert]::FromBase64String($env:CODEX_NOTIFIER_SPEECH_B64);"
        "$text=[Text.Encoding]::UTF8.GetString($bytes);$voice=New-Object -ComObject SAPI.SpVoice;"
        "$wantedName=$env:CODEX_NOTIFIER_SPEECH_VOICE;$wantedLanguage=$env:CODEX_NOTIFIER_SPEECH_LANGUAGE;"
        "$wantedVolume=$env:CODEX_NOTIFIER_SPEECH_VOLUME;$selected=$null;"
        "foreach($candidate in $voice.GetVoices()){$description=$candidate.GetDescription();"
        "if($wantedName -and $description -like ('*'+$wantedName+'*')){$selected=$candidate;break}"
        "if(-not $selected){try{$lcid=[Convert]::ToInt32($candidate.GetAttribute('Language'),16);"
        "$culture=[Globalization.CultureInfo]::GetCultureInfo($lcid);"
        "if($culture.TwoLetterISOLanguageName -eq $wantedLanguage){$selected=$candidate}}catch{}}};"
        "if($selected){$voice.Voice=$selected};if($wantedVolume -ne ''){$voice.Volume=[int]$wantedVolume};"
        "[void]$voice.Speak($text)"
    )
    subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                   env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, creationflags=_creation_flags(), check=True,
                   timeout=SPEECH_TIMEOUT_SECONDS)


def play_wav_windows(wav_path: Path, *, powershell: str | None = None) -> None:
    executable = powershell or shutil.which("powershell.exe") or "powershell.exe"
    encoded_path = base64.b64encode(str(wav_path).encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference='Stop';$bytes=[Convert]::FromBase64String($env:CODEX_NOTIFIER_WAV_B64);"
        "$path=[Text.Encoding]::UTF8.GetString($bytes);$player=New-Object System.Media.SoundPlayer $path;"
        "$player.Load();$player.PlaySync()"
    )
    environment = os.environ.copy()
    environment["CODEX_NOTIFIER_WAV_B64"] = encoded_path
    subprocess.run([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                   env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, creationflags=_creation_flags(), check=True,
                   timeout=SPEECH_TIMEOUT_SECONDS)


def _play_wav_wsl(wav_path: Path, powershell: str, wslpath: str) -> None:
    result = subprocess.run([wslpath, "-w", str(wav_path)], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, check=True, timeout=5)
    windows_path = result.stdout.strip()
    encoded_path = base64.b64encode(windows_path.encode("utf-8")).decode("ascii")
    script = ("$ErrorActionPreference='Stop';$bytes=[Convert]::FromBase64String('" + encoded_path + "');"
              "$path=[Text.Encoding]::UTF8.GetString($bytes);$player=New-Object System.Media.SoundPlayer $path;"
              "$player.Load();$player.PlaySync()")
    subprocess.run([powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=_creation_flags(), check=True, timeout=SPEECH_TIMEOUT_SECONDS)


def generate_piper_wav(message: str, *, language: str = "es", voice_config: dict[str, Any],
                       temporary_dir: str | Path) -> Path:
    model_value = configured_voice(voice_config, language)
    key = "spanish_voice" if language == "es" else "english_voice"
    if not model_value:
        raise RuntimeError(f"Configura voice.{key} para usar Piper.")
    model = Path(model_value).expanduser()
    if not model.is_file():
        raise RuntimeError(f"No se encontró el modelo de Piper: {model}")
    configured = text(voice_config.get("piper_executable"))
    piper = resolve_executable(configured, "piper")
    if not piper:
        raise RuntimeError(f"No se encontró el ejecutable de Piper configurado: {configured or 'piper'}")
    wav_path = Path(temporary_dir) / "speech.wav"
    command = [piper, "-m", str(model), "-f", str(wav_path)]
    volume = configured_volume(voice_config)
    if volume is not None:
        command.extend(["--volume", str(volume)])
    command.extend(["--", message])
    try:
        subprocess.run(command, cwd=str(temporary_dir), stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=SPEECH_TIMEOUT_SECONDS,
                       creationflags=_creation_flags())
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Piper no pudo generar el audio: {exc}") from exc
    return wav_path


def speak_piper(message: str, *, language: str = "es", voice_config: dict[str, Any],
                windows: bool | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="codex-notifier-") as temporary_dir:
        wav_path = generate_piper_wav(message, language=language, voice_config=voice_config,
                                       temporary_dir=temporary_dir)
        use_windows = os.name == "nt" if windows is None else windows
        if use_windows:
            play_wav_windows(wav_path)
            return
        play_wav_linux(wav_path)


def play_wav_linux(wav_path: Path) -> None:
    powershell = shutil.which("powershell.exe")
    wslpath = shutil.which("wslpath")
    if powershell and wslpath:
        _play_wav_wsl(wav_path, powershell, wslpath)
        return
    player = next(((name, shutil.which(name)) for name in ("paplay", "pw-play", "aplay", "ffplay") if shutil.which(name)), None)
    if not player:
        raise RuntimeError("No se encontró un reproductor compatible: instala paplay, pw-play, aplay o ffplay. En WSL también se admite powershell.exe.")
    name, executable = player
    command = [executable, "-nodisp", "-autoexit", str(wav_path)] if name == "ffplay" else [executable, str(wav_path)]
    subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=SPEECH_TIMEOUT_SECONDS,
                   creationflags=_creation_flags())


def speak_linux(message: str, *, language: str = "es", voice_config: dict[str, Any] | None = None) -> None:
    settings = {} if voice_config is None else voice_config
    if text(settings.get("piper_executable")):
        speak_piper(message, language=language, voice_config=settings, windows=False)
        return
    dispatcher = shutil.which("spd-say")
    if dispatcher:
        command = [dispatcher, "-w", "-l", language, message]
    else:
        espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        if not espeak:
            raise RuntimeError("Instala speech-dispatcher (spd-say) o espeak-ng para usar voz en Linux.")
        command = [espeak, "-v", language, message]
    subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=SPEECH_TIMEOUT_SECONDS,
                   creationflags=_creation_flags())


def speak(message: str, *, language: str = "es", preferred_voice: str = "",
          voice_config: dict[str, Any] | None = None) -> None:
    settings = {} if voice_config is None else voice_config
    if os.name == "nt":
        if text(settings.get("piper_executable")):
            speak_piper(message, language=language, voice_config=settings, windows=True)
        else:
            speak_sapi_windows(message, language=language, preferred_voice=preferred_voice,
                               volume=configured_volume(settings))
        return
    if os.name == "posix":
        speak_linux(message, language=language, voice_config=settings)
        return
    raise RuntimeError(f"La voz local no es compatible con la plataforma {os.name}.")


speak_windows = speak_sapi_windows
