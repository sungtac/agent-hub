"""Run the non-destructive Agent Hub portability acceptance checks.

This script is intentionally standard-library only so it can execute in a
fresh virtual environment on Linux, Windows, or macOS after project extras are
installed.  It does not call a provider CLI or change Hub runtime settings.
"""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*arguments: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": [sys.executable, *arguments],
        "return_code": completed.returncode,
        "stdout": completed.stdout[-4_000:],
        "stderr": completed.stderr[-4_000:],
    }


def dependency_check() -> dict[str, object]:
    """Use the package manager available in the current virtual environment.

    ``uv`` intentionally creates virtual environments without the ``pip``
    module by default.  Treating that valid layout as a dependency failure
    makes the portability check report a false negative.
    """

    if importlib.util.find_spec("pip") is not None:
        return run("-m", "pip", "check")
    uv = shutil.which("uv")
    if uv is not None:
        completed = subprocess.run(
            [uv, "pip", "check", "--python", sys.executable],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "command": [uv, "pip", "check", "--python", sys.executable],
            "return_code": completed.returncode,
            "stdout": completed.stdout[-4_000:],
            "stderr": completed.stderr[-4_000:],
        }
    return {
        "command": [],
        "return_code": 0,
        "stdout": "",
        "stderr": "Dependency resolver check skipped: neither pip nor uv is available.",
        "skipped": True,
    }


def main() -> int:
    checks = [
        run("-m", "compileall", "-q", "src", "tests"),
        run("-m", "unittest", "discover", "-s", "tests", "-q"),
        dependency_check(),
    ]
    report = {
        "platform": platform.platform(),
        "python": sys.version,
        "root": str(ROOT),
        "checks": checks,
        "passed": all(item["return_code"] == 0 for item in checks),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
