from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from agent_hub.workflow import ApprovalWorkflow


class ApprovalWorkflowTests(unittest.TestCase):
    def test_approval_interrupt_resumes_from_sqlite_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkpoint = Path(temporary_directory) / "workflow.sqlite"
            with ApprovalWorkflow(checkpoint) as workflow:
                paused = workflow.start("thread-1", "task-1", 2, "write report")
                self.assertIn("__interrupt__", paused)
                if os.name != "nt":
                    self.assertEqual(0o600, checkpoint.stat().st_mode & 0o777)
            with ApprovalWorkflow(checkpoint) as resumed_workflow:
                completed = resumed_workflow.resume("thread-1", "approved")
            self.assertEqual("completed", completed["status"])
            self.assertEqual("task-1:2", completed["receipt_key"])

    def test_rejection_never_records_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkpoint = Path(temporary_directory) / "workflow.sqlite"
            with ApprovalWorkflow(checkpoint) as workflow:
                workflow.start("thread-2", "task-2", 1, "sensitive change")
                rejected = workflow.resume("thread-2", "rejected")
            self.assertEqual("rejected", rejected["status"])
            self.assertNotIn("receipt_key", rejected)

    def test_completed_thread_cannot_be_resumed_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkpoint = Path(temporary_directory) / "workflow.sqlite"
            with ApprovalWorkflow(checkpoint) as workflow:
                workflow.start("thread-repeat", "task-repeat", 1, "one action")
                workflow.resume("thread-repeat", "approved")
            with ApprovalWorkflow(checkpoint) as restarted:
                repeated = restarted.resume("thread-repeat", "approved")
            self.assertTrue(repeated["already_finalized"])
            self.assertEqual("completed", repeated["status"])

    def test_invalid_decision_is_rejected_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with ApprovalWorkflow(Path(temporary_directory) / "workflow.sqlite") as workflow:
                with self.assertRaises(ValueError):
                    workflow.resume("thread", "maybe")
