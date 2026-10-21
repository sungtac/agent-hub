from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_hub.improvement import ImprovementQueue
from agent_hub.sandbox import SandboxImprovementRunner


@unittest.skipUnless(shutil.which("bwrap"), "bubblewrap is required for isolated sandbox tests")
class SandboxImprovementTests(unittest.TestCase):
    def make_project(self, root: Path) -> None:
        source = root / "src" / "agent_hub"
        tests = root / "tests"
        source.mkdir(parents=True)
        tests.mkdir()
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
        (tests / "test_value.py").write_text(
            "from agent_hub.value import VALUE\n"
            "import unittest\n"
            "class ValueTest(unittest.TestCase):\n"
            " def test_value_is_positive(self): self.assertGreater(VALUE, 0)\n",
            encoding="utf-8",
        )

    def test_passing_change_is_backed_up_and_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "project"
            root.mkdir()
            self.make_project(root)
            runner = SandboxImprovementRunner(Path(__import__("sys").executable))
            result = runner.run(
                root,
                "candidate-abc",
                "quality",
                {"src/agent_hub/value.py": "VALUE = 2\n"},
                root / "backups",
            )
            self.assertEqual("promoted", result.status)
            self.assertEqual("VALUE = 2\n", (root / "src/agent_hub/value.py").read_text(encoding="utf-8"))
            self.assertEqual("VALUE = 1\n", (Path(result.backup_directory) / "src/agent_hub/value.py").read_text(encoding="utf-8"))

    def test_rollback_restores_only_unchanged_promoted_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "project"
            root.mkdir()
            self.make_project(root)
            (root / "state").mkdir()
            queue = ImprovementQueue(root / "state" / "improvements.json")
            candidate = queue.propose("quality", "improve value", "regression evidence")
            runner = SandboxImprovementRunner(Path(__import__("sys").executable))
            promoted = runner.run(root, candidate.identifier, "quality", {"src/agent_hub/value.py": "VALUE = 2\n"}, root / "backups", queue)
            rolled_back = runner.rollback(root, Path(promoted.backup_directory), queue)
            self.assertEqual("rolled_back", rolled_back.status)
            self.assertEqual("VALUE = 1\n", (root / "src/agent_hub/value.py").read_text(encoding="utf-8"))
            with self.assertRaises(PermissionError):
                queue.propose("quality", "repeat immediately", "same area after rollback")

    def test_failing_change_is_not_promoted_and_protected_files_are_denied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "project"
            root.mkdir()
            self.make_project(root)
            runner = SandboxImprovementRunner(Path(__import__("sys").executable))
            result = runner.run(
                root,
                "candidate-fail",
                "quality",
                {"src/agent_hub/value.py": "VALUE = -1\n"},
                root / "backups",
            )
            self.assertEqual("rejected", result.status)
            self.assertEqual("VALUE = 1\n", (root / "src/agent_hub/value.py").read_text(encoding="utf-8"))
            # Risk is determined by the changed path, not a caller-supplied area label.
            self.assertEqual("promoted", runner.run(root, "candidate-policy", "policy", {"src/agent_hub/value.py": "VALUE=2"}, root / "backups").status)
            with self.assertRaises(ValueError):
                runner.run(root, "../outside", "quality", {"src/agent_hub/value.py": "VALUE=2"}, root / "backups")

    def test_daily_promotion_limit_restores_candidate_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "project"
            root.mkdir()
            self.make_project(root)
            (root / "state").mkdir()
            queue = ImprovementQueue(root / "state" / "improvements.json", max_promotions_per_day=1)
            runner = SandboxImprovementRunner(Path(__import__("sys").executable))
            first = queue.propose("quality", "small improvement one", "measured result")
            first_result = runner.run(root, first.identifier, "quality", {"src/agent_hub/value.py": "VALUE = 2\n"}, root / "backups", queue)
            self.assertEqual("promoted", first_result.status)
            second = queue.propose("quality", "small improvement two", "second measured result")
            second_result = runner.run(root, second.identifier, "quality", {"src/agent_hub/value.py": "VALUE = 3\n"}, root / "backups", queue)
            self.assertEqual("deferred", second_result.status)
            self.assertEqual("VALUE = 2\n", (root / "src/agent_hub/value.py").read_text(encoding="utf-8"))

    def test_source_change_during_sandbox_run_is_rejected_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "project"; root.mkdir(); self.make_project(root)
            target = root / "src" / "agent_hub" / "value.py"

            class MutatingRunner(SandboxImprovementRunner):
                calls = 0
                def _run_tests(self, workspace: Path):
                    self.calls += 1
                    if self.calls == 2:
                        target.write_text("VALUE = 99\n", encoding="utf-8")
                    return subprocess.CompletedProcess([], 0, "", "")

            runner = MutatingRunner(Path(__import__("sys").executable))
            before = {"src/agent_hub/value.py": runner._sha256(target)}
            with self.assertRaises(PermissionError):
                runner.run(root, "candidate-race", "quality", {"src/agent_hub/value.py": "VALUE = 2\n"}, root / "backups", expected_before=before)
            self.assertEqual("VALUE = 99\n", target.read_text(encoding="utf-8"))


class ProtectedPathPolicyTests(unittest.TestCase):
    def test_non_canonical_spelling_cannot_bypass_protected_file_approval(self) -> None:
        from agent_hub.sandbox import requires_approval

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src" / "agent_hub").mkdir(parents=True)
            (root / "src" / "agent_hub" / "cli.py").write_text("VALUE = 1\n", encoding="utf-8")
            for name in ("src/agent_hub//cli.py", "src/agent_hub/./cli.py"):
                self.assertTrue(requires_approval({name: "VALUE = 2\n"}))
                with self.assertRaises(PermissionError):
                    SandboxImprovementRunner._validate_changes(root, {name: "VALUE = 2\n"}, frozenset({name}))

    def test_improvement_trust_path_requires_owner_approval(self) -> None:
        from agent_hub.sandbox import requires_approval

        for module in ("multiagent_improvement", "improvement_review", "observer", "discovery", "provider_gateway", "scheduler", "subscription_policy", "natural_language", "file_lock"):
            self.assertTrue(requires_approval({f"src/agent_hub/{module}.py": ""}), module)
        self.assertFalse(requires_approval({"src/agent_hub/value.py": ""}))

    def test_package_initialiser_requires_owner_approval(self) -> None:
        from agent_hub.sandbox import requires_approval

        # __init__.py executes on every agent_hub import and can monkeypatch
        # any protected module, so it must never auto-promote.
        self.assertTrue(requires_approval({"src/agent_hub/__init__.py": "import agent_hub.sandbox\n"}))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src" / "agent_hub").mkdir(parents=True)
            (root / "src" / "agent_hub" / "__init__.py").write_text("", encoding="utf-8")
            with self.assertRaises(PermissionError):
                SandboxImprovementRunner._validate_changes(root, {"src/agent_hub/__init__.py": "x = 1\n"})
