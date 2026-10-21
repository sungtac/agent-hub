from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_hub.scheduler import MARKER, ObserverScheduler


class FakeCron:
    def __init__(self, initial: str = "MAILTO=owner@example.test\n") -> None:
        self.payload = initial

    def __call__(self, command, **kwargs):
        if command == ["crontab", "-l"]:
            return subprocess.CompletedProcess(command, 0, self.payload, "")
        if command == ["crontab", "-"]:
            self.payload = kwargs["input"]
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)


class ObserverSchedulerTests(unittest.TestCase):
    def test_enable_and_disable_preserve_other_crontab_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            executable = root / "agent-hub"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            cron = FakeCron()
            scheduler = ObserverScheduler(root, executable, cron)
            self.assertFalse(scheduler.status())
            scheduler.enable(10)
            self.assertTrue(scheduler.status())
            self.assertIn("MAILTO=owner@example.test", cron.payload)
            self.assertIn("*/10", cron.payload)
            self.assertIn(MARKER, cron.payload)
            self.assertTrue(scheduler.disable())
            self.assertFalse(scheduler.status())
            self.assertIn("MAILTO=owner@example.test", cron.payload)

    def test_interval_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            executable = root / "agent-hub"
            executable.write_text("", encoding="utf-8")
            scheduler = ObserverScheduler(root, executable, FakeCron())
            with self.assertRaises(ValueError):
                scheduler.enable(4)


class CrontabPathSafetyTests(unittest.TestCase):
    def test_paths_that_break_crontab_syntax_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            for name in ("hub\n* * * * * touch injected", "hub%evil"):
                root = Path(temporary_directory) / name
                root.mkdir()
                executable = root / "agent-hub"
                executable.write_text("#!/bin/sh\n", encoding="utf-8")
                cron = FakeCron("")
                with self.assertRaises(ValueError):
                    ObserverScheduler(root, executable, cron).enable(10)
                # Nothing outside the tagged Hub lines may ever be written.
                self.assertTrue(all(MARKER in line or "agent-hub-improvement-discovery" in line for line in cron.payload.splitlines()))
