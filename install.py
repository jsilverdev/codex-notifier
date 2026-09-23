"""Install Codex Notifier on Windows or Linux using only Python's stdlib."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
NOTIFIER_PATH = PROJECT_ROOT / "notifier.py"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.json"


def default_codex_home(
    environment: dict[str, str] | None = None, home: Path | None = None
) -> Path:
    env = os.environ if environment is None else environment
    configured = env.get("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return (Path.home() if home is None else home) / ".codex"


def default_local_config_path(
    *,
    platform_name: str | None = None,
    environment: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    platform = os.name if platform_name is None else platform_name
    env = os.environ if environment is None else environment
    configured = env.get("CODEX_NOTIFIER_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser()
    user_home = Path.home() if home is None else home
    if platform == "nt":
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data) / "CodexNotifier" / "config.json"
        return user_home / "AppData" / "Local" / "CodexNotifier" / "config.json"
    xdg_config_home = env.get("XDG_CONFIG_HOME", "").strip()
    config_home = Path(xdg_config_home) if xdg_config_home else user_home / ".config"
    return config_home / "codex-notifier" / "config.json"


def _is_python3(
    command: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    try:
        result = runner(
            [
                *command,
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _is_mise_managed_path(executable: str) -> bool:
    normalized = executable.replace("\\", "/").lower()
    return "/mise/shims/" in normalized or "/mise/installs/" in normalized


def resolve_python_command(
    *,
    platform_name: str | None = None,
    explicit_executable: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    current_executable: str | None = None,
) -> list[str]:
    """Find a stable Python 3 command, preferring non-mise installations."""
    platform = os.name if platform_name is None else platform_name
    if explicit_executable:
        candidate = [str(Path(explicit_executable).expanduser())]
        if _is_python3(candidate, runner=runner):
            return candidate
        raise RuntimeError(f"No es un Python 3 válido: {explicit_executable}")

    names: list[tuple[str, list[str]]] = [("python3", [])]
    if platform == "nt":
        names.append(("py", ["-3"]))
    names.append(("python", []))
    for name, arguments in names:
        executable = which(name)
        if executable and not _is_mise_managed_path(executable):
            candidate = [executable, *arguments]
            if _is_python3(candidate, runner=runner):
                return candidate

    mise = which("mise")
    if mise:
        candidate = [mise, "exec", "--", "python"]
        if _is_python3(candidate, runner=runner):
            return candidate

    active_python = current_executable or sys.executable
    if active_python and _is_python3([active_python], runner=runner):
        return [active_python]

    raise RuntimeError(
        "No se encontró Python 3. Instálalo o ejecuta este archivo con "
        "'mise exec -- python install.py'."
    )


def _command_text(arguments: Sequence[str], platform_name: str) -> str:
    if platform_name == "nt":
        return subprocess.list2cmdline(list(arguments))
    return shlex.join(arguments)


def _backup(path: Path, timestamp: str) -> Path:
    backup = path.with_name(f"{path.name}.{timestamp}.bak")
    shutil.copy2(path, backup)
    return backup


def update_codex_config(
    config_path: Path,
    python_command: Sequence[str],
    notifier_path: Path,
    *,
    timestamp: str,
) -> tuple[bool, Path | None]:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    notify_arguments = [*python_command, str(notifier_path), "notify"]
    notify_line = "notify = " + json.dumps(notify_arguments, ensure_ascii=False)
    pattern = re.compile(r"^[ \t]*notify[ \t]*=.*$", re.MULTILINE)
    if pattern.search(existing):
        updated = pattern.sub(lambda _: notify_line, existing, count=1)
    else:
        updated = notify_line + os.linesep + existing
    if updated == existing:
        return False, None
    backup = _backup(config_path, timestamp) if config_path.exists() else None
    config_path.write_text(updated, encoding="utf-8", newline="")
    return True, backup


def _is_notifier_handler(handler: object) -> bool:
    if not isinstance(handler, dict):
        return False
    commands = [handler.get("command"), handler.get("commandWindows")]
    return any(
        isinstance(command, str)
        and "notifier.py" in command
        and "record-start" in command
        for command in commands
    )


def update_hooks(
    hooks_path: Path,
    python_command: Sequence[str],
    notifier_path: Path,
    *,
    platform_name: str,
    timestamp: str,
) -> tuple[bool, Path | None]:
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    if hooks_path.exists():
        document = json.loads(hooks_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise TypeError(f"{hooks_path} debe contener un objeto JSON")
    else:
        document = {
            "description": "Local lifecycle hooks for Codex.",
            "hooks": {},
        }

    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise TypeError(f"El campo hooks de {hooks_path} debe ser un objeto")
    groups = hooks.setdefault("UserPromptSubmit", [])
    if not isinstance(groups, list):
        raise TypeError("hooks.UserPromptSubmit debe ser una lista")

    preserved_groups: list[object] = []
    for group in groups:
        if not isinstance(group, dict):
            preserved_groups.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            preserved_groups.append(group)
            continue
        kept_handlers = [handler for handler in handlers if not _is_notifier_handler(handler)]
        if kept_handlers:
            copied_group = dict(group)
            copied_group["hooks"] = kept_handlers
            preserved_groups.append(copied_group)

    hook_arguments = [*python_command, str(notifier_path), "record-start"]
    handler = {
        "type": "command",
        "command": _command_text(hook_arguments, platform_name),
        "timeout": 5,
    }
    preserved_groups.append({"hooks": [handler]})
    hooks["UserPromptSubmit"] = preserved_groups

    updated = json.dumps(document, ensure_ascii=False, indent=2) + os.linesep
    existing = hooks_path.read_text(encoding="utf-8") if hooks_path.exists() else ""
    if updated == existing:
        return False, None
    backup = _backup(hooks_path, timestamp) if hooks_path.exists() else None
    hooks_path.write_text(updated, encoding="utf-8", newline="")
    return True, backup


def install_local_config(
    example_path: Path,
    destination: Path,
    *,
    force: bool,
    platform_name: str,
    timestamp: str,
) -> tuple[bool, Path | None]:
    if destination.exists() and not force:
        return False, None
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(destination, timestamp) if destination.exists() else None
    shutil.copyfile(example_path, destination)
    if platform_name != "nt":
        destination.chmod(0o600)
    return True, backup


def install(
    *,
    codex_home: Path,
    local_config_path: Path,
    python_command: Sequence[str],
    notifier_path: Path = NOTIFIER_PATH,
    example_path: Path = EXAMPLE_CONFIG_PATH,
    platform_name: str | None = None,
    force_config: bool = False,
    timestamp: str | None = None,
) -> dict[str, object]:
    platform = os.name if platform_name is None else platform_name
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    config_changed, config_backup = update_codex_config(
        codex_home / "config.toml",
        python_command,
        notifier_path,
        timestamp=stamp,
    )
    hooks_changed, hooks_backup = update_hooks(
        codex_home / "hooks.json",
        python_command,
        notifier_path,
        platform_name=platform,
        timestamp=stamp,
    )
    local_changed, local_backup = install_local_config(
        example_path,
        local_config_path,
        force=force_config,
        platform_name=platform,
        timestamp=stamp,
    )
    return {
        "config_changed": config_changed,
        "config_backup": config_backup,
        "hooks_changed": hooks_changed,
        "hooks_backup": hooks_backup,
        "local_config_changed": local_changed,
        "local_config_backup": local_backup,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Instala Codex Notifier y crea su configuración local."
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Ruta de CODEX_HOME (predeterminado: ~/.codex).",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=default_local_config_path(),
        help="Ruta del config.json local del notificador.",
    )
    parser.add_argument(
        "--python-executable",
        help="Ejecutable de Python 3 que usarán notify y el hook.",
    )
    parser.add_argument(
        "--force-config",
        action="store_true",
        help="Reemplaza config.json con config.example.json (crea backup).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not NOTIFIER_PATH.is_file() or not EXAMPLE_CONFIG_PATH.is_file():
        print("Faltan notifier.py o config.example.json.", file=sys.stderr)
        return 1
    try:
        python_command = resolve_python_command(
            explicit_executable=args.python_executable
        )
        result = install(
            codex_home=args.codex_home.expanduser().resolve(),
            local_config_path=args.config_path.expanduser().resolve(),
            python_command=python_command,
            force_config=args.force_config,
        )
    except (OSError, ValueError, TypeError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"No se pudo instalar: {exc}", file=sys.stderr)
        return 1

    print("Python:", _command_text(python_command, os.name))
    print("Codex config:", args.codex_home.expanduser() / "config.toml")
    print("Codex hooks:", args.codex_home.expanduser() / "hooks.json")
    print("Notifier config:", args.config_path.expanduser())
    if not result["local_config_changed"]:
        print("Se conservó el config.json existente.")
    if result["hooks_changed"]:
        print("Abre un chat nuevo y usa /hooks para revisar y confiar en el hook.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
