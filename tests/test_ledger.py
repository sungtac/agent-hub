from __future__ import annotations

import json
import io
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_hub.ledger import DispatchLimitError, DuplicateDispatchError, LedgerError, LeaseConflictError, StaleLeaseError, TaskLedger
from agent_hub.cli import main
from agent_hub.configuration import initialise_hub


class TaskLedgerTests(unittest.TestCase):
    def make_ledger(self) -> tuple[tempfile.TemporaryDirectory[str], TaskLedger]:
        temporary_directory = tempfile.TemporaryDirectory()
        root = Path(temporary_directory.name) / "tasks"
        root.mkdir(mode=0o700)
        return temporary_directory, TaskLedger(root)

    def test_events_are_authoritative_when_snapshot_is_missing(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Read-only review", "personal")
        task_dir = ledger.tasks_root / created["task_id"]
        (task_dir / "task.json").unlink()
        restored = ledger.get_task(created["task_id"])
        self.assertEqual("Read-only review", restored["title"])
        self.assertTrue((task_dir / "task.json").is_file())

    def test_torn_final_append_does_not_brick_the_task(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Crash during append", "personal")
        events = ledger.tasks_root / created["task_id"] / "events.jsonl"
        # A crash mid-write leaves an unterminated fragment (here ending inside
        # a multi-byte character); it was never acknowledged to any caller.
        with events.open("ab") as stream:
            stream.write('{"event_type": "approval_recorded", "subject": "\uc6d0'.encode("utf-8")[:-1])
        self.assertEqual(1, ledger.get_task(created["task_id"])["event_count"])
        approved = ledger.record_approval(created["task_id"], "deploy", "approved")
        self.assertEqual(2, approved["event_count"])
        self.assertTrue(events.read_bytes().endswith(b"\n"))
        self.assertEqual(2, len(events.read_bytes().splitlines()))

    def test_lease_generation_rejects_stale_release(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Provider switch", "company")
        leased = ledger.acquire_lease(created["task_id"], "codex", "orchestrator")
        lease = leased["lease"]
        with self.assertRaises(LeaseConflictError):
            ledger.acquire_lease(created["task_id"], "claude", "orchestrator")
        released = ledger.release_lease(created["task_id"], lease["lease_id"], lease["generation"], "handoff")
        self.assertIsNone(released["lease"])
        with self.assertRaises(StaleLeaseError):
            ledger.release_lease(created["task_id"], lease["lease_id"], lease["generation"], "late writer")

    def test_restart_requires_explicit_grace_period_lease_recovery(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Interrupted provider", "personal")
        leased = ledger.acquire_lease(created["task_id"], "claude", "orchestrator")
        # Simulates a new process reading only the append-only ledger after a crash.
        restarted = TaskLedger(ledger.tasks_root)
        granted = datetime.fromisoformat(leased["lease"]["granted_at"])
        with self.assertRaises(LeaseConflictError):
            restarted.recover_stale_lease(created["task_id"], 60, at=granted + timedelta(seconds=59))
        recovered = restarted.recover_stale_lease(created["task_id"], 60, at=granted + timedelta(seconds=60))
        self.assertIsNone(recovered["lease"])
        handoff = restarted.acquire_lease(created["task_id"], "codex", "orchestrator")
        self.assertEqual("codex", handoff["lease"]["provider"])
        events = (ledger.tasks_root / created["task_id"] / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"event_type": "lease_recovered"', events)

    def test_approvals_are_append_only_events(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Approval", "quarantine")
        approved = ledger.record_approval(created["task_id"], "write:report", "approved")
        event_path = ledger.tasks_root / created["task_id"] / "events.jsonl"
        events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual("approval_recorded", events[-1]["event_type"])
        self.assertEqual("approved", approved["approvals"][0]["decision"])
        with self.assertRaises(LedgerError):
            ledger.record_approval(created["task_id"], " ", "approved")

    def test_replay_rejects_wrong_generation_release(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Tamper detection", "personal")
        leased = ledger.acquire_lease(created["task_id"], "codex", "orchestrator")
        task_dir = ledger.tasks_root / created["task_id"]
        with (task_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event_type": "lease_released", "lease_id": leased["lease"]["lease_id"], "generation": 99}) + "\n")
        with self.assertRaises(LedgerError):
            ledger.get_task(created["task_id"])

    def test_execution_record_requires_active_matching_lease(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Execution", "personal")
        leased = ledger.acquire_lease(created["task_id"], "codex", "orchestrator")
        lease = leased["lease"]
        result = ledger.record_execution(created["task_id"], lease["lease_id"], lease["generation"], "codex", "succeeded", 0)
        self.assertEqual("succeeded", result["executions"][0]["status"])
        with self.assertRaises(StaleLeaseError):
            ledger.record_execution(created["task_id"], lease["lease_id"], lease["generation"] + 1, "codex", "failed", 1)

    def test_review_receipt_requires_matching_successful_live_execution(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Attested review", "personal")
        leased = ledger.acquire_lease(created["task_id"], "codex", "codex-main")
        lease = leased["lease"]
        digest = "a" * 64
        with self.assertRaises(LedgerError):
            ledger.record_review_receipt(created["task_id"], lease["lease_id"], lease["generation"], "codex-main", "codex", digest, "pass", "b" * 64)
        ledger.record_execution(created["task_id"], lease["lease_id"], lease["generation"], "codex", "succeeded", 0)
        recorded = ledger.record_review_receipt(created["task_id"], lease["lease_id"], lease["generation"], "codex-main", "codex", digest, "pass", "b" * 64)
        self.assertEqual("pass", recorded["review_receipts"][0]["decision"])

    def test_dispatch_reservation_is_durable_and_bounded(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Bounded dispatch", "personal")
        reserved = ledger.reserve_dispatch(created["task_id"], "subscription-langchain", 1)
        self.assertEqual("subscription-langchain", reserved["dispatches"][0]["channel"])
        restarted = TaskLedger(ledger.tasks_root)
        with self.assertRaises(DispatchLimitError):
            restarted.reserve_dispatch(created["task_id"], "subscription-langchain", 1)

    def test_external_dispatch_receipt_is_unique_and_completable(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Loadout worker", "personal")
        fingerprint = "a" * 64
        receipt = ledger.reserve_external_dispatch(created["task_id"], "loadout-worker", 2, fingerprint, "codex")
        self.assertEqual("reserved", receipt["task"]["external_dispatches"][0]["status"])
        with self.assertRaises(DuplicateDispatchError):
            ledger.reserve_external_dispatch(created["task_id"], "loadout-worker", 2, fingerprint, "codex")
        completed = ledger.complete_external_dispatch(created["task_id"], receipt["dispatch_id"], "succeeded", 0)
        self.assertEqual("succeeded", completed["external_dispatches"][0]["status"])

    def test_task_completion_requires_no_active_or_unreceipted_work(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Verified completion", "personal")
        receipt = ledger.reserve_external_dispatch(created["task_id"], "loadout-worker", 1, "b" * 64, "claude")
        with self.assertRaises(LedgerError):
            ledger.complete_task(created["task_id"], "tests passed")
        ledger.complete_external_dispatch(created["task_id"], receipt["dispatch_id"], "succeeded", 0)
        completed = ledger.complete_task(created["task_id"], "tests passed; reviewer receipts recorded")
        self.assertEqual("completed", completed["status"])
        with self.assertRaises(LedgerError):
            ledger.acquire_lease(created["task_id"], "codex", "orchestrator")

    def test_concurrent_lease_attempts_have_one_winner(self) -> None:
        temporary_directory, ledger = self.make_ledger()
        self.addCleanup(temporary_directory.cleanup)
        created = ledger.create_task("Concurrent", "personal")
        outcomes: list[str] = []

        def attempt(provider: str) -> None:
            try:
                ledger.acquire_lease(created["task_id"], provider, "orchestrator")
                outcomes.append("acquired")
            except LeaseConflictError:
                outcomes.append("conflict")

        first = threading.Thread(target=attempt, args=("codex",))
        second = threading.Thread(target=attempt, args=("claude",))
        first.start()
        second.start()
        first.join()
        second.join()
        self.assertCountEqual(["acquired", "conflict"], outcomes)

    def test_cli_task_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = io.StringIO()
            with redirect_stdout(output):
                initialise_hub(root)
                self.assertEqual(0, main(["task", "--hub-root", str(root), "create", "--title", "CLI", "--scope", "personal"]))
            created = json.loads(output.getvalue())
            self.assertEqual("CLI", created["title"])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    0,
                    main(
                        [
                            "task",
                            "--hub-root",
                            str(root),
                            "lease-acquire",
                            created["task_id"],
                            "--provider",
                            "codex",
                            "--role",
                            "orchestrator",
                        ]
                    ),
                )
            self.assertEqual("codex", json.loads(output.getvalue())["lease"]["provider"])
