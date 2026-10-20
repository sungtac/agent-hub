from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from agent_hub.cli import main
from agent_hub.compatibility import assess, command_statuses, serialise_results, version_at_least
from agent_hub.configuration import CONFIG_RELATIVE_PATH, ConfigurationError, RUNTIME_DIRECTORIES, initialise_hub, load_configuration
from agent_hub.environment import build_report, inspect_path


class EnvironmentInspectionTests(unittest.TestCase):
    def test_existing_writable_directory_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = inspect_path(Path(temporary_directory))
        self.assertEqual("available", result.status)
        self.assertTrue(result.exists)
        self.assertTrue(result.is_directory)

    def test_missing_path_is_reported_without_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / "not-created"
            result = inspect_path(missing)
            self.assertEqual("missing", result.status)
            self.assertFalse(missing.exists())

    def test_report_is_read_only_and_serialisable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            report = build_report(Path(temporary_directory), commands=("definitely-not-a-command",))
        self.assertEqual(1, report["schema_version"])
        self.assertTrue(report["read_only"])
        self.assertEqual("unknown", report["commands"][0]["status"])
        self.assertIn("generated_at", report)

    def test_home_path_is_redacted(self) -> None:
        result = inspect_path(Path.home())
        self.assertEqual("~", result.path)

    def test_explicit_output_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "report.json"
            self.assertEqual(0, main(["environment", "check", "--output", str(output)]))
            self.assertTrue(output.is_file())
            with self.assertRaises(SystemExit):
                main(["environment", "check", "--output", str(output)])

    def test_version_comparison_and_full_profile_gap(self) -> None:
        self.assertTrue(version_at_least("3.12.0", "3.12"))
        self.assertFalse(version_at_least("3.10.12", "3.12"))
        report = build_report(Path.cwd(), commands=("definitely-not-a-provider",))
        results = {item.name: item for item in assess(report)}
        self.assertIn(results["full-profile-python"].status, {"ready", "conditional"})
        self.assertEqual("unknown", results["provider-claude"].status)
        self.assertEqual({}, command_statuses({"commands": "invalid"}))
        self.assertTrue(serialise_results(list(results.values())))

    def test_initialization_creates_private_boundaries_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = initialise_hub(root)
            self.assertEqual(root / CONFIG_RELATIVE_PATH, config_path)
            self.assertEqual("owner-only", load_configuration(root).owner_mode)
            for relative_path in RUNTIME_DIRECTORIES:
                self.assertTrue((root / relative_path).is_dir())
            self.assertEqual(config_path, initialise_hub(root))
            if os.name != "nt":
                for relative_path in RUNTIME_DIRECTORIES:
                    self.assertEqual(0o700, (root / relative_path).stat().st_mode & 0o777)

    def test_initialization_refuses_missing_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / "missing"
            with self.assertRaises(ConfigurationError):
                initialise_hub(missing)

    def test_configuration_rejects_unsafe_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            config_path = root / CONFIG_RELATIVE_PATH
            config_path.write_text(
                '{"schema_version": 1, "owner_mode": "owner-only", "sharing": "disabled", '
                '"default_scope": "quarantine", "runtime_directories": ["../unsafe"]}',
                encoding="utf-8",
            )
            with self.assertRaises(ConfigurationError):
                load_configuration(root)
