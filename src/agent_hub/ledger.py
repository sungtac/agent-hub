"""Append-only, file-based task ledger with generation-scoped provider leases."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .file_lock import exclusive_lock


LEDGER_SCHEMA_VERSION = 1
TASK_ID_PATTERN = re.compile(r"task-[0-9a-f]{32}$")
SCOPES = {"company", "personal", "shared", "quarantine"}
HANDOFF_FIELDS = ("goal", "criteria", "completed", "verification", "remaining", "next_action")
SENSITIVE_HANDOFF = re.compile(r"(?i)(?:api[_ -]?key|access[_ -]?token|secret|password|passwd)\s*[:=]\s*\S+|\b(?:sk|ghp)_[A-Za-z0-9_-]{16,}\b|\b\d{6}-[1-4]\d{6}\b")


def validate_handoff_record(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(HANDOFF_FIELDS):
        raise LedgerError("Shared handoff must contain exactly the six required fields.")
    if any(not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 4000 for key in HANDOFF_FIELDS):
        raise LedgerError("Shared handoff fields must be non-empty strings of at most 4000 characters.")
    if SENSITIVE_HANDOFF.search(json.dumps(value, ensure_ascii=False)):
        raise LedgerError("Potential credential or identifier in shared handoff; record rejected.")
    return {key: value[key].strip() for key in HANDOFF_FIELDS}


class LedgerError(RuntimeError):
    """Base error for durable task-ledger operations."""


class TaskNotFoundError(LedgerError):
    """Raised when a task directory does not exist."""


class LeaseConflictError(LedgerError):
    """Raised when another active provider already holds a task lease."""


class StaleLeaseError(LedgerError):
    """Raised when a lease id or generation cannot mutate the current task."""


class DispatchLimitError(LedgerError):
    """Raised when a durable logical-dispatch budget is exhausted."""


class DuplicateDispatchError(LedgerError):
    """Raised when an external caller repeats an already-reserved request."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fsync_directory(path: Path) -> None:
    """Persist directory-entry changes on the Linux/WSL authority filesystem."""

    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class TaskLedger:
    """Task ledger whose events file is the authority and snapshot is derived."""

    def __init__(self, tasks_root: Path) -> None:
        self.tasks_root = tasks_root.expanduser().resolve(strict=False)
        if not self.tasks_root.is_dir():
            raise LedgerError(f"Tasks root does not exist: {self.tasks_root}")

    def create_task(self, title: str, scope: str) -> dict[str, Any]:
        if not title.strip():
            raise LedgerError("Task title must not be empty.")
        if scope not in SCOPES:
            raise LedgerError(f"Unsupported task scope: {scope}")
        task_id = f"task-{uuid.uuid4().hex}"
        task_dir = self.tasks_root / task_id
        task_dir.mkdir(mode=0o700)
        if os.name != "nt":
            task_dir.chmod(0o700)
        fsync_directory(self.tasks_root)
        event = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event_type": "task_created",
            "recorded_at": utc_now(),
            "task_id": task_id,
            "title": title.strip(),
            "scope": scope,
        }
        try:
            self._append_event(task_dir, event)
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot
        except Exception:
            # Preserve the append-only event log for diagnosis; never delete it.
            raise

    def get_task(self, task_id: str) -> dict[str, Any]:
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def acquire_lease(self, task_id: str, provider: str, role: str) -> dict[str, Any]:
        if not provider.strip() or not role.strip():
            raise LedgerError("Provider and role must not be empty.")
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            if snapshot["status"] != "active":
                raise LedgerError("Completed task cannot acquire a new lease.")
            if snapshot["lease"] is not None:
                raise LeaseConflictError(f"Task already leased by {snapshot['lease']['provider']}.")
            lease = {
                "lease_id": uuid.uuid4().hex,
                "provider": provider.strip(),
                "role": role.strip(),
                "generation": snapshot["generation"] + 1,
            }
            self._append_event(
                task_dir,
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "event_id": uuid.uuid4().hex,
                    "event_type": "lease_granted",
                    "recorded_at": utc_now(),
                    "lease": lease,
                },
            )
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def release_lease(self, task_id: str, lease_id: str, generation: int, reason: str) -> dict[str, Any]:
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            lease = snapshot["lease"]
            if lease is None or lease["lease_id"] != lease_id or lease["generation"] != generation:
                raise StaleLeaseError("Lease does not match the active task generation.")
            self._append_event(
                task_dir,
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "event_id": uuid.uuid4().hex,
                    "event_type": "lease_released",
                    "recorded_at": utc_now(),
                    "lease_id": lease_id,
                    "generation": generation,
                    "reason": reason.strip() or "unspecified",
                },
            )
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def recover_stale_lease(self, task_id: str, stale_after_seconds: int, at: datetime | None = None) -> dict[str, Any]:
        """Explicitly release an abandoned lease after its durable grace period.

        A restart never silently steals a live lease.  Recovery is an owner
        action and is recorded in the append-only ledger before another
        provider can acquire the task.
        """

        if stale_after_seconds < 1:
            raise LedgerError("Stale lease recovery period must be at least one second.")
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            lease = snapshot["lease"]
            if lease is None:
                raise LedgerError("Task has no active lease to recover.")
            granted_at = lease.get("granted_at")
            try:
                granted = datetime.fromisoformat(str(granted_at))
            except ValueError as error:
                raise LedgerError("Active lease has no valid grant timestamp.") from error
            timestamp = at or datetime.now(timezone.utc)
            if granted + timedelta(seconds=stale_after_seconds) > timestamp:
                raise LeaseConflictError("Active lease is still within its recovery grace period.")
            self._append_event(
                task_dir,
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "event_id": uuid.uuid4().hex,
                    "event_type": "lease_recovered",
                    "recorded_at": utc_now(),
                    "lease_id": lease["lease_id"],
                    "generation": lease["generation"],
                    "reason": "owner-confirmed stale lease recovery",
                },
            )
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def record_approval(self, task_id: str, subject: str, decision: str) -> dict[str, Any]:
        if not subject.strip():
            raise LedgerError("Approval subject must not be empty.")
        if decision not in {"approved", "rejected"}:
            raise LedgerError("Approval decision must be approved or rejected.")
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            self._append_event(
                task_dir,
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "event_id": uuid.uuid4().hex,
                    "event_type": "approval_recorded",
                    "recorded_at": utc_now(),
                    "subject": subject.strip(),
                    "decision": decision,
                },
            )
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def record_execution(self, task_id: str, lease_id: str, generation: int, provider: str, status: str, return_code: int | None) -> dict[str, Any]:
        """Record a redacted execution outcome under the active lease."""

        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            lease = snapshot["lease"]
            if (
                lease is None
                or lease["lease_id"] != lease_id
                or lease["generation"] != generation
                or lease["provider"] != provider
            ):
                raise StaleLeaseError("Execution does not match the active task lease.")
            self._append_event(
                task_dir,
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "event_id": uuid.uuid4().hex,
                    "event_type": "execution_recorded",
                    "recorded_at": utc_now(),
                    "provider": provider,
                    "status": status,
                    "return_code": return_code,
                    "lease_id": lease_id,
                    "generation": generation,
                },
            )
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def record_handoff(self, task_id: str, lease_id: str, generation: int, provider: str, record: dict[str, str]) -> dict[str, Any]:
        """Persist a deliberately shared record against a successful active execution."""
        record = validate_handoff_record(record)
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            lease = snapshot["lease"]
            if not lease or lease["lease_id"] != lease_id or lease["generation"] != generation or lease["provider"] != provider:
                raise StaleLeaseError("Handoff does not match the active task lease.")
            if not any(x["lease_id"] == lease_id and x["generation"] == generation and x["provider"] == provider and x["status"] == "succeeded" for x in snapshot["executions"]):
                raise LedgerError("Handoff requires a successful execution under this lease.")
            if any(x["generation"] == generation for x in snapshot["handoffs"]):
                raise LedgerError("This execution already has a handoff record.")
            self._append_event(task_dir, {"schema_version": LEDGER_SCHEMA_VERSION, "event_id": uuid.uuid4().hex, "event_type": "handoff_recorded", "recorded_at": utc_now(), "lease_id": lease_id, "generation": generation, "provider": provider, "record": record})
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def record_review_receipt(
        self, task_id: str, lease_id: str, generation: int, role: str, provider: str,
        change_sha256: str, decision: str, output_sha256: str,
    ) -> dict[str, Any]:
        """Bind a parsed provider review to its successful live execution."""
        if decision not in {"pass", "return_to_design"}:
            raise LedgerError("Review decision must be pass or return_to_design.")
        if not all(re.fullmatch(r"[0-9a-f]{64}", value or "") for value in (change_sha256, output_sha256)):
            raise LedgerError("Review hashes must be SHA-256 hex digests.")
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            lease = snapshot["lease"]
            if lease is None or lease["lease_id"] != lease_id or lease["generation"] != generation or lease["provider"] != provider or lease["role"] != role:
                raise StaleLeaseError("Review receipt does not match the active task lease.")
            if not any(item["provider"] == provider and item["lease_id"] == lease_id and item["generation"] == generation and item["status"] == "succeeded" for item in snapshot["executions"]):
                raise LedgerError("Review receipt requires a successful live provider execution.")
            if any(item["role"] == role and item["change_sha256"] == change_sha256 for item in snapshot["review_receipts"]):
                raise LedgerError("Duplicate review receipt for role and change set.")
            self._append_event(task_dir, {"schema_version": LEDGER_SCHEMA_VERSION, "event_id": uuid.uuid4().hex, "event_type": "review_receipt_recorded", "recorded_at": utc_now(), "lease_id": lease_id, "generation": generation, "role": role, "provider": provider, "change_sha256": change_sha256, "decision": decision, "output_sha256": output_sha256})
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def reserve_dispatch(self, task_id: str, channel: str, maximum: int) -> dict[str, Any]:
        """Durably reserve one logical dispatch before an external CLI starts.

        A reservation counts a single owner request even if the router safely
        hands it to another provider after a usage limit or network failure.
        It is intentionally conservative: a crash after reservation consumes
        the budget rather than risking an unrecorded duplicate external call.
        """

        if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", channel) is None:
            raise LedgerError("Invalid dispatch channel.")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
            raise LedgerError("Dispatch maximum must be a positive integer.")
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            if snapshot["status"] != "active":
                raise LedgerError("Completed task cannot reserve a dispatch.")
            attempts = sum(1 for item in snapshot["dispatches"] if item["channel"] == channel)
            if attempts >= maximum:
                raise DispatchLimitError(f"Dispatch limit reached for channel: {channel}")
            self._append_event(
                task_dir,
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "event_id": uuid.uuid4().hex,
                    "event_type": "dispatch_reserved",
                    "recorded_at": utc_now(),
                    "channel": channel,
                    "attempt": attempts + 1,
                },
            )
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def reserve_external_dispatch(self, task_id: str, channel: str, maximum: int, fingerprint: str, provider: str) -> dict[str, Any]:
        """Reserve one non-Hub worker request before its process starts.

        Loadout's shell dispatcher is an external caller, so it cannot hold a
        Hub provider lease.  It still receives a durable request receipt and
        must later complete that receipt.  Equal fingerprints are never sent
        twice, including after a process crash or restart.
        """
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", channel) is None:
            raise LedgerError("Invalid dispatch channel.")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
            raise LedgerError("Dispatch maximum must be a positive integer.")
        if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise LedgerError("External dispatch fingerprint must be a SHA-256 hex digest.")
        if not provider.strip():
            raise LedgerError("External dispatch provider must not be empty.")
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            if snapshot["status"] != "active":
                raise LedgerError("Completed task cannot reserve an external dispatch.")
            if any(item["channel"] == channel and item["fingerprint"] == fingerprint for item in snapshot["external_dispatches"]):
                raise DuplicateDispatchError("External dispatch already reserved for this request fingerprint.")
            attempts = sum(1 for item in snapshot["external_dispatches"] if item["channel"] == channel)
            if attempts >= maximum:
                raise DispatchLimitError(f"Dispatch limit reached for channel: {channel}")
            dispatch_id = uuid.uuid4().hex
            self._append_event(
                task_dir,
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "event_id": uuid.uuid4().hex,
                    "event_type": "external_dispatch_reserved",
                    "recorded_at": utc_now(),
                    "dispatch_id": dispatch_id,
                    "channel": channel,
                    "fingerprint": fingerprint,
                    "provider": provider.strip(),
                },
            )
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return {"dispatch_id": dispatch_id, "task": snapshot}

    def complete_external_dispatch(self, task_id: str, dispatch_id: str, status: str, return_code: int | None) -> dict[str, Any]:
        if (
            re.fullmatch(r"[0-9a-f]{32}", dispatch_id) is None
            or status not in {"succeeded", "failed", "timed_out", "empty", "scope_violation"}
            or (return_code is not None and (not isinstance(return_code, int) or isinstance(return_code, bool)))
        ):
            raise LedgerError("Invalid external dispatch completion.")
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            match = next((item for item in snapshot["external_dispatches"] if item["dispatch_id"] == dispatch_id), None)
            if match is None or match["status"] != "reserved":
                raise LedgerError("External dispatch is not pending.")
            self._append_event(
                task_dir,
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "event_id": uuid.uuid4().hex,
                    "event_type": "external_dispatch_completed",
                    "recorded_at": utc_now(),
                    "dispatch_id": dispatch_id,
                    "status": status.strip(),
                    "return_code": return_code,
                },
            )
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def complete_task(self, task_id: str, evidence: str) -> dict[str, Any]:
        """Close a task only after all durable work has a terminal receipt."""
        if not evidence.strip():
            raise LedgerError("Task completion requires non-empty verification evidence.")
        task_dir = self._task_dir(task_id)
        with exclusive_lock(task_dir / "task.lock"):
            snapshot = self._replay(task_dir)
            if snapshot["status"] == "completed":
                raise LedgerError("Task is already completed.")
            if snapshot["lease"] is not None:
                raise LeaseConflictError("Task has an active lease and cannot be completed.")
            if any(item["status"] == "reserved" for item in snapshot["external_dispatches"]):
                raise LedgerError("Task has external dispatches without completion receipts.")
            self._append_event(
                task_dir,
                {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "event_id": uuid.uuid4().hex,
                    "event_type": "task_completed",
                    "recorded_at": utc_now(),
                    "evidence": evidence.strip(),
                },
            )
            snapshot = self._replay(task_dir)
            self._write_snapshot(task_dir, snapshot)
            return snapshot

    def _task_dir(self, task_id: str) -> Path:
        if TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise TaskNotFoundError("Invalid task identifier.")
        task_dir = self.tasks_root / task_id
        if not task_dir.is_dir():
            raise TaskNotFoundError(f"Task does not exist: {task_id}")
        return task_dir

    @staticmethod
    def _append_event(task_dir: Path, event: dict[str, Any]) -> None:
        event_path = task_dir / "events.jsonl"
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with event_path.open("a", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(task_dir)

    @staticmethod
    def _replay(task_dir: Path) -> dict[str, Any]:
        events_path = task_dir / "events.jsonl"
        try:
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
        except (OSError, json.JSONDecodeError) as error:
            raise LedgerError(f"Could not replay task ledger: {task_dir}") from error
        if not events or events[0].get("event_type") != "task_created":
            raise LedgerError("Task ledger has no valid creation event.")
        created = events[0]
        snapshot: dict[str, Any] = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "task_id": created["task_id"],
            "title": created["title"],
            "scope": created["scope"],
            "status": "active",
            "generation": 0,
            "lease": None,
            "approvals": [],
            "executions": [],
            "handoffs": [],
            "review_receipts": [],
            "dispatches": [],
            "external_dispatches": [],
            "completion": None,
            "event_count": 0,
        }
        for index, event in enumerate(events):
            event_type = event.get("event_type")
            if event_type == "lease_granted":
                lease = event.get("lease")
                if not isinstance(lease, dict) or lease.get("generation") != snapshot["generation"] + 1:
                    raise LedgerError("Invalid lease generation in event log.")
                granted_at = event.get("recorded_at")
                try:
                    datetime.fromisoformat(str(granted_at))
                except ValueError as error:
                    raise LedgerError("Lease grant has no valid timestamp.") from error
                snapshot["lease"] = {**lease, "granted_at": granted_at}
                snapshot["generation"] = lease["generation"]
            elif event_type in {"lease_released", "lease_recovered"}:
                if snapshot["lease"] is None:
                    raise LedgerError("Lease closure without active lease.")
                if (
                    event.get("lease_id") != snapshot["lease"]["lease_id"]
                    or event.get("generation") != snapshot["lease"]["generation"]
                ):
                    raise LedgerError("Lease closure does not match active lease.")
                snapshot["lease"] = None
            elif event_type == "approval_recorded":
                snapshot["approvals"].append(
                    {"subject": event.get("subject"), "decision": event.get("decision"), "recorded_at": event.get("recorded_at")}
                )
            elif event_type == "execution_recorded":
                if (
                    snapshot["lease"] is None
                    or event.get("lease_id") != snapshot["lease"]["lease_id"]
                    or event.get("generation") != snapshot["lease"]["generation"]
                    or event.get("provider") != snapshot["lease"]["provider"]
                ):
                    raise LedgerError("Execution record does not match active lease.")
                snapshot["executions"].append(
                    {
                        "event_id": event.get("event_id"),
                        "provider": event.get("provider"),
                        "status": event.get("status"),
                        "return_code": event.get("return_code"),
                        "lease_id": event.get("lease_id"),
                        "generation": event.get("generation"),
                        "recorded_at": event.get("recorded_at"),
                    }
                )
            elif event_type == "handoff_recorded":
                lease = snapshot["lease"]
                if not lease or event.get("lease_id") != lease["lease_id"] or event.get("generation") != lease["generation"] or event.get("provider") != lease["provider"] or not any(x["lease_id"] == lease["lease_id"] and x["generation"] == lease["generation"] and x["status"] == "succeeded" for x in snapshot["executions"]) or any(x["generation"] == lease["generation"] for x in snapshot["handoffs"]):
                    raise LedgerError("Invalid handoff record event.")
                snapshot["handoffs"].append({"provider": event["provider"], "generation": event["generation"], "recorded_at": event["recorded_at"], "record": validate_handoff_record(event.get("record"))})
            elif event_type == "review_receipt_recorded":
                role, provider = event.get("role"), event.get("provider")
                digest, output = event.get("change_sha256"), event.get("output_sha256")
                if (snapshot["lease"] is None or event.get("lease_id") != snapshot["lease"]["lease_id"] or event.get("generation") != snapshot["lease"]["generation"] or role != snapshot["lease"]["role"] or provider != snapshot["lease"]["provider"] or event.get("decision") not in {"pass", "return_to_design"} or not isinstance(role, str) or not re.fullmatch(r"[0-9a-f]{64}", str(digest)) or not re.fullmatch(r"[0-9a-f]{64}", str(output)) or not any(item["provider"] == provider and item["lease_id"] == event.get("lease_id") and item["generation"] == event.get("generation") and item["status"] == "succeeded" for item in snapshot["executions"]) or any(item["role"] == role and item["change_sha256"] == digest for item in snapshot["review_receipts"])):
                    raise LedgerError("Invalid review receipt event.")
                snapshot["review_receipts"].append({"role": role, "provider": provider, "change_sha256": digest, "decision": event["decision"], "output_sha256": output, "recorded_at": event.get("recorded_at")})
            elif event_type == "dispatch_reserved":
                channel = event.get("channel")
                expected_attempt = sum(1 for item in snapshot["dispatches"] if item["channel"] == channel) + 1
                if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(channel)) is None or event.get("attempt") != expected_attempt:
                    raise LedgerError("Invalid dispatch reservation event.")
                snapshot["dispatches"].append(
                    {"channel": channel, "attempt": event["attempt"], "recorded_at": event.get("recorded_at")}
                )
            elif event_type == "external_dispatch_reserved":
                dispatch_id = event.get("dispatch_id")
                channel = event.get("channel")
                fingerprint = event.get("fingerprint")
                provider = event.get("provider")
                if (
                    not isinstance(dispatch_id, str)
                    or re.fullmatch(r"[0-9a-f]{32}", dispatch_id) is None
                    or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", str(channel)) is None
                    or re.fullmatch(r"[0-9a-f]{64}", str(fingerprint)) is None
                    or not isinstance(provider, str)
                    or not provider
                    or any(item["dispatch_id"] == dispatch_id or (item["channel"] == channel and item["fingerprint"] == fingerprint) for item in snapshot["external_dispatches"])
                ):
                    raise LedgerError("Invalid external dispatch reservation event.")
                snapshot["external_dispatches"].append(
                    {"dispatch_id": dispatch_id, "channel": channel, "fingerprint": fingerprint, "provider": provider, "status": "reserved", "recorded_at": event.get("recorded_at"), "return_code": None}
                )
            elif event_type == "external_dispatch_completed":
                match = next((item for item in snapshot["external_dispatches"] if item["dispatch_id"] == event.get("dispatch_id")), None)
                if (
                    match is None
                    or match["status"] != "reserved"
                    or event.get("status") not in {"succeeded", "failed", "timed_out", "empty", "scope_violation"}
                    or (event.get("return_code") is not None and (not isinstance(event.get("return_code"), int) or isinstance(event.get("return_code"), bool)))
                ):
                    raise LedgerError("Invalid external dispatch completion event.")
                match["status"] = event["status"]
                match["return_code"] = event.get("return_code")
                match["completed_at"] = event.get("recorded_at")
            elif event_type == "task_completed":
                if snapshot["status"] != "active" or snapshot["lease"] is not None or not isinstance(event.get("evidence"), str) or not event["evidence"].strip():
                    raise LedgerError("Invalid task completion event.")
                if any(item["status"] == "reserved" for item in snapshot["external_dispatches"]):
                    raise LedgerError("Task completion has a pending external dispatch.")
                snapshot["status"] = "completed"
                snapshot["completion"] = {"evidence": event["evidence"], "recorded_at": event.get("recorded_at")}
            elif event_type == "task_created" and index != 0:
                raise LedgerError("Task creation event may only occur first.")
            elif event_type != "task_created":
                raise LedgerError(f"Unsupported event type: {event_type}")
            snapshot["event_count"] += 1
        return snapshot

    @staticmethod
    def _write_snapshot(task_dir: Path, snapshot: dict[str, Any]) -> None:
        destination = task_dir / "task.json"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=task_dir, delete=False) as temporary:
                json.dump(snapshot, temporary, ensure_ascii=False, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, destination)
            fsync_directory(task_dir)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
