"""Deterministic read-only coverage manifests for the `점검 리스트` workflow."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .environment import redact_path


MANIFEST_SCHEMA_VERSION = 1
EXCLUDED_DIRECTORY_NAMES = {
    ".agent-hub",  # generated project overlays
    ".bootstrap-venv",  # isolated bootstrap tooling
    ".git",
    ".python",  # uv-managed interpreter
    ".venv",  # project dependencies
    "build",  # setuptools build output
    "dist",  # distributable build output
    "__pycache__",
    "node_modules",
    "runtime",
    "secrets",
    "generated",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo"}
MAX_REVIEWABLE_BYTES = 5 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(path: Path, root: Path) -> tuple[str, str]:
    relative = path.relative_to(root)
    if path.is_symlink():
        return "excluded", "symlink_not_followed"
    if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
        return "excluded", "compiled_python_artifact"
    if not path.is_file():
        return "excluded", "not_regular_file"
    if path.stat().st_size > MAX_REVIEWABLE_BYTES:
        return "excluded", "file_exceeds_review_size_limit"
    try:
        with path.open("rb") as stream:
            if b"\0" in stream.read(8192):
                return "excluded", "binary_file"
    except OSError as error:
        return "deferred", f"unreadable:{type(error).__name__}"
    return "included", "review_candidate"


def build_manifest(project_root: Path) -> dict[str, Any]:
    """Hash every discoverable file once and state why it is or is not reviewable."""

    root = project_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Project root must be a directory: {root}")
    files: list[dict[str, Any]] = []
    walk_errors: list[str] = []

    def record_walk_error(error: OSError) -> None:
        walk_errors.append(str(error))

    for current_root, directory_names, file_names in os.walk(root, followlinks=False, onerror=record_walk_error):
        current = Path(current_root)
        directory_names.sort()
        file_names.sort()
        for directory_name in list(directory_names):
            candidate = current / directory_name
            relative = candidate.relative_to(root)
            if candidate.is_symlink():
                files.append(
                    {
                        "path": str(relative) + "/",
                        "kind": "directory",
                        "disposition": "excluded",
                        "reason": "symlink_not_followed",
                        "review_state": "excluded",
                    }
                )
                directory_names.remove(directory_name)
            elif directory_name in EXCLUDED_DIRECTORY_NAMES:
                files.append(
                    {
                        "path": str(relative) + "/",
                        "kind": "directory",
                        "disposition": "excluded",
                        "reason": "generated_or_private_directory",
                        "review_state": "excluded",
                    }
                )
                directory_names.remove(directory_name)
            elif directory_name.endswith(".egg-info"):
                files.append(
                    {
                        "path": str(relative) + "/",
                        "kind": "directory",
                        "disposition": "excluded",
                        "reason": "generated_build_metadata",
                        "review_state": "excluded",
                    }
                )
                directory_names.remove(directory_name)
        for file_name in file_names:
            candidate = current / file_name
            disposition, reason = classify(candidate, root)
            item: dict[str, Any] = {
                "path": str(candidate.relative_to(root)),
                "disposition": disposition,
                "reason": reason,
                "review_state": "pending" if disposition == "included" else "excluded",
            }
            if disposition == "included":
                try:
                    item["bytes"] = candidate.stat().st_size
                    item["sha256"] = sha256(candidate)
                except OSError as error:
                    item["disposition"] = "deferred"
                    item["reason"] = f"unreadable:{type(error).__name__}"
                    item["review_state"] = "deferred"
            files.append(item)
    for error in sorted(walk_errors):
        files.append(
            {
                "path": "<walk-error>",
                "kind": "directory",
                "disposition": "deferred",
                "reason": f"walk_error:{error}",
                "review_state": "deferred",
            }
        )
    counts = {
        disposition: sum(1 for item in files if item["disposition"] == disposition)
        for disposition in ("included", "excluded", "deferred")
    }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": redact_path(root),
        "read_only": True,
        "files": files,
        "counts": counts,
    }


def write_new_manifest(manifest: dict[str, Any], destination: Path) -> None:
    """Persist a manifest only when the caller selected a new output path."""

    output = destination.expanduser().resolve(strict=False)
    if not output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {output.parent}")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
