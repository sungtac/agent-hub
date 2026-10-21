"""Network-isolated test and atomic promotion for low-risk Hub code changes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from .file_lock import exclusive_lock
from .ledger import fsync_directory

try:
    import resource
except ImportError:  # Windows has no POSIX resource module.
    resource = None  # type: ignore[assignment]


PROTECTED_FILES = {
    "src/agent_hub/backup.py",
    "src/agent_hub/cli.py",
    "src/agent_hub/configuration.py",
    "src/agent_hub/compatibility.py",
    "src/agent_hub/environment.py",
    "src/agent_hub/integrations.py",
    "src/agent_hub/knowledge.py",
    "src/agent_hub/ledger.py",
    "src/agent_hub/loadout.py",
    "src/agent_hub/orchestration.py",
    "src/agent_hub/routing.py",
    "src/agent_hub/providers.py",
    "src/agent_hub/improvement.py",
    "src/agent_hub/memory_candidates.py",
    "src/agent_hub/review.py",
    "src/agent_hub/sandbox.py",
    "src/agent_hub/skills.py",
    "src/agent_hub/workflow.py",
    # The improvement trust path itself, dispatch budgets, provider-switch
    # rules, scheduling and policy state must never self-promote unreviewed by
    # the owner (IMPLEMENTATION_PLAN.md "Bounded autonomous improvement").
    "src/agent_hub/discovery.py",
    "src/agent_hub/file_lock.py",
    "src/agent_hub/improvement_review.py",
    "src/agent_hub/loadout_bridge.py",
    "src/agent_hub/loadout_manager.py",
    "src/agent_hub/multiagent_improvement.py",
    "src/agent_hub/natural_language.py",
    "src/agent_hub/observer.py",
    "src/agent_hub/provider_gateway.py",
    "src/agent_hub/scheduler.py",
    "src/agent_hub/subscription_langchain.py",
    "src/agent_hub/subscription_memory.py",
    "src/agent_hub/subscription_policy.py",
    # The package initialiser runs before every module above is imported, so
    # an unreviewed edit to it could rebind any protected policy at runtime.
    "src/agent_hub/__init__.py",
}


def requires_approval(changes: Mapping[str, str]) -> bool:
    """Classify by actual target paths, never by a caller-supplied area label."""
    # Normalise so "src/agent_hub//cli.py" or "src/agent_hub/./cli.py" cannot
    # address a protected file under an unlisted spelling.
    return any(name in PROTECTED_FILES or PurePosixPath(name).as_posix() in PROTECTED_FILES for name in changes)


@dataclass(frozen=True)
class SandboxResult:
    status: str
    changed_files: tuple[str, ...]
    before_sha256: dict[str, str]
    after_sha256: dict[str, str]
    baseline_return_code: int
    candidate_return_code: int | None
    diagnostic: str
    backup_directory: str | None


class SandboxImprovementRunner:
    """Test changed Hub source in bubblewrap, then back up and atomically promote."""

    def __init__(self, python_executable: Path, timeout_seconds: int = 300) -> None:
        self.python_executable = python_executable.expanduser().absolute()
        if not self.python_executable.is_file():
            raise ValueError("Python executable does not exist.")
        self.runtime_roots = tuple(dict.fromkeys((Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())))
        self.timeout_seconds = timeout_seconds
        if timeout_seconds < 1 or shutil.which("bwrap") is None:
            raise RuntimeError("bubblewrap is required for isolated automatic improvement.")

    def run(
        self,
        project_root: Path,
        candidate_id: str,
        area: str,
        changes: Mapping[str, str],
        backup_root: Path,
        improvement_queue: object | None = None,
        approved_paths: frozenset[str] = frozenset(),
        expected_before: Mapping[str, str] | None = None,
    ) -> SandboxResult:
        if improvement_queue is not None:
            candidate = improvement_queue.get_candidate(candidate_id)
            if candidate["area"] != area or candidate["status"] != "candidate":
                raise ValueError("Candidate area or state does not match the sandbox request.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", candidate_id) or not changes:
            raise ValueError("Candidate id and source changes are required.")
        root = project_root.expanduser().resolve(strict=True)
        if not (root / "src").is_dir() or not (root / "tests").is_dir():
            raise ValueError("Project must contain src/ and tests/ directories.")
        if requires_approval(changes) and set(changes) != set(approved_paths):
            raise PermissionError("High-risk changes require an approval for this exact file set.")
        relative_changes = self._validate_changes(root, changes, approved_paths)
        before = {name: self._sha256(root / name) for name in relative_changes}
        if expected_before is not None and before != dict(expected_before):
            raise PermissionError("Source changed after review; approval and receipts are stale.")

        with tempfile.TemporaryDirectory(prefix="agent-hub-improvement-") as temporary:
            sandbox = Path(temporary) / "workspace"
            sandbox.mkdir()
            shutil.copytree(root / "src", sandbox / "src", symlinks=False)
            shutil.copytree(root / "tests", sandbox / "tests", symlinks=False)
            baseline = self._run_tests(sandbox)
            if baseline.returncode != 0:
                return SandboxResult("baseline_failed", tuple(relative_changes), before, {}, baseline.returncode, None, self._diagnostic(baseline), None)
            for name, contents in changes.items():
                target = sandbox / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(contents, encoding="utf-8")
            candidate = self._run_tests(sandbox)
            if candidate.returncode != 0:
                if improvement_queue is not None:
                    improvement_queue.promote(candidate_id, False)
                return SandboxResult("rejected", tuple(relative_changes), before, {}, baseline.returncode, candidate.returncode, self._diagnostic(candidate), None)

        backup_base = backup_root.expanduser().resolve(strict=False)
        backup_base.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            backup_base.chmod(0o700)
        with exclusive_lock(backup_base / ".promotion.lock"):
            # Recheck immediately before backup/apply.  The lock serialises
            # Hub promotions; the digest preserves unrelated user edits.
            current = {name: self._sha256(root / name) for name in relative_changes}
            if current != before:
                raise PermissionError("Source changed while candidate tests ran; refusing to overwrite newer work.")
            backup_directory = backup_base / candidate_id
            backup_directory.mkdir(mode=0o700, exist_ok=False)
            try:
                for name in relative_changes:
                    original = root / name
                    saved = backup_directory / name
                    saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copy2(original, saved, follow_symlinks=False)
                record = {
                    "candidate_id": candidate_id,
                    "area": area,
                    "files": {name: {"before": before[name], "after": self._sha256(root / name) if name not in changes else hashlib.sha256(changes[name].encode("utf-8")).hexdigest()} for name in relative_changes},
                    "status": "prepared",
                }
                (backup_directory / "promotion.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                if os.name != "nt":
                    (backup_directory / "promotion.json").chmod(0o600)
                self._apply(root, changes)
                if improvement_queue is not None:
                    promotion = improvement_queue.promote(candidate_id, True)
                    if promotion != "promoted":
                        self._restore(root, backup_directory, relative_changes)
                        return SandboxResult("deferred", tuple(relative_changes), before, {}, baseline.returncode, candidate.returncode, "Daily promotion limit reached; source files restored.", str(backup_directory))
                record["status"] = "promoted"
                (backup_directory / "promotion.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                after = {name: self._sha256(root / name) for name in relative_changes}
                return SandboxResult("promoted", tuple(relative_changes), before, after, baseline.returncode, candidate.returncode, self._diagnostic(candidate), str(backup_directory))
            except Exception:
                self._restore(root, backup_directory, relative_changes)
                raise

    def _run_tests(self, workspace: Path) -> subprocess.CompletedProcess[str]:
        command = self._bubblewrap_command(workspace)
        command.extend((str(self.python_executable), "-m", "unittest", "discover", "-s", "tests", "-q"))
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                preexec_fn=self._set_resource_limits if os.name != "nt" else None,
            )
        except subprocess.TimeoutExpired as error:
            return subprocess.CompletedProcess(command, 124, error.stdout or "", error.stderr or "Test execution timed out.")

    @staticmethod
    def _set_resource_limits() -> None:
        if resource is None:
            return
        resource.setrlimit(resource.RLIMIT_CPU, (300, 305))
        resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 1024 * 1024, 100 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))

    def _bubblewrap_command(self, workspace: Path) -> list[str]:
        command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session"]
        for system_path in ("/usr", "/bin", "/lib", "/lib64", "/etc"):
            if Path(system_path).exists():
                command.extend(("--ro-bind", system_path, system_path))
        command.extend(("--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/home"))
        shared_root = Path(os.path.commonpath([str(path) for path in self.runtime_roots]))
        parents: list[Path] = []
        cursor = shared_root
        while cursor != cursor.parent:
            parents.append(cursor)
            cursor = cursor.parent
        for directory in reversed(parents):
            if directory != Path("/home"):
                command.extend(("--dir", str(directory)))
        for dependency_path in self.runtime_roots:
            if dependency_path.exists():
                command.extend(("--ro-bind", str(dependency_path), str(dependency_path)))
        command.extend(("--bind", str(workspace), "/workspace", "--chdir", "/workspace"))
        command.extend(("--setenv", "HOME", "/tmp", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "PYTHONPATH", "/workspace/src", "--"))
        return command

    @staticmethod
    def _validate_changes(root: Path, changes: Mapping[str, str], approved_paths: frozenset[str] = frozenset()) -> list[str]:
        names = sorted(changes)
        for name in names:
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or not name.startswith("src/agent_hub/") or (name in PROTECTED_FILES and name not in approved_paths):
                raise PermissionError(f"Automatic source change is outside the allowed area: {name}")
            target = root.joinpath(*relative.parts)
            if any(part.is_symlink() for part in (target, *target.parents)) or not target.is_file():
                raise ValueError(f"Automatic source target must be an existing regular file: {name}")
        return names

    def rollback(self, project_root: Path, backup_directory: Path, improvement_queue: object | None = None) -> SandboxResult:
        root = project_root.expanduser().resolve(strict=True)
        backup = backup_directory.expanduser().resolve(strict=True)
        record_path = backup / "promotion.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("status") != "promoted" or record.get("area") in {"policy", "security", "budget", "provider-routing", "retention"}:
            raise ValueError("Only a completed low-risk promotion can be rolled back.")
        names = sorted(record.get("files", {}))
        self._validate_changes(root, {name: "" for name in names})
        before = {name: record["files"][name]["after"] for name in names}
        current = {name: self._sha256(root / name) for name in names}
        if current != before:
            raise ValueError("Source changed after promotion; refusing to overwrite newer work.")
        for name in names:
            if self._sha256(backup / name) != record["files"][name]["before"]:
                raise ValueError(f"Backup integrity check failed: {name}")
        self._restore(root, backup, names)
        record["status"] = "rolled_back"
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if improvement_queue is not None:
            improvement_queue.set_cooldown(record["area"], "automatic improvement rollback")
        restored = {name: self._sha256(root / name) for name in names}
        return SandboxResult("rolled_back", tuple(names), before, restored, 0, 0, "Promotion restored from verified backup.", str(backup))

    @staticmethod
    def _apply(root: Path, changes: Mapping[str, str]) -> None:
        for name, contents in changes.items():
            target = root / name
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as stream:
                    stream.write(contents)
                    stream.flush()
                    os.fsync(stream.fileno())
                    temporary = Path(stream.name)
                temporary.chmod(target.stat().st_mode & 0o777)
                os.replace(temporary, target)
                fsync_directory(target.parent)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()

    @staticmethod
    def _restore(root: Path, backup_directory: Path, names: list[str]) -> None:
        for name in names:
            saved = backup_directory / name
            if saved.is_file():
                destination = root / name
                temporary = destination.with_suffix(destination.suffix + ".restore-tmp")
                shutil.copy2(saved, temporary)
                os.replace(temporary, destination)
                fsync_directory(destination.parent)

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _diagnostic(result: subprocess.CompletedProcess[str]) -> str:
        return (result.stdout + "\n" + result.stderr)[-4000:].strip()
