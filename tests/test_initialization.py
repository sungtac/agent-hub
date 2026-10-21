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


class FeatureSimulationCoverageTests(unittest.TestCase):
    def test_feature_simulation_runs_every_test_module(self) -> None:
        import ast

        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "scripts" / "verify_feature_simulation.py").read_text(encoding="utf-8"))
        listed = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("tests.test_")}
        present = {f"tests.{path.stem}" for path in (root / "tests").glob("test_*.py")}
        self.assertEqual(set(), present - listed)
