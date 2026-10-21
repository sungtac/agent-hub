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

    def test_non_object_model_replies_produce_no_finding_or_draft(self) -> None:
        from agent_hub.discovery import discover, draft
        from agent_hub.providers import ExecutionResult

        class Adapter:
            provider = "codex"
            def __init__(self, reply: str) -> None:
                self.reply = reply
            def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
                return ExecutionResult(self.provider, "succeeded", 0, self.reply, (), None)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src" / "agent_hub").mkdir(parents=True)
            (root / "src" / "agent_hub" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.assertIsNone(discover(Adapter('["finding"]'), root)[1])
            finding = {"summary": "s", "evidence": "e", "area": "quality", "fingerprint": source_fingerprint(root), "finding_id": "f"}
            self.assertIsNone(draft(Adapter('"proposal"'), root, finding)[1])

    def test_draft_rejects_non_canonical_change_paths(self) -> None:
        import json
        from agent_hub.discovery import draft
        from agent_hub.providers import ExecutionResult

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src" / "agent_hub").mkdir(parents=True)
            (root / "src" / "agent_hub" / "cli.py").write_text("VALUE = 1\n", encoding="utf-8")
            fingerprint = source_fingerprint(root)
            finding = {"summary": "s", "evidence": "e", "area": "quality", "fingerprint": fingerprint, "finding_id": "f"}
            for name in ("src/agent_hub//cli.py", "src/agent_hub/./cli.py"):
                reply = json.dumps({"finding_id": "f", "fingerprint": fingerprint, "changes": {name: "VALUE = 2\n"}})

                class Adapter:
                    provider = "claude"
                    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
                        return ExecutionResult(self.provider, "succeeded", 0, reply, (), None)

                self.assertIsNone(draft(Adapter(), root, finding)[1])
