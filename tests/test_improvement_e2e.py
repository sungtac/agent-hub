from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from agent_hub.discovery import discover, draft
from agent_hub.improvement import ImprovementQueue
from agent_hub.improvement_review import run_review
from agent_hub.ledger import TaskLedger
from agent_hub.multiagent_improvement import validate_ledger_review
from agent_hub.observer import ImprovementObserver
from agent_hub.providers import ExecutionResult
from agent_hub.sandbox import SandboxImprovementRunner


class FakeAdapter:
    def __init__(self, provider: str, response: object) -> None:
        self.provider = provider
        self.response = response

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
        payload = self.response(prompt) if callable(self.response) else self.response
        return ExecutionResult(self.provider, "succeeded", 0, json.dumps(payload), (), None)


@unittest.skipUnless(shutil.which("bwrap"), "bubblewrap is required for E2E sandbox test")
class ImprovementEndToEndTests(unittest.TestCase):
    def _project(self, root: Path) -> None:
        source = root / "src" / "agent_hub"; tests = root / "tests"
        source.mkdir(parents=True); tests.mkdir()
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
        (tests / "test_value.py").write_text("from agent_hub.value import VALUE\nimport unittest\nclass T(unittest.TestCase):\n def test_value(self): self.assertGreater(VALUE, 0)\n", encoding="utf-8")

    def test_readonly_discovery_to_attested_sandbox_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self._project(root)
            finding_adapter = FakeAdapter("codex", {"summary": "raise value", "evidence": "src/agent_hub/value.py:1", "area": "quality"})
            _, finding = discover(finding_adapter, root)
            self.assertIsNotNone(finding)
            proposal_adapter = FakeAdapter("claude", lambda _: {"finding_id": finding["finding_id"], "fingerprint": finding["fingerprint"], "changes": {"src/agent_hub/value.py": "VALUE = 2\n"}})
            _, proposal = draft(proposal_adapter, root, finding)
            self.assertIsNotNone(proposal)
            tasks = root / "runtime" / "tasks"; state = root / "runtime" / "state"; tasks.mkdir(parents=True); state.mkdir()
            ledger = TaskLedger(tasks); task = ledger.create_task("e2e", "personal")
            digest = __import__("agent_hub.multiagent_improvement", fromlist=["change_sha256"]).change_sha256(proposal["changes"])
            for role, provider in (("codex-main", "codex"), ("claude-main", "claude")):
                adapter = FakeAdapter(provider, {"candidate_sha256": digest, "review_decision": "pass"})
                run_review(ledger, task["task_id"], role, proposal["changes"], adapter, root)
            receipt = validate_ledger_review(ledger, task["task_id"], proposal["changes"])
            queue = ImprovementQueue(state / "improvements.json")
            observer = ImprovementObserver(state / "observer.json", queue, SandboxImprovementRunner(Path(sys.executable)), root, root / "backups", ledger)
            observer.observe("quality", finding["summary"], finding["evidence"], proposal["changes"], receipt)
            outcome = observer.run_once()
            self.assertEqual("promoted", outcome["status"])
            self.assertEqual("VALUE = 2\n", (root / "src/agent_hub/value.py").read_text(encoding="utf-8"))

    def test_protected_change_needs_exact_approval_before_promotion(self) -> None:
        """A reviewed protected-file patch stays inert until its exact approval."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self._project(root)
            protected = root / "src" / "agent_hub" / "cli.py"
            protected.write_text("VALUE = 1\n", encoding="utf-8")
            (root / "tests" / "test_cli.py").write_text(
                "from agent_hub.cli import VALUE\nimport unittest\n"
                "class T(unittest.TestCase):\n def test_value(self): self.assertGreater(VALUE, 0)\n",
                encoding="utf-8",
            )
            finding_adapter = FakeAdapter("codex", {"summary": "raise protected value", "evidence": "src/agent_hub/cli.py:1", "area": "quality"})
            _, finding = discover(finding_adapter, root)
            self.assertIsNotNone(finding)
            proposal_adapter = FakeAdapter("claude", lambda _: {"finding_id": finding["finding_id"], "fingerprint": finding["fingerprint"], "changes": {"src/agent_hub/cli.py": "VALUE = 2\n"}})
            _, proposal = draft(proposal_adapter, root, finding)
            self.assertIsNotNone(proposal)
            tasks = root / "runtime" / "tasks"; state = root / "runtime" / "state"; tasks.mkdir(parents=True); state.mkdir()
            ledger = TaskLedger(tasks); task = ledger.create_task("protected-e2e", "personal")
            digest = __import__("agent_hub.multiagent_improvement", fromlist=["change_sha256"]).change_sha256(proposal["changes"])
            for role, provider in (("codex-main", "codex"), ("claude-main", "claude")):
                run_review(ledger, task["task_id"], role, proposal["changes"], FakeAdapter(provider, {"candidate_sha256": digest, "review_decision": "pass"}), root)
            receipt = validate_ledger_review(ledger, task["task_id"], proposal["changes"])
            queue = ImprovementQueue(state / "improvements.json")
            observer = ImprovementObserver(state / "observer.json", queue, SandboxImprovementRunner(Path(sys.executable)), root, root / "backups", ledger)
            candidate_id = observer.observe("quality", finding["summary"], finding["evidence"], proposal["changes"], receipt)

            self.assertIsNone(observer.run_once(candidate_id))
            self.assertEqual("VALUE = 1\n", protected.read_text(encoding="utf-8"))

            approval_subject = observer._load()["observations"][candidate_id]["approval_subject"]
            ledger.record_approval(task["task_id"], approval_subject, "approved")
            outcome = observer.run_once(candidate_id)
            self.assertEqual("promoted", outcome["status"])
            self.assertEqual("VALUE = 2\n", protected.read_text(encoding="utf-8"))

    def test_observer_forwards_approved_paths_and_expected_before_to_real_runner(self) -> None:
        """The observer must hand the real runner the exact approved-path set and
        the before-hashes it recorded (observer.py:93-96).  A protected candidate
        reaches the sandbox only through its approval (approved_paths honoured),
        and a source edit after review makes the recorded before-hashes stale, so
        the runner refuses to promote (expected_before honoured)."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self._project(root)
            protected = root / "src" / "agent_hub" / "cli.py"
            protected.write_text("VALUE = 1\n", encoding="utf-8")
            (root / "tests" / "test_cli.py").write_text(
                "from agent_hub.cli import VALUE\nimport unittest\n"
                "class T(unittest.TestCase):\n def test_value(self): self.assertGreater(VALUE, 0)\n",
                encoding="utf-8",
            )
            finding_adapter = FakeAdapter("codex", {"summary": "raise protected value", "evidence": "src/agent_hub/cli.py:1", "area": "quality"})
            _, finding = discover(finding_adapter, root)
            self.assertIsNotNone(finding)
            proposal_adapter = FakeAdapter("claude", lambda _: {"finding_id": finding["finding_id"], "fingerprint": finding["fingerprint"], "changes": {"src/agent_hub/cli.py": "VALUE = 2\n"}})
            _, proposal = draft(proposal_adapter, root, finding)
            self.assertIsNotNone(proposal)
            tasks = root / "runtime" / "tasks"; state = root / "runtime" / "state"; tasks.mkdir(parents=True); state.mkdir()
            ledger = TaskLedger(tasks); task = ledger.create_task("approved-stale-e2e", "personal")
            digest = __import__("agent_hub.multiagent_improvement", fromlist=["change_sha256"]).change_sha256(proposal["changes"])
            for role, provider in (("codex-main", "codex"), ("claude-main", "claude")):
                run_review(ledger, task["task_id"], role, proposal["changes"], FakeAdapter(provider, {"candidate_sha256": digest, "review_decision": "pass"}), root)
            receipt = validate_ledger_review(ledger, task["task_id"], proposal["changes"])
            queue = ImprovementQueue(state / "improvements.json")
            observer = ImprovementObserver(state / "observer.json", queue, SandboxImprovementRunner(Path(sys.executable)), root, root / "backups", ledger)
            candidate_id = observer.observe("quality", finding["summary"], finding["evidence"], proposal["changes"], receipt)
            approval_subject = observer._load()["observations"][candidate_id]["approval_subject"]
            ledger.record_approval(task["task_id"], approval_subject, "approved")

            # Edit the source after review/approval: the before-hashes the observer
            # recorded at observe() time are now stale.  The runner only learns the
            # staleness because the observer forwards item["before_sha256"].
            protected.write_text("VALUE = 3\n", encoding="utf-8")
            outcome = observer.run_once(candidate_id)

            self.assertEqual("failed", outcome["status"])
            # The stale-source refusal (not the missing-approval refusal) proves
            # BOTH args were honoured: execution cleared the approved-path gate and
            # then tripped the expected_before check.
            self.assertIn("Source changed after review", outcome["diagnostic"])
            self.assertEqual("VALUE = 3\n", protected.read_text(encoding="utf-8"))
