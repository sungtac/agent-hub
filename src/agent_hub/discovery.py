"""Read-only finding and draft generation for bounded self-improvement."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Protocol

from .providers import ExecutionResult
from .file_lock import exclusive_lock


class ReadOnlyAdapter(Protocol):
    provider: str
    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult: ...


def source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for directory in (root / "src", root / "tests"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


class DiscoveryStore:
    """Private, atomic state; findings and drafts never edit project source."""
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def write(self, value: dict[str, Any]) -> None:
        with exclusive_lock(self.lock_path):
            self._write(value)

    def _write(self, value: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush(); os.fsync(stream.fileno()); temporary = Path(stream.name)
            os.replace(temporary, self.path)
            if os.name != "nt": self.path.chmod(0o600)
        finally:
            if temporary is not None and temporary.exists(): temporary.unlink()

    def read(self) -> dict[str, Any]:
        with exclusive_lock(self.lock_path):
            return self._read()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists(): return {"schema_version": 1, "findings": {}, "drafts": {}}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1: raise ValueError("Unsupported improvement discovery state.")
        return value

    @contextmanager
    def locked(self):
        """Serialize a complete read/model/write discovery cycle."""
        with exclusive_lock(self.lock_path):
            yield self


def discover(adapter: ReadOnlyAdapter, root: Path, timeout_seconds: float = 120.0) -> tuple[ExecutionResult, dict[str, Any] | None]:
    fingerprint = source_fingerprint(root)
    prompt = ("Read-only code inspection only; do not edit files. Return ONLY JSON "
              '{"summary":"...","evidence":"path:line and reproducible reason","area":"quality|tests|documentation"}. '
              f"Source fingerprint: {fingerprint}")
    result = adapter.execute(prompt, root, timeout_seconds)
    if result.status != "succeeded" or not result.final_message: return result, None
    try: finding = json.loads(result.final_message)
    except json.JSONDecodeError: return result, None
    if not all(isinstance(finding.get(key), str) and finding[key].strip() for key in ("summary", "evidence", "area")): return result, None
    finding["fingerprint"] = fingerprint
    finding["finding_id"] = hashlib.sha256(json.dumps(finding, sort_keys=True).encode()).hexdigest()[:20]
    return result, finding


def draft(adapter: ReadOnlyAdapter, root: Path, finding: dict[str, Any], timeout_seconds: float = 120.0) -> tuple[ExecutionResult, dict[str, Any] | None]:
    """Ask Claude for a bounded proposal; it remains state data, never a write."""
    fingerprint = source_fingerprint(root)
    if finding.get("fingerprint") != fingerprint:
        raise ValueError("Finding is stale; source fingerprint changed.")
    prompt = ("Read-only drafting only; do not edit files. Return ONLY JSON "
              '{"finding_id":"...","fingerprint":"...","changes":{"src/agent_hub/file.py":"complete replacement text"}}. '
              "Only alter existing src/agent_hub Python files; no new files, deletes, config, dependencies, or commands. Finding: "
              + json.dumps(finding, ensure_ascii=False))
    result = adapter.execute(prompt, root, timeout_seconds)
    if result.status != "succeeded" or not result.final_message: return result, None
    try: proposal = json.loads(result.final_message)
    except json.JSONDecodeError: return result, None
    changes = proposal.get("changes")
    if proposal.get("finding_id") != finding.get("finding_id") or proposal.get("fingerprint") != fingerprint or not isinstance(changes, dict) or not changes:
        return result, None
    for name, body in changes.items():
        relative = PurePosixPath(name)
        if not isinstance(body, str) or relative.is_absolute() or ".." in relative.parts or not name.startswith("src/agent_hub/") or not (root / name).is_file():
            return result, None
    proposal["proposal_id"] = hashlib.sha256(json.dumps(proposal, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    return result, proposal
