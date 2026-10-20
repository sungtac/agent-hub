from __future__ import annotations

import tempfile
import unittest
import os
import json
from pathlib import Path

from agent_hub.improvement_review import run_missing_reviews, run_review
from agent_hub.ledger import TaskLedger
from agent_hub.multiagent_improvement import ReviewCycleError, ReviewReturnedToDesign, change_sha256, validate_loadout_review
from agent_hub.providers import ExecutionResult


class ReviewAdapter:
    def __init__(self, provider: str, response: dict[str, str]) -> None:
        self.provider = provider
        self.response = response
        self.calls = 0

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(self.provider, "succeeded", 0, json.dumps(self.response), (), None)


class MultiAgentImprovementTests(unittest.TestCase):
    def _task(self, root: Path, changes: dict[str, str], decision: str = "pass") -> Path:
        task = root / "task"
        digest = change_sha256(changes)
        (task / "task.md").parent.mkdir(parents=True)
        (task / "task.md").write_text("\n".join(("# task", "```yaml", "workers_approved:", "- worker: claude-main", "- worker: codex-main", "```")), encoding="utf-8")
        for role in ("claude-main", "codex-main"):
            result = task / "workers" / role / "result.md"
            result.parent.mkdir(parents=True)
            result.write_text(f"status: complete\ncandidate_sha256: {digest}\nreview_decision: {decision}\n", encoding="utf-8")
        return task

    def test_exact_three_role_pass_receipts_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            changes = {"src/agent_hub/example.py": "VALUE = 1\n"}
            receipt = validate_loadout_review(self._task(Path(temporary_directory), changes), changes)
            self.assertEqual(("claude-main", "codex-main"), receipt.roles)

    def test_rejection_returns_to_design_and_mismatched_changes_are_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            changes = {"src/agent_hub/example.py": "VALUE = 1\n"}
            with self.assertRaises(ReviewReturnedToDesign):
                validate_loadout_review(self._task(Path(temporary_directory), changes, "return_to_design"), changes)
        with tempfile.TemporaryDirectory() as temporary_directory:
            changes = {"src/agent_hub/example.py": "VALUE = 1\n"}
            task = self._task(Path(temporary_directory), changes)
            with self.assertRaises(ReviewCycleError):
                validate_loadout_review(task, {"src/agent_hub/example.py": "VALUE = 2\n"})

    @unittest.skipIf(os.name == "nt", "symlink creation is permission-dependent on Windows")
    def test_task_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            changes = {"src/agent_hub/example.py": "VALUE = 1\n"}
            task = self._task(root / "real", changes)
            link = root / "link"
            link.symlink_to(task, target_is_directory=True)
            with self.assertRaises(ReviewCycleError):
                validate_loadout_review(link, changes)

    def test_retry_runs_only_the_missing_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory); tasks = root / "tasks"; tasks.mkdir()
            ledger = TaskLedger(tasks); task = ledger.create_task("retry", "personal")
            changes = {"src/agent_hub/example.py": "VALUE = 1\n"}; digest = change_sha256(changes)
            codex = ReviewAdapter("codex", {"candidate_sha256": digest, "review_decision": "pass"})
            claude = ReviewAdapter("claude", {"candidate_sha256": digest, "review_decision": "pass"})
            run_review(ledger, task["task_id"], "codex-main", changes, codex, root)
            run_missing_reviews(ledger, task["task_id"], changes, root, 10, {"codex": codex, "claude": claude})
            self.assertEqual(1, codex.calls)
            self.assertEqual(1, claude.calls)
