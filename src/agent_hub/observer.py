"""Durable, one-at-a-time improvement observer for the local Hub harness."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .improvement import ImprovementQueue
from .multiagent_improvement import MultiAgentReviewReceipt, change_sha256, validate_ledger_review
from .file_lock import exclusive_lock
from .sandbox import SandboxImprovementRunner, requires_approval
from .ledger import TaskLedger


RUNNING_RECOVERY_SECONDS = 3600


class ImprovementObserver:
    """Persist proposed changes and promote at most one candidate per invocation.

    A scheduler may call :meth:`run_once` repeatedly.  The observer itself
    never creates source patches: a provider/critic must supply evidence and a
    concrete candidate first, leaving an auditable boundary before automatic
    promotion.
    """

    def __init__(self, state_path: Path, queue: ImprovementQueue, runner: SandboxImprovementRunner | None, project_root: Path, backup_root: Path, ledger: TaskLedger | None = None) -> None:
        self.state_path = state_path.expanduser().resolve(strict=False)
        if not self.state_path.parent.is_dir():
            raise ValueError("Observer state directory must already exist.")
        self.queue = queue
        self.runner = runner
        self.project_root = project_root.expanduser().resolve(strict=True)
        self.backup_root = backup_root.expanduser().resolve(strict=False)
        self.ledger = ledger
        self.lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")

    def observe(self, area: str, summary: str, evidence: str, changes: Mapping[str, str], receipt: MultiAgentReviewReceipt) -> str:
        if not changes or not all(isinstance(path, str) and isinstance(body, str) for path, body in changes.items()):
            raise ValueError("Observed changes must be a non-empty string mapping.")
        if self.ledger is None:
            raise ValueError("Observed improvements require an attested task ledger.")
        if receipt.change_sha256 != change_sha256(changes):
            raise ValueError("Review receipt does not match the observed change set.")
        verified = validate_ledger_review(self.ledger, receipt.task_directory, changes)
        if verified != receipt:
            raise ValueError("Observed improvement receipt is not the live ledger receipt.")
        candidate = self.queue.propose(area, summary, evidence)
        approval = self._approval_subject(candidate.identifier, receipt.change_sha256) if requires_approval(changes) else None
        with exclusive_lock(self.lock_path):
            state = self._load()
            current = state["observations"].get(candidate.identifier)
            if current is None:
                state["observations"][candidate.identifier] = {
                    "area": area,
                    "changes": dict(changes),
                    "multiagent_review": receipt.serialise(),
                    "status": "awaiting_approval" if approval else "pending",
                    "approval_subject": approval,
                    "task_id": receipt.task_directory,
                    "before_sha256": {name: hashlib.sha256((self.project_root / name).read_bytes()).hexdigest() if (self.project_root / name).is_file() else "" for name in changes},
                }
                self._write(state)
            elif current["changes"] != dict(changes):
                raise ValueError("A duplicate candidate cannot replace its recorded change set.")
        return candidate.identifier

    def run_once(self, candidate_id: str | None = None) -> dict[str, Any] | None:
        with exclusive_lock(self.lock_path):
            state = self._load()
            self._recover_stale_running(state)
            for identifier, item in state["observations"].items():
                if candidate_id is not None and identifier != candidate_id:
                    continue
                if item.get("status") == "awaiting_approval" and self._approved(item):
                    item["status"] = "pending"
            pending = next(((identifier, item) for identifier, item in state["observations"].items() if item["status"] == "pending" and (candidate_id is None or identifier == candidate_id)), None)
            if pending is None:
                self._write(state)
                return None
            identifier, item = pending
            item["status"] = "running"
            item["started_at"] = self._now()
            self._write(state)
        try:
            if self.runner is None:
                raise RuntimeError("A sandbox runner is required to execute an observation.")
            if item.get("approval_subject"):
                result = self.runner.run(self.project_root, identifier, item["area"], item["changes"], self.backup_root, self.queue, frozenset(item["changes"]), item["before_sha256"])
            else:
                result = self.runner.run(self.project_root, identifier, item["area"], item["changes"], self.backup_root, self.queue, frozenset(), item["before_sha256"])
        except Exception as error:
            outcome = {"status": "failed", "diagnostic": f"{type(error).__name__}: {error}"}
        else:
            outcome = {"status": result.status, "backup_directory": result.backup_directory, "diagnostic": result.diagnostic}
        with exclusive_lock(self.lock_path):
            state = self._load()
            state["observations"][identifier].update(outcome)
            state["observations"][identifier].pop("started_at", None)
            self._write(state)
        return {"candidate_id": identifier, **outcome}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _recover_stale_running(self, state: dict[str, Any]) -> None:
        """Return abandoned executions to the durable pending queue.

        A fresh ``running`` item belongs to another active invocation and is
        left untouched.  A crashed process cannot clear its receipt, so an old
        one is explicitly made eligible for a later bounded retry.
        """

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=RUNNING_RECOVERY_SECONDS)
        for item in state["observations"].values():
            if item.get("status") != "running":
                continue
            try:
                started_at = datetime.fromisoformat(str(item["started_at"]))
            except (KeyError, TypeError, ValueError):
                started_at = datetime.min.replace(tzinfo=timezone.utc)
            if started_at <= cutoff:
                item["status"] = "pending"
                item["recovery_note"] = "Recovered abandoned running observation."
                item.pop("started_at", None)

    @staticmethod
    def _approval_subject(candidate_id: str, digest: str) -> str:
        return f"improvement-v1:{candidate_id}:{digest}"

    def _approved(self, item: Mapping[str, Any]) -> bool:
        if self.ledger is None:
            return False
        try:
            task = self.ledger.get_task(str(item["task_id"]))
        except Exception:
            return False
        return any(entry["subject"] == item["approval_subject"] and entry["decision"] == "approved" for entry in task["approvals"])

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"schema_version": 1, "observations": {}}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Could not read observer state.") from error
        if state.get("schema_version") != 1 or not isinstance(state.get("observations"), dict):
            raise ValueError("Unsupported observer state.")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_path.parent, delete=False) as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                temporary = Path(stream.name)
            os.replace(temporary, self.state_path)
            if os.name != "nt":
                self.state_path.chmod(0o600)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
