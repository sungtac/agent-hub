from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_hub.review import build_manifest, write_new_manifest


class ReviewManifestTests(unittest.TestCase):
    def test_manifest_accounts_for_included_and_excluded_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "source.py").write_text("print('ok')\n", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("private\n", encoding="utf-8")
            (root / "generated.pyc").write_bytes(b"bytecode")
            (root / "runtime").mkdir()
            (root / "runtime" / "private-task.json").write_text("private", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "dependency.py").write_text("generated", encoding="utf-8")
            (root / ".agent-hub").mkdir()
            (root / ".agent-hub" / "overlay.md").write_text("generated", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "generated.py").write_text("generated", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"\0binary")
            manifest = build_manifest(root)
            by_path = {item["path"]: item for item in manifest["files"]}
            self.assertEqual("included", by_path["source.py"]["disposition"])
            self.assertIn("sha256", by_path["source.py"])
            self.assertEqual("excluded", by_path[".git/"]["disposition"])
            self.assertNotIn(".git/config", by_path)
            self.assertEqual("excluded", by_path["runtime/"]["disposition"])
            self.assertNotIn("runtime/private-task.json", by_path)
            self.assertEqual("excluded", by_path[".venv/"]["disposition"])
            self.assertNotIn(".venv/dependency.py", by_path)
            self.assertEqual("excluded", by_path[".agent-hub/"]["disposition"])
            self.assertNotIn(".agent-hub/overlay.md", by_path)
            self.assertEqual("excluded", by_path["build/"]["disposition"])
            self.assertNotIn("build/generated.py", by_path)
            self.assertEqual("excluded", by_path["generated.pyc"]["disposition"])
            self.assertEqual("binary_file", by_path["binary.bin"]["reason"])

    def test_manifest_write_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "source.py").write_text("x", encoding="utf-8")
            output = root / "manifest.json"
            write_new_manifest(build_manifest(root), output)
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["read_only"])
            with self.assertRaises(FileExistsError):
                write_new_manifest(build_manifest(root), output)
