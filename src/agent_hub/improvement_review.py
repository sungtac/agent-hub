"""Run and attest one read-only self-improvement review."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from .ledger import TaskLedger
from .multiagent_improvement import change_sha256
from .providers import ExecutionResult


ROLE_PROVIDERS = {"codex-main": "codex", "claude-main": "claude"}


class ReviewAdapter(Protocol):
    provider: str
    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult: ...


def run_review(ledger: TaskLedger, task_id: str, role: str, changes: dict[str, str], adapter: ReviewAdapter, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
    """Execute one fixed-provider review and record only a verified decision."""
    if ROLE_PROVIDERS.get(role) != adapter.provider:
        raise ValueError("Review role must use its fixed independent provider.")
    digest = change_sha256(changes)
    prompt = ("Read-only improvement review. Do not modify files. Review the exact candidate below and return ONLY JSON "
              '{"candidate_sha256":"%s","review_decision":"pass|return_to_design"}. Candidate: %s'
              % (digest, json.dumps(dict(sorted(changes.items())), ensure_ascii=False)))
    leased = ledger.acquire_lease(task_id, adapter.provider, role)
    lease = leased["lease"]
    try:
        result = adapter.execute(prompt, working_directory, timeout_seconds)
        ledger.record_execution(task_id, lease["lease_id"], lease["generation"], adapter.provider, result.status, result.return_code)
        if result.status == "succeeded" and result.final_message:
            try:
                payload = json.loads(result.final_message)
                decision = payload.get("review_decision")
                returned = payload.get("candidate_sha256")
            except json.JSONDecodeError:
                decision = returned = None
            if returned == digest and decision in {"pass", "return_to_design"}:
                ledger.record_review_receipt(task_id, lease["lease_id"], lease["generation"], role, adapter.provider, digest, decision, hashlib.sha256(result.final_message.encode("utf-8")).hexdigest())
        return result
    finally:
        ledger.release_lease(task_id, lease["lease_id"], lease["generation"], "improvement review completed or failed")


def run_missing_reviews(ledger: TaskLedger, task_id: str, changes: dict[str, str], working_directory: Path, timeout_seconds: float, adapters: dict[str, ReviewAdapter]) -> None:
    """Resume only review roles still missing a receipt for this exact patch."""
    digest = change_sha256(changes)
    snapshot = ledger.get_task(task_id)
    receipts = {item["role"]: item for item in snapshot["review_receipts"] if item["change_sha256"] == digest}
    for role, provider in ROLE_PROVIDERS.items():
        receipt = receipts.get(role)
        if receipt is not None:
            continue
        run_review(ledger, task_id, role, changes, adapters[provider], working_directory, timeout_seconds)
