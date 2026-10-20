"""Loadout receipt gate for bounded automatic improvements.

An improvement may be promoted only after the three logical Loadout roles have
reviewed the exact proposed change set.  Provider substitution is allowed by
Loadout, but it must retain the logical role name in its result receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .ledger import TaskLedger


# Antigravity is deliberately not part of the self-improvement trust path.
# The independent reviewers are the two locally routed providers only.
REQUIRED_ROLES = ("claude-main", "codex-main")


class ReviewCycleError(ValueError):
    """The Loadout task does not yet provide promotable review evidence."""


class ReviewReturnedToDesign(ReviewCycleError):
    """A reviewer rejected the proposal; restart at the design stage."""


@dataclass(frozen=True)
class MultiAgentReviewReceipt:
    task_directory: str
    change_sha256: str
    roles: tuple[str, ...]

    def serialise(self) -> dict[str, object]:
        return asdict(self)


def change_sha256(changes: Mapping[str, str]) -> str:
    """Return a stable fingerprint that binds receipts to one exact proposal."""
    if not changes or not all(isinstance(path, str) and isinstance(body, str) for path, body in changes.items()):
        raise ValueError("Changes must be a non-empty string mapping.")
    canonical = json.dumps(dict(sorted(changes.items())), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_loadout_review(task_directory: Path, changes: Mapping[str, str]) -> MultiAgentReviewReceipt:
    """Accept only complete, passing receipts for the exact change fingerprint."""
    supplied = task_directory.expanduser()
    if supplied.is_symlink():
        raise ReviewCycleError("Loadout task directory must be a real directory.")
    task = supplied.resolve(strict=True)
    if not task.is_dir():
        raise ReviewCycleError("Loadout task directory must be a real directory.")
    task_file = _contained_file(task, task / "task.md")
    task_text = _read(task_file)
    digest = change_sha256(changes)
    missing_approvals = [role for role in REQUIRED_ROLES if not re.search(rf"(?m)^\s*-\s*worker:\s*{re.escape(role)}\s*$", task_text)]
    if missing_approvals:
        raise ReviewCycleError("Loadout task lacks approved roles: " + ", ".join(missing_approvals))
    incomplete: list[str] = []
    rejected: list[str] = []
    for role in REQUIRED_ROLES:
        result_file = _contained_file(task, task / "workers" / role / "result.md")
        if not result_file.is_file():
            incomplete.append(role)
            continue
        result = _read(result_file)
        status = _field(result, "status")
        decision = _field(result, "review_decision")
        receipt_digest = _field(result, "candidate_sha256")
        if receipt_digest != digest:
            incomplete.append(role + " (change fingerprint mismatch)")
        elif status in {"failed", "rejected"} or decision == "return_to_design":
            rejected.append(role)
        elif status != "complete" or decision != "pass":
            incomplete.append(role)
    if rejected:
        raise ReviewReturnedToDesign("Review returned to design by: " + ", ".join(rejected))
    if incomplete:
        raise ReviewCycleError("Loadout review is incomplete: " + ", ".join(incomplete))
    return MultiAgentReviewReceipt(str(task), digest, REQUIRED_ROLES)


def validate_ledger_review(ledger: TaskLedger, task_id: str, changes: Mapping[str, str]) -> MultiAgentReviewReceipt:
    """Accept only Hub-recorded live Codex and Claude review receipts."""
    snapshot = ledger.get_task(task_id)
    digest = change_sha256(changes)
    receipts = {item["role"]: item for item in snapshot["review_receipts"] if item["change_sha256"] == digest}
    missing = [role for role in REQUIRED_ROLES if role not in receipts]
    rejected = [role for role in REQUIRED_ROLES if role in receipts and receipts[role]["decision"] == "return_to_design"]
    if rejected:
        raise ReviewReturnedToDesign("Review returned to design by: " + ", ".join(rejected))
    if missing or any(receipts[role]["decision"] != "pass" for role in REQUIRED_ROLES):
        raise ReviewCycleError("Ledger review is incomplete: " + ", ".join(missing or REQUIRED_ROLES))
    return MultiAgentReviewReceipt(task_id, digest, REQUIRED_ROLES)


def _contained_file(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ReviewCycleError("Loadout receipt path escapes its task directory.") from error
    return resolved


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ReviewCycleError(f"Could not read Loadout receipt: {path}") from error


def _field(text: str, name: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(name)}:\s*([^\s#]+)", text)
    return match.group(1) if match else None
