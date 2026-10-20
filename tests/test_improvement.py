from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_hub.improvement import ImprovementQueue


class ImprovementQueueTests(unittest.TestCase):
    def test_deduplication_area_is_metadata_and_daily_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            queue = ImprovementQueue(Path(temporary_directory) / "improvement.json", max_promotions_per_day=1)
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            one = queue.propose("tests", "add regression", "failure evidence", now)
            self.assertEqual(one.identifier, queue.propose("tests", "add regression", "failure evidence", now).identifier)
            self.assertEqual("promoted", queue.promote(one.identifier, True, now))
            two = queue.propose("documentation", "clarify", "review evidence", now)
            self.assertEqual("deferred", queue.promote(two.identifier, True, now))
            self.assertEqual("security", queue.propose("security", "review routing", "evidence", now).area)

    def test_failed_checks_reject_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            queue = ImprovementQueue(Path(temporary_directory) / "improvement.json")
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            candidate = queue.propose("tests", "add regression", "evidence", now)
            self.assertEqual("rejected", queue.promote(candidate.identifier, False, now))
            with self.assertRaises(PermissionError):
                queue.propose("tests", "different retry", "same failed area", now)
            self.assertEqual("tests", queue.propose("tests", "retry later", "cooldown elapsed", now.replace(day=2)).area)
