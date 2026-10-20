from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent_hub.cli import main


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_silently(arguments: list[str]) -> int:
    with redirect_stdout(io.StringIO()):
        return main(arguments)


class InitializationIsolationTests(unittest.TestCase):
    def test_initialization_does_not_modify_external_provider_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            hub_root = base / "hub"
            provider_config = base / "provider-config.toml"
            hub_root.mkdir()
            provider_config.write_text('model = "existing"\n', encoding="utf-8")
            before = digest(provider_config)
            self.assertEqual(0, run_silently(["init", "--hub-root", str(hub_root)]))
            self.assertEqual(before, digest(provider_config))

    def test_initialization_is_idempotent_at_cli_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.assertEqual(0, run_silently(["init", "--hub-root", str(root)]))
            config = root / "config/hub.json"
            first = digest(config)
            self.assertEqual(0, run_silently(["init", "--hub-root", str(root)]))
            self.assertEqual(first, digest(config))
