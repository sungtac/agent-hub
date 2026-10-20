from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_hub.discovery import source_fingerprint


class DiscoveryTests(unittest.TestCase):
    def test_fingerprint_changes_for_source_and_test_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "agent_hub"
            tests = root / "tests"
            source.mkdir(parents=True); tests.mkdir()
            (source / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            (tests / "test_value.py").write_text("assert True\n", encoding="utf-8")
            initial = source_fingerprint(root)
            (tests / "test_value.py").write_text("assert False\n", encoding="utf-8")
            self.assertNotEqual(initial, source_fingerprint(root))
            (source / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
            self.assertNotEqual(initial, source_fingerprint(root))
