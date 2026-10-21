from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import json
import io
import tarfile

from agent_hub.backup import create_backup, restore_backup, verify_backup


class BackupTests(unittest.TestCase):
    def test_new_backup_contains_verified_hub_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "hub"
            (root / "config").mkdir(parents=True)
            (root / "runtime" / "knowledge").mkdir(parents=True)
            (root / "config" / "hub.json").write_text("{}")
            (root / "runtime" / "knowledge" / "note.md").write_text("note")
            archive = create_backup(root, Path(temporary_directory) / "backup.tar.gz")
            self.assertTrue(verify_backup(archive))
            with self.assertRaises(ValueError):
                create_backup(root, archive)

    def test_restore_preview_and_apply_create_a_safety_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            root = base / "hub"
            (root / "config").mkdir(parents=True)
            (root / "runtime" / "knowledge").mkdir(parents=True)
            (root / "runtime" / "state").mkdir(parents=True)
            (root / "config" / "hub.json").write_text("original config")
            note = root / "runtime" / "knowledge" / "note.md"
            note.write_text("original knowledge")
            archive = create_backup(root, base / "snapshot.tar.gz")
            (root / "config" / "hub.json").write_text("changed config")
            note.write_text("changed knowledge")

            preview = restore_backup(archive, root)
            self.assertFalse(preview["applied"])
            self.assertEqual("changed config", (root / "config" / "hub.json").read_text())

            applied = restore_backup(archive, root, apply=True)
            self.assertTrue(applied["applied"])
            self.assertEqual("original config", (root / "config" / "hub.json").read_text())
            self.assertEqual("original knowledge", note.read_text())
            self.assertTrue(verify_backup(Path(applied["safety_backup"])))

    def test_verifier_rejects_path_traversal_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "unsafe.tar.gz"
            payload = b"secret"
            manifest = json.dumps({"files": {"../escape": "bad"}}).encode()
            with tarfile.open(archive_path, "w:gz") as archive:
                for name, contents in (("../escape", payload), ("manifest.json", manifest)):
                    info = tarfile.TarInfo(name)
                    info.size = len(contents)
                    archive.addfile(info, io.BytesIO(contents))
            self.assertFalse(verify_backup(archive_path))

    def test_verifier_rejects_windows_drive_member_with_valid_digest(self) -> None:
        import hashlib

        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "drive.tar.gz"
            payload = b"payload"
            # On Windows root.joinpath("config", "D:", "evil") resolves to "D:evil".
            name = "config/D:/evil"
            manifest = json.dumps({"files": {name: hashlib.sha256(payload).hexdigest()}}).encode()
            with tarfile.open(archive_path, "w:gz") as archive:
                for member, contents in ((name, payload), ("manifest.json", manifest)):
                    info = tarfile.TarInfo(member)
                    info.size = len(contents)
                    archive.addfile(info, io.BytesIO(contents))
            self.assertFalse(verify_backup(archive_path))

    def test_backup_destination_inside_snapshotted_state_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "config").mkdir()
            (root / "config" / "hub.json").write_text("{}", encoding="utf-8")
            (root / "runtime" / "state").mkdir(parents=True)
            (root / "runtime" / "state" / "s.json").write_text("x" * 100_000, encoding="utf-8")
            # Previously this produced an archive that captured itself and failed verification.
            with self.assertRaises(ValueError):
                create_backup(root, root / "runtime" / "state" / "self.tar.gz")
            self.assertFalse((root / "runtime" / "state" / "self.tar.gz").exists())

    def test_verifier_rejects_malformed_archive_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "malformed.tar.gz"
            archive_path.write_bytes(b"not a tar archive")
            self.assertFalse(verify_backup(archive_path))

    @unittest.skipIf(__import__("os").name == "nt", "symlink creation may require elevated Windows privileges")
    def test_restore_refuses_symlinked_runtime_backup_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            root = base / "hub"
            (root / "runtime" / "knowledge").mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            (root / "runtime" / "backups").symlink_to(outside, target_is_directory=True)
            archive = create_backup(root, base / "snapshot.tar.gz")
            with self.assertRaises(ValueError):
                restore_backup(archive, root, apply=True)
            self.assertEqual([], list(outside.iterdir()))
