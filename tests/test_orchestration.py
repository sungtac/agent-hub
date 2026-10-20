from __future__ import annotations

import stat
import tempfile
import unittest
import os
from pathlib import Path

from agent_hub.ledger import TaskLedger
from agent_hub.orchestration import run_codex_task, run_routed_task
from agent_hub.providers import CodexAdapter, ExecutionResult, ProviderCapability
from agent_hub.routing import ProviderRouter


class FakeAdapter:
    def __init__(self, provider: str, status: str = "succeeded", diagnostic: str | None = None) -> None:
        self.provider = provider
        self.status = status
        self.diagnostic = diagnostic

    def discover(self) -> ProviderCapability:
        return ProviderCapability(self.provider, "available", self.provider, "fake", None)

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
        return ExecutionResult(self.provider, self.status, 0, "done", (), self.diagnostic)


class OrchestrationTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX script fixture is not a native Windows executable")
    def test_execution_is_recorded_and_lease_is_released(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            script = directory / "fake-codex.py"
            script.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "if '--version' in sys.argv: print('fake'); raise SystemExit(0)\n"
                "pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text('done')\n",
                encoding="utf-8",
            )
            if os.name == "nt":
                executable = directory / "fake-codex.cmd"
                executable.write_text(f'@echo off\n"{os.sys.executable}" "%~dp0fake-codex.py" %*\n', encoding="utf-8")
            else:
                executable = directory / "fake-codex"
                script.rename(executable)
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Run", "personal")
            result = run_codex_task(ledger, task["task_id"], "ready", directory, adapter=CodexAdapter(str(executable)))
            snapshot = ledger.get_task(task["task_id"])
            self.assertEqual("succeeded", result.status)
            self.assertIsNone(snapshot["lease"])
            self.assertEqual("succeeded", snapshot["executions"][0]["status"])

    def test_role_transfer_uses_ledger_boundary_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Transfer", "personal")
            router = ProviderRouter(directory / "provider-health.json")
            decision, result = run_routed_task(
                ledger,
                router,
                {"codex": FakeAdapter("codex"), "claude": FakeAdapter("claude")},
                task["task_id"],
                "continue from ledger",
                directory,
                requested_provider="claude",
            )
            snapshot = ledger.get_task(task["task_id"])
            self.assertEqual("claude", decision.provider)
            self.assertEqual("claude", result.provider)
            self.assertEqual("claude", snapshot["executions"][0]["provider"])
            self.assertIsNone(snapshot["lease"])

    def test_usage_limit_hands_off_same_task_to_available_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Limited handoff", "personal")
            router = ProviderRouter(directory / "provider-health.json")
            decision, result = run_routed_task(
                ledger,
                router,
                {"claude": FakeAdapter("claude", "limited"), "codex": FakeAdapter("codex")},
                task["task_id"],
                "continue safely",
                directory,
                requested_provider="claude",
            )
            snapshot = ledger.get_task(task["task_id"])
            self.assertEqual("codex", decision.provider)
            self.assertTrue(decision.fallback)
            self.assertEqual("codex", result.provider)
            self.assertEqual(["claude", "codex"], [item["provider"] for item in snapshot["executions"]])
            self.assertIsNone(snapshot["lease"])

    def test_antigravity_quota_limit_hands_off_to_codex_until_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Antigravity quota handoff", "personal")
            router = ProviderRouter(directory / "provider-health.json")
            decision, result = run_routed_task(
                ledger,
                router,
                {
                    "antigravity": FakeAdapter("antigravity", "limited", "Individual quota reached. Resets in 6d18h41s."),
                    "codex": FakeAdapter("codex"),
                },
                task["task_id"],
                "continue safely",
                directory,
                requested_provider="antigravity",
            )
            self.assertEqual("codex", decision.provider)
            self.assertTrue(decision.fallback)
            self.assertEqual("codex", result.provider)
            self.assertEqual(["antigravity", "codex"], [item["provider"] for item in ledger.get_task(task["task_id"])["executions"]])

    def test_known_antigravity_limit_uses_codex_even_for_strict_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Known Antigravity limit", "personal")
            router = ProviderRouter(directory / "provider-health.json")
            router.record_result("antigravity", "limited", cooldown_seconds=3_600)
            decision, result = run_routed_task(
                ledger,
                router,
                {"antigravity": FakeAdapter("antigravity"), "codex": FakeAdapter("codex")},
                task["task_id"],
                "continue safely",
                directory,
                requested_provider="antigravity",
                allow_handoff=False,
            )
            self.assertEqual("codex", decision.provider)
            self.assertTrue(decision.fallback)
            self.assertEqual("codex", result.provider)
            self.assertEqual(["codex"], [item["provider"] for item in ledger.get_task(task["task_id"])["executions"]])

    def test_when_every_provider_is_limited_original_limit_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("All limited", "personal")
            router = ProviderRouter(directory / "provider-health.json")
            decision, result = run_routed_task(
                ledger,
                router,
                {"claude": FakeAdapter("claude", "limited"), "codex": FakeAdapter("codex", "limited")},
                task["task_id"],
                "continue safely",
                directory,
                requested_provider="claude",
            )
            snapshot = ledger.get_task(task["task_id"])
            self.assertEqual("limited", result.status)
            self.assertEqual("codex", decision.provider)
            self.assertEqual(2, len(snapshot["executions"]))
            self.assertIsNone(snapshot["lease"])

    def test_network_unavailable_hands_off_to_another_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Network handoff", "personal")
            decision, result = run_routed_task(
                ledger,
                ProviderRouter(directory / "provider-health.json"),
                {"claude": FakeAdapter("claude", "unavailable"), "antigravity": FakeAdapter("antigravity")},
                task["task_id"],
                "resume from durable ledger",
                directory,
                requested_provider="claude",
            )
            self.assertEqual("antigravity", decision.provider)
            self.assertEqual("succeeded", result.status)
            self.assertEqual(["claude", "antigravity"], [item["provider"] for item in ledger.get_task(task["task_id"])["executions"]])

    def test_empty_provider_response_hands_off_and_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Empty review handoff", "personal")
            decision, result = run_routed_task(
                ledger,
                ProviderRouter(directory / "provider-health.json"),
                {"claude": FakeAdapter("claude", "empty"), "antigravity": FakeAdapter("antigravity")},
                task["task_id"],
                "review from durable ledger",
                directory,
                requested_provider="claude",
            )
            self.assertEqual("antigravity", decision.provider)
            self.assertEqual("succeeded", result.status)
            self.assertEqual(["empty", "succeeded"], [item["status"] for item in ledger.get_task(task["task_id"])["executions"]])

    def test_strict_independent_review_never_substitutes_another_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Strict reviewer", "personal")
            decision, result = run_routed_task(
                ledger,
                ProviderRouter(directory / "provider-health.json"),
                {"claude": FakeAdapter("claude", "empty"), "codex": FakeAdapter("codex")},
                task["task_id"],
                "independent review",
                directory,
                requested_provider="claude",
                allow_handoff=False,
            )
            self.assertEqual("claude", decision.provider)
            self.assertEqual("empty", result.status)
            self.assertEqual(["claude"], [item["provider"] for item in ledger.get_task(task["task_id"])["executions"]])

    def test_adapter_registry_rejects_provider_alias_mismatch(self) -> None:
        class MismatchedAdapter(FakeAdapter):
            def __init__(self) -> None:
                super().__init__("agy")

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            tasks = directory / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Reject mismatch", "personal")
            router = ProviderRouter(directory / "provider-health.json")
            with self.assertRaises(RuntimeError):
                run_routed_task(ledger, router, {"antigravity": MismatchedAdapter()}, task["task_id"], "ready", directory)
