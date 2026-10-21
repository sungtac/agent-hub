"""Non-destructive local snapshot and verification for Hub recovery."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


def create_backup(hub_root: Path, destination: Path) -> Path:
    root = hub_root.expanduser().resolve(strict=True)
    output = destination.expanduser().resolve(strict=False)
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Backup destination must be a new file in an existing directory.")
    members = [path for path in (root / "config", root / "runtime" / "tasks", root / "runtime" / "knowledge", root / "runtime" / "state") if path.exists()]
    # An archive written inside a snapshotted directory would capture itself
    # half-written and could never verify.
    if any(output.parent == member.resolve() or member.resolve() in output.parents for member in members):
        raise ValueError("Backup destination must not be inside a directory that is being backed up.")
    manifest: dict[str, object] = {"created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
    with tarfile.open(output, "x:gz") as archive:
        for member in members:
            for file_path in sorted(member.rglob("*")):
                if file_path.is_file() and not file_path.is_symlink():
                    relative = file_path.relative_to(root).as_posix()
                    manifest["files"][relative] = hashlib.sha256(file_path.read_bytes()).hexdigest()
                    archive.add(file_path, arcname=relative, recursive=False)
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(encoded)
        archive.addfile(info, io.BytesIO(encoded))
    return output


def verify_backup(archive_path: Path) -> bool:
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            by_name = {member.name: member for member in members}
            if len(by_name) != len(members) or "manifest.json" not in by_name:
                return False
            manifest_member = by_name["manifest.json"]
            if not manifest_member.isfile():
                return False
            manifest_stream = archive.extractfile(manifest_member)
            if manifest_stream is None:
                return False
            manifest = json.loads(manifest_stream.read().decode("utf-8"))
            if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
                return False
            if set(manifest["files"]) != set(by_name) - {"manifest.json"}:
                return False
            for name, expected in manifest["files"].items():
                if not _safe_archive_path(name) or not by_name[name].isfile():
                    return False
                if not isinstance(expected, str) or len(expected) != 64:
                    return False
                stream = archive.extractfile(by_name[name])
                if stream is None or hashlib.sha256(stream.read()).hexdigest() != expected:
                    return False
        return True
    except (OSError, tarfile.TarError, ValueError, TypeError, AttributeError, UnicodeDecodeError):
        return False


def restore_backup(archive_path: Path, hub_root: Path, apply: bool = False) -> dict[str, Any]:
    """Preview or overlay a verified snapshot; applying first snapshots current state."""
    archive_file = archive_path.expanduser().resolve(strict=True)
    root = hub_root.expanduser().resolve(strict=True)
    if not root.is_dir() or not verify_backup(archive_file):
        raise ValueError("Backup is invalid or does not match its manifest.")
    with tarfile.open(archive_file, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers() if member.name != "manifest.json"}
        targets = sorted(members)
        payloads: dict[str, bytes] = {}
        for name, member in members.items():
            if not _safe_archive_path(name) or not member.isfile():
                raise ValueError(f"Unsafe backup member: {name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"Unreadable backup member: {name}")
            payloads[name] = stream.read()
    if not apply:
        return {"applied": False, "files": targets, "safety_backup": None}

    backups_directory = _safe_backup_directory(root)
    backups_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    safety_path = backups_directory / f"pre-restore-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.tar.gz"
    create_backup(root, safety_path)
    target_paths = {name: _safe_target(root, name) for name in targets}
    previous = {name: path.read_bytes() if path.is_file() else None for name, path in target_paths.items()}
    try:
        for name, payload in payloads.items():
            target = target_paths[name]
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _atomic_write(target, payload)
    except Exception:
        for name, payload in previous.items():
            if payload is None:
                target = root / name
                if target.exists() and target.is_file() and not target.is_symlink():
                    target.unlink()
            else:
                _atomic_write(target_paths[name], payload)
        raise
    return {"applied": True, "files": targets, "safety_backup": str(safety_path)}


def _safe_archive_path(name: str) -> bool:
    path = PurePosixPath(name)
    allowed = len(path.parts) >= 2 and (path.parts[0] == "config" or (path.parts[0] == "runtime" and path.parts[1] in {"tasks", "knowledge", "state"}))
    # ":" would let a member such as "config/D:/x" join to another drive on
    # Windows (Path("C:/hub").joinpath("config", "D:", "x") == "D:x").
    return bool(allowed and not path.is_absolute() and ".." not in path.parts and "\\" not in name and ":" not in name)


def _safe_target(root: Path, name: str) -> Path:
    if not _safe_archive_path(name):
        raise ValueError(f"Unsafe restore target: {name}")
    target = root.joinpath(*PurePosixPath(name).parts)
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise ValueError(f"Restore target traverses a symbolic link: {name}")
    return target


def _safe_backup_directory(root: Path) -> Path:
    directory = root / "runtime" / "backups"
    if any(part.is_symlink() for part in (directory, *directory.parents)):
        raise ValueError("Backup directory must not traverse a symbolic link.")
    return directory


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
