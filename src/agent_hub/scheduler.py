"""Owner-managed cron scheduling for the bounded improvement observer."""

from __future__ import annotations

import shlex
import os
import subprocess
from pathlib import Path
from typing import Callable


MARKER = "# agent-hub-improvement-observer"
DISCOVERY_MARKER = "# agent-hub-improvement-discovery"


class ObserverScheduler:
    """Manage one tagged user-crontab entry without touching other entries."""

    def __init__(self, hub_root: Path, executable: Path, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        if os.name == "nt" and runner is subprocess.run:
            raise RuntimeError("The improvement scheduler requires a POSIX crontab; run it from WSL/Linux or use run-once.")
        self.hub_root = hub_root.expanduser().resolve(strict=True)
        self.executable = executable.expanduser().resolve(strict=True)
        if not self.executable.is_file():
            raise ValueError("Agent Hub executable does not exist.")
        # shlex.quote does not protect crontab syntax: a newline starts a new
        # (untagged, never-disabled) cron entry and an unescaped "%" ends the
        # command and feeds the remainder to stdin.
        for path in (self.hub_root, self.executable):
            if any(character in str(path) for character in "%\n\r"):
                raise ValueError("Hub root and executable paths must not contain '%' or line breaks for crontab scheduling.")
        self.runner = runner

    def status(self) -> bool:
        return MARKER in self._read()

    def status_details(self) -> dict[str, bool]:
        current = self._read()
        return {"promotion": MARKER in current, "discovery": DISCOVERY_MARKER in current}

    def enable(self, every_minutes: int = 10) -> bool:
        if not 5 <= every_minutes <= 1440:
            raise ValueError("Observer interval must be between 5 and 1440 minutes.")
        current = self._read()
        lines = [line for line in current.splitlines() if MARKER not in line and DISCOVERY_MARKER not in line]
        command = " ".join(
            (
                f"*/{every_minutes}", "*", "*", "*", "*",
                "cd", shlex.quote(str(self.hub_root)), "&&", shlex.quote(str(self.executable)),
                "improvement", "--hub-root", shlex.quote(str(self.hub_root)), "run-once",
                ">>", shlex.quote(str(self.hub_root / "runtime" / "state" / "improvement-observer.log")), "2>&1", MARKER,
            )
        )
        lines.append(command)
        discovery = " ".join((
            "17", "3", "*", "*", "*", "cd", shlex.quote(str(self.hub_root)), "&&", shlex.quote(str(self.executable)),
            "improvement", "--hub-root", shlex.quote(str(self.hub_root)), "auto-once",
            ">>", shlex.quote(str(self.hub_root / "runtime" / "state" / "improvement-discovery.log")), "2>&1", DISCOVERY_MARKER,
        ))
        lines.append(discovery)
        self._write("\n".join(lines).strip() + "\n")
        return True

    def disable(self) -> bool:
        current = self._read()
        lines = [line for line in current.splitlines() if MARKER not in line and DISCOVERY_MARKER not in line]
        if len(lines) == len(current.splitlines()):
            return False
        payload = "\n".join(lines).strip()
        self._write(payload + "\n" if payload else "")
        return True

    def _read(self) -> str:
        completed = self.runner(["crontab", "-l"], capture_output=True, text=True, check=False)
        if completed.returncode == 0:
            return completed.stdout
        if completed.returncode == 1 and "no crontab" in completed.stderr.lower():
            return ""
        raise RuntimeError(f"Could not read user crontab: {completed.stderr.strip()}")

    def _write(self, payload: str) -> None:
        completed = self.runner(["crontab", "-"], input=payload, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"Could not update user crontab: {completed.stderr.strip()}")
