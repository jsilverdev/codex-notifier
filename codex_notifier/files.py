"""Small filesystem helpers used by installation and notifier state."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def secure_directory(path: Path, *, platform_name: str | None = None) -> Path:
    """Create a private POSIX directory; Windows ACL behavior is unchanged."""
    path.mkdir(parents=True, exist_ok=True)
    platform = os.name if platform_name is None else platform_name
    if platform != "nt":
        path.chmod(0o700)
    return path


def secure_file(path: Path, mode: int = 0o600, *, platform_name: str | None = None) -> None:
    platform = os.name if platform_name is None else platform_name
    if platform != "nt":
        path.chmod(mode)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    mode: int | None = None,
    platform_name: str | None = None,
) -> None:
    """Write in the destination directory and replace it only after close."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        if mode is not None:
            secure_file(temporary, mode, platform_name=platform_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            secure_file(temporary, mode, platform_name=platform_name)
        os.replace(temporary, path)
        if mode is not None:
            secure_file(path, mode, platform_name=platform_name)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_text(
    path: Path,
    text: str,
    *,
    mode: int | None = None,
    platform_name: str | None = None,
) -> None:
    atomic_write_bytes(
        path,
        text.encode("utf-8"),
        mode=mode,
        platform_name=platform_name,
    )


def atomic_copy(
    source: Path,
    destination: Path,
    *,
    mode: int | None = None,
    platform_name: str | None = None,
) -> None:
    with source.open("rb") as stream:
        atomic_write_bytes(
            destination,
            stream.read(),
            mode=mode,
            platform_name=platform_name,
        )
