from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_hub.knowledge import KnowledgeStore


class KnowledgeStoreTests(unittest.TestCase):
    def test_scope_and_deletion_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = KnowledgeStore(Path(temporary_directory))
            document = store.save("personal", "note-1", "private fact")
            self.assertIn("scope: personal", document.read_text(encoding="utf-8"))
            tombstone = store.delete("personal", "note-1")
            self.assertTrue(tombstone.is_file())
            with self.assertRaises(PermissionError):
                store.read("personal", "note-1")
            # Deletion must remove the content, not merely rename it.
            leftovers = [path.name for path in Path(temporary_directory).rglob("*") if path.is_file() and b"private fact" in path.read_bytes()]
            self.assertEqual([], leftovers)

    def test_update_search_and_explicit_scope_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = KnowledgeStore(Path(temporary_directory))
            store.save("personal", "note-1", "first private fact")
            second = store.update("personal", "note-1", "improved private fact")
            self.assertEqual("v2.md", second.name)
            result = store.search("personal", "improved")
            self.assertEqual("note-1", result[0]["id"])
            self.assertEqual(2, result[0]["version"])
            self.assertEqual([], store.search("company", "improved"))
            store.promote("personal", "note-1", "shared")
            self.assertIn("improved private fact", store.read("shared", "note-1"))
            store.delete("personal", "note-1")
            self.assertEqual([], store.search("personal", "improved"))
