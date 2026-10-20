"""Verify that commands unrelated to optional integrations work in a base install."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def run(arguments: list[str]) -> None:
    completed = subprocess.run([sys.executable, "-m", "agent_hub.cli", *arguments], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"base-install command failed: {arguments}\n{completed.stderr}")


with tempfile.TemporaryDirectory(prefix="agent-hub-base-") as temporary_directory:
    root = Path(temporary_directory)
    run(["environment", "check", "--hub-root", str(root)])
    run(["init", "--hub-root", str(root)])
    run(["subscription-chat", "--hub-root", str(root), "status"])
