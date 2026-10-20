"""Read-only environment inspection for the Agent Hub bootstrap phase."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 1
DEFAULT_COMMANDS = (
    "codex",
    "claude",
    "agy",
    "python3",
    "git",
    "node",
    "sqlite3",
    "docker",
    "uv",
    "pipx",
)


@dataclass(frozen=True)
class CommandStatus:
    """Availability only; commands are never executed by this inspector."""

    name: str
    path: str | None
    status: str


@dataclass(frozen=True)
class PathStatus:
    """Writable-path result without creating or modifying the inspected path."""

    path: str
    exists: bool
    is_directory: bool
    writable: bool
    status: str


def inspect_command(name: str) -> CommandStatus:
    path = shutil.which(name)
    return CommandStatus(name=name, path=path, status="available" if path else "unknown")


def inspect_path(path: Path) -> PathStatus:
    expanded = path.expanduser().resolve(strict=False)
    exists = expanded.exists()
    is_directory = expanded.is_dir()
    parent = expanded if is_directory else expanded.parent
    writable = parent.exists() and os.access(parent, os.W_OK)
    if not exists:
        status = "missing"
    elif not is_directory:
        status = "not_directory"
    elif not writable:
        status = "unwritable"
    else:
        status = "available"
    return PathStatus(
        path=redact_path(expanded),
        exists=exists,
        is_directory=is_directory,
        writable=writable,
        status=status,
    )


def redact_path(path: Path) -> str:
    """Return a report-safe path that does not expose the local home name."""

    home = Path.home().resolve()
    try:
        return "~" if path == home else str(Path("~") / path.relative_to(home))
    except ValueError:
        return str(path)


def build_report(hub_root: Path, commands: Iterable[str] = DEFAULT_COMMANDS) -> dict[str, object]:
    """Return a serialisable report without changing the local environment."""

    root = hub_root.expanduser().resolve(strict=False)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "hub_root": asdict(inspect_path(root)),
        "commands": [asdict(inspect_command(name)) for name in commands],
    }
