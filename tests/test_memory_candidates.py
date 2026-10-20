from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_hub.knowledge import KnowledgeStore
from agent_hub.memory_candidates import MemoryCandidateStore, extract_langmem_candidates


class FakeMemoryManager:
    def invoke(self, request, config):
        self.request = request
        self.config = config
        return [
            {"namespace": ("agent-hub", "local-owner", "personal"), "key": "first", "value": {"kind": "semantic", "content": "Prefer concise Korean release notes."}},
            {"namespace": ("agent-hub", "local-owner", "personal"), "key": "second", "value": {"kind": "semantic", "content": "Prefer concise Korean release notes."}},
        ]


class MemoryCandidateTests(unittest.TestCase):
    def test_langmem_output_is_staged_deduplicated_and_scope_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = root / "runtime"
            runtime.mkdir()
            knowledge_root = runtime / "knowledge"
            knowledge_root.mkdir()
            queue = MemoryCandidateStore(runtime / "candidates.json")
            manager = FakeMemoryManager()
            results = extract_langmem_candidates(
                manager,
                [{"role": "user", "content": "Use concise Korean notes."}],
                "local-owner",
                "personal",
                "task-123",
                "repeated preference in user request",
                queue,
            )
            self.assertEqual(2, len(results))
            self.assertEqual(results[0]["candidate_id"], results[1]["candidate_id"])
            self.assertEqual("local-owner", manager.config["configurable"]["langgraph_user_id"])
            self.assertEqual(1, len(queue.pending("personal")))
            queue = MemoryCandidateStore(runtime / "candidates.json")
            self.assertEqual(1, len(queue.pending("personal")))
            self.assertEqual([], queue.pending("company"))
            self.assertEqual([], KnowledgeStore(knowledge_root).search("personal", "concise"))

    def test_only_explicit_promotion_writes_live_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = root / "runtime"
            runtime.mkdir()
            knowledge_root = runtime / "knowledge"
            knowledge_root.mkdir()
            queue = MemoryCandidateStore(runtime / "candidates.json")
            candidates = queue.ingest("personal", "task-123", "owner confirmed this preference", [{"kind": "procedural", "content": {"rule": "Run tests before handoff"}}])
            knowledge = KnowledgeStore(knowledge_root)
            path = queue.promote(candidates[0]["candidate_id"], knowledge)
            self.assertTrue(path.is_file())
            self.assertEqual([], queue.pending("personal"))
            self.assertEqual(1, len(knowledge.search("personal", "tests")))
            with self.assertRaises(ValueError):
                queue.promote(candidates[0]["candidate_id"], knowledge)

    def test_rejection_is_terminal_and_invalid_scope_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            queue = MemoryCandidateStore(root / "candidates.json")
            candidates = queue.ingest("company", "task-456", "documented decision", [{"content": "Keep a changelog"}])
            self.assertEqual("rejected", queue.reject(candidates[0]["candidate_id"])["status"])
            self.assertEqual([], queue.pending("company"))
            with self.assertRaises(ValueError):
                queue.ingest("personal-company", "task-456", "evidence", [{"content": "x"}])
