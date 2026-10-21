from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from agent_hub.improvement import ImprovementQueue
from agent_hub.ledger import TaskLedger
from agent_hub.multiagent_improvement import change_sha256, validate_ledger_review
from agent_hub.observer import ImprovementObserver


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def run(self, root: Path, candidate_id: str, area: str, changes: dict[str, str], backup_root: Path, queue: ImprovementQueue, *unused: object):
        self.calls.append((candidate_id, area, changes))
        status = queue.promote(candidate_id, True)
        return SimpleNamespace(status=status, backup_directory=str(backup_root / candidate_id), diagnostic="fake sandbox passed")


def review_receipt(ledger: TaskLedger, changes: dict[str, str]):
    task = ledger.create_task("review", "personal")
    digest = change_sha256(changes)
    for role in ("claude-main", "codex-main"):
        provider = role.removesuffix("-main")
        lease = ledger.acquire_lease(task["task_id"], provider, role)["lease"]
        ledger.record_execution(task["task_id"], lease["lease_id"], lease["generation"], provider, "succeeded", 0)
        ledger.record_review_receipt(task["task_id"], lease["lease_id"], lease["generation"], role, provider, digest, "pass", "b" * 64)
        ledger.release_lease(task["task_id"], lease["lease_id"], lease["generation"], "test")
    return validate_ledger_review(ledger, task["task_id"], changes)


class ImprovementObserverTests(unittest.TestCase):
    def test_observer_persists_and_promotes_one_candidate_at_a_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state = root / "state"
            state.mkdir()
            queue = ImprovementQueue(state / "improvements.json")
            runner = FakeRunner()
            first_changes = {"src/agent_hub/value.py": "VALUE = 2\n"}
            second_changes = {"src/agent_hub/value.py": "VALUE = 3\n"}
            tasks = root / "tasks"; tasks.mkdir()
            ledger = TaskLedger(tasks)
            first_receipt = review_receipt(ledger, first_changes)
            observer = ImprovementObserver(state / "observer.json", queue, runner, root, root / "backups", ledger)
            first = observer.observe("quality", "first", "measured", first_changes, first_receipt)
            second_receipt = review_receipt(ledger, second_changes)
            observer.observe("documentation", "second", "review", second_changes, second_receipt)
            result = observer.run_once()
            self.assertEqual(first, result["candidate_id"])
            self.assertEqual("promoted", result["status"])
            self.assertEqual(1, len(runner.calls))
            self.assertIsNotNone(observer.run_once())
            self.assertIsNone(observer.run_once())

    def test_duplicate_candidate_cannot_replace_recorded_change_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state = root / "state"
            state.mkdir()
            queue = ImprovementQueue(state / "improvements.json")
            first_changes = {"src/agent_hub/value.py": "one"}
            tasks = root / "tasks"; tasks.mkdir()
            ledger = TaskLedger(tasks)
            receipt = review_receipt(ledger, first_changes)
            observer = ImprovementObserver(state / "observer.json", queue, FakeRunner(), root, root / "backups", ledger)
            observer.observe("quality", "same", "same evidence", first_changes, receipt)
            with self.assertRaises(ValueError):
                second_changes = {"src/agent_hub/value.py": "two"}
                second_receipt = review_receipt(ledger, second_changes)
                observer.observe("quality", "same", "same evidence", second_changes, second_receipt)

    def test_stale_running_observation_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state = root / "state"
            state.mkdir()
            queue = ImprovementQueue(state / "improvements.json")
            runner = FakeRunner()
            changes = {"src/agent_hub/value.py": "VALUE = 4\n"}
            tasks = root / "tasks"; tasks.mkdir()
            ledger = TaskLedger(tasks)
            receipt = review_receipt(ledger, changes)
            observer = ImprovementObserver(state / "observer.json", queue, runner, root, root / "backups", ledger)
            identifier = observer.observe("quality", "recover", "interrupted", changes, receipt)
            payload = observer._load()
            payload["observations"][identifier].update(
                {
                    "status": "running",
                    "started_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                }
            )
            observer._write(payload)
            result = observer.run_once()
            self.assertEqual(identifier, result["candidate_id"])
            self.assertEqual("promoted", result["status"])
            self.assertEqual(1, len(runner.calls))

    def test_observe_rejects_forged_or_mismatched_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory); state = root / "state"; state.mkdir()
            changes = {"src/agent_hub/value.py": "VALUE = 2\n"}
            tasks = root / "tasks"; tasks.mkdir()
            ledger = TaskLedger(tasks)
            receipt = review_receipt(ledger, changes)
            observer = ImprovementObserver(state / "observer.json", ImprovementQueue(state / "improvements.json"), FakeRunner(), root, root / "backups", ledger)
            with self.assertRaises(ValueError):
                observer.observe("quality", "forged", "evidence", {"src/agent_hub/value.py": "VALUE = 3\n"}, receipt)

    def test_run_once_with_candidate_id_never_selects_an_older_pending_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory); state = root / "state"; state.mkdir(); tasks = root / "tasks"; tasks.mkdir()
            ledger = TaskLedger(tasks); queue = ImprovementQueue(state / "improvements.json"); runner = FakeRunner()
            observer = ImprovementObserver(state / "observer.json", queue, runner, root, root / "backups", ledger)
            first_changes = {"src/agent_hub/first.py": "ONE = 1\n"}
            second_changes = {"src/agent_hub/second.py": "TWO = 2\n"}
            first = observer.observe("quality", "first", "evidence", first_changes, review_receipt(ledger, first_changes))
            second = observer.observe("quality", "second", "evidence", second_changes, review_receipt(ledger, second_changes))
            result = observer.run_once(second)
            self.assertEqual(second, result["candidate_id"])
            self.assertEqual([(second, "quality", second_changes)], runner.calls)
            self.assertEqual("candidate", queue.get_candidate(first)["status"])

    def test_revoked_approval_is_not_treated_as_approved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory); state = root / "state"; state.mkdir(); tasks = root / "tasks"; tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("review", "personal")
            subject = "improvement-v1:cand:" + "a" * 64
            observer = ImprovementObserver(state / "observer.json", ImprovementQueue(state / "improvements.json"), FakeRunner(), root, root / "backups", ledger)
            item = {"task_id": task["task_id"], "approval_subject": subject}
            ledger.record_approval(task["task_id"], subject, "approved")
            self.assertTrue(observer._approved(item))
            # A later revocation for the same subject must win over the stale approval.
            ledger.record_approval(task["task_id"], subject, "rejected")
            self.assertFalse(observer._approved(item))
            # A fresh re-approval takes effect again.
            ledger.record_approval(task["task_id"], subject, "approved")
            self.assertTrue(observer._approved(item))
