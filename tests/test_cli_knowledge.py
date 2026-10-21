from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_hub.cli import main
from agent_hub.configuration import initialise_hub
from agent_hub.memory_candidates import MemoryCandidateStore
from agent_hub.multiagent_improvement import MultiAgentReviewReceipt, change_sha256
from agent_hub.ledger import TaskLedger
from agent_hub.backup import verify_backup
from agent_hub.providers import ExecutionResult
from agent_hub.provider_gateway import GatewayDispatch
from agent_hub.routing import RouteDecision


class KnowledgeCliTests(unittest.TestCase):
    @staticmethod
    def _passing_review_task(root: Path, changes: dict[str, str]) -> str:
        digest = change_sha256(changes)
        for role in ("claude-main", "codex-main"):
            provider = role.removesuffix("-main")
            ledger = TaskLedger(root / "runtime/tasks")
            if role == "claude-main":
                created = ledger.create_task("attested review", "personal")
                task_id = created["task_id"]
            leased = ledger.acquire_lease(task_id, provider, role)
            lease = leased["lease"]
            ledger.record_execution(task_id, lease["lease_id"], lease["generation"], provider, "succeeded", 0)
            ledger.record_review_receipt(task_id, lease["lease_id"], lease["generation"], role, provider, digest, "pass", "b" * 64)
            ledger.release_lease(task_id, lease["lease_id"], lease["generation"], "test")
        return task_id

    def test_subscription_chat_status_and_owner_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            initial = io.StringIO()
            with contextlib.redirect_stdout(initial):
                self.assertEqual(0, main(["subscription-chat", "--hub-root", str(root), "status"]))
            self.assertFalse(json.loads(initial.getvalue())["enabled"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    0,
                    main(
                        [
                            "subscription-chat", "--hub-root", str(root), "set", "--enabled", "true",
                            "--provider", "claude", "--max-calls-per-task", "2", "--timeout-seconds", "90",
                        ]
                    ),
                )
            enabled = io.StringIO()
            with contextlib.redirect_stdout(enabled):
                self.assertEqual(0, main(["subscription-chat", "--hub-root", str(root), "status"]))
            status = json.loads(enabled.getvalue())
            self.assertTrue(status["enabled"])
            self.assertEqual(["claude"], status["allowed_providers"])
            self.assertEqual(2, status["max_calls_per_task"])

    def test_auto_once_runs_only_the_candidate_created_in_that_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory); initialise_hub(root)
            changes = {"src/agent_hub/value.py": "VALUE = 2\n"}
            finding = {"finding_id": "finding-1", "fingerprint": "f" * 64, "summary": "new", "evidence": "evidence", "area": "quality"}
            proposal = {"proposal_id": "proposal-1", "finding_id": "finding-1", "fingerprint": "f" * 64, "changes": changes}

            class FakeObserver:
                def __init__(self, *unused): self.run_arguments = []
                def observe(self, *unused): return "new-candidate"
                def run_once(self, candidate_id=None):
                    self.run_arguments.append(candidate_id)
                    return {"candidate_id": candidate_id, "status": "promoted"}

            observer = FakeObserver()
            with (
                patch("agent_hub.cli.source_fingerprint", return_value="f" * 64),
                patch("agent_hub.cli.CodexAdapter", return_value=object()),
                patch("agent_hub.cli.claude_adapter", return_value=object()),
                patch("agent_hub.cli.discover", return_value=(ExecutionResult("codex", "succeeded", 0, "{}", (), None), finding)),
                patch("agent_hub.cli.draft", return_value=(ExecutionResult("claude", "succeeded", 0, "{}", (), None), proposal)),
                patch("agent_hub.cli.run_missing_reviews"),
                patch("agent_hub.cli.validate_ledger_review", return_value=MultiAgentReviewReceipt("task-placeholder", change_sha256(changes), ("claude-main", "codex-main"))),
                patch("agent_hub.cli.ImprovementObserver", return_value=observer),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, main(["improvement", "--hub-root", str(root), "auto-once", "--timeout-seconds", "1"]))
            self.assertEqual(["new-candidate"], observer.run_arguments)
    def test_knowledge_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["knowledge", "--hub-root", str(root), "save", "--scope", "personal", "--id", "cli-note", "--content", "hello world"]))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["knowledge", "--hub-root", str(root), "search", "--scope", "personal", "--query", "hello"]))
            self.assertIn("cli-note", output.getvalue())

    def test_memory_candidate_owner_review_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            candidate_store = MemoryCandidateStore(root / "runtime" / "state" / "memory-candidates.json")
            candidate = candidate_store.ingest("personal", "task-789", "owner reviewed preference", [{"kind": "semantic", "content": "Keep responses concise"}])[0]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["memory-candidate", "--hub-root", str(root), "list", "--scope", "personal"]))
            self.assertIn(candidate["candidate_id"], output.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["memory-candidate", "--hub-root", str(root), "promote", "--id", candidate["candidate_id"]]))
            self.assertEqual([], candidate_store.pending("personal"))

    def test_backup_cli_preview_and_explicit_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "hub"
            root.mkdir()
            initialise_hub(root)
            note = root / "runtime" / "knowledge" / "note.md"
            note.write_text("snapshot value")
            archive = Path(temporary_directory) / "snapshot.tar.gz"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["backup", "--hub-root", str(root), "create", "--output", str(archive)]))
            self.assertTrue(verify_backup(archive))
            note.write_text("current value")

            preview = io.StringIO()
            with contextlib.redirect_stdout(preview):
                self.assertEqual(0, main(["backup", "--hub-root", str(root), "restore", "--archive", str(archive)]))
            self.assertIn('"applied": false', preview.getvalue())
            self.assertEqual("current value", note.read_text())

            applied = io.StringIO()
            with contextlib.redirect_stdout(applied):
                self.assertEqual(0, main(["backup", "--hub-root", str(root), "restore", "--archive", str(archive), "--apply"]))
            self.assertEqual("snapshot value", note.read_text())
            self.assertIn('"applied": true', applied.getvalue())

    def test_task_lease_recovery_rejects_non_stale_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["task", "--hub-root", str(root), "create", "--title", "CLI recovery", "--scope", "personal"]))
            task_id = __import__("json").loads(output.getvalue())["task_id"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["task", "--hub-root", str(root), "lease-acquire", task_id, "--provider", "codex", "--role", "orchestrator"]))
            with self.assertRaises(SystemExit):
                main(["task", "--hub-root", str(root), "lease-recover", task_id, "--stale-after-seconds", "60"])

    def test_gateway_cli_creates_and_completes_external_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            created = io.StringIO()
            with contextlib.redirect_stdout(created):
                self.assertEqual(0, main(["task", "--hub-root", str(root), "create", "--title", "Loadout bridge", "--scope", "personal"]))
            task_id = json.loads(created.getvalue())["task_id"]
            reserved = io.StringIO()
            with contextlib.redirect_stdout(reserved):
                self.assertEqual(0, main(["gateway", "--hub-root", str(root), "reserve-external", "--task-id", task_id, "--channel", "loadout-worker", "--fingerprint", "c" * 64, "--provider", "codex"]))
            dispatch_id = json.loads(reserved.getvalue())["dispatch_id"]
            completed = io.StringIO()
            with contextlib.redirect_stdout(completed):
                self.assertEqual(0, main(["gateway", "--hub-root", str(root), "complete-external", "--task-id", task_id, "--dispatch-id", dispatch_id, "--status", "succeeded", "--return-code", "0"]))
            self.assertEqual("succeeded", json.loads(completed.getvalue())["external_dispatches"][0]["status"])

    def test_loadout_bind_task_creates_one_way_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "hub"
            project = Path(temporary_directory) / "loadout"
            task_file = project / "tasks" / "bridge-test" / "task.md"
            root.mkdir()
            task_file.parent.mkdir(parents=True)
            task_file.write_text("```yaml\nagent_hub_task_id: N/A\n```\n", encoding="utf-8")
            initialise_hub(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["loadout", "--hub-root", str(root), "bind-task", "--project-root", str(project), "--task-name", "bridge-test", "--title", "Bridge test", "--scope", "personal"]))
            linked = json.loads(output.getvalue())
            self.assertIn(linked["task_id"], task_file.read_text(encoding="utf-8"))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["loadout", "--hub-root", str(root), "bind-task", "--project-root", str(project), "--task-name", "bridge-test", "--title", "Bridge test", "--scope", "personal"])

    def test_loadout_legacy_migration_and_managed_ledger_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "hub"
            project = Path(temporary_directory) / "loadout"
            task_file = project / "tasks" / "legacy-task" / "task.md"
            root.mkdir()
            task_file.parent.mkdir(parents=True)
            task_file.write_text("# Legacy task\n\n```yaml\nstatus: in_progress\n```\n", encoding="utf-8")
            initialise_hub(root)
            preview = io.StringIO()
            with contextlib.redirect_stdout(preview):
                self.assertEqual(0, main(["loadout", "--hub-root", str(root), "migrate-tasks", "--project-root", str(project), "--scope", "personal"]))
            self.assertEqual("would_bind", json.loads(preview.getvalue())[0]["status"])
            applied = io.StringIO()
            with contextlib.redirect_stdout(applied):
                self.assertEqual(0, main(["loadout", "--hub-root", str(root), "migrate-tasks", "--project-root", str(project), "--scope", "personal", "--apply"]))
            task_id = json.loads(applied.getvalue())[0]["task_id"]
            self.assertIn(task_id, task_file.read_text(encoding="utf-8"))
            synced = io.StringIO()
            with contextlib.redirect_stdout(synced):
                self.assertEqual(0, main(["loadout", "--hub-root", str(root), "sync-task", "--project-root", str(project), "--task-name", "legacy-task"]))
            self.assertEqual(task_id, json.loads(synced.getvalue())["task_id"])
            rendered = task_file.read_text(encoding="utf-8")
            self.assertIn("<!-- agent-hub:ledger:start -->", rendered)
            self.assertIn("Hub status: `active`", rendered)

    def test_loadout_migration_rejects_task_directory_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "hub"
            project = Path(temporary_directory) / "loadout"
            external = Path(temporary_directory) / "external"
            root.mkdir()
            (project / "tasks").mkdir(parents=True)
            external.mkdir()
            (external / "task.md").write_text("# Outside\n\n```yaml\nstatus: pending\n```\n", encoding="utf-8")
            try:
                (project / "tasks" / "escaped").symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest("Symlinks unavailable on this platform")
            initialise_hub(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["loadout", "--hub-root", str(root), "migrate-tasks", "--project-root", str(project), "--scope", "personal"]))
            self.assertEqual("unsafe_path", json.loads(output.getvalue())[0]["status"])

    def test_task_complete_cli_freezes_verified_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            created = io.StringIO()
            with contextlib.redirect_stdout(created):
                self.assertEqual(0, main(["task", "--hub-root", str(root), "create", "--title", "Finish", "--scope", "personal"]))
            task_id = json.loads(created.getvalue())["task_id"]
            completed = io.StringIO()
            with contextlib.redirect_stdout(completed):
                self.assertEqual(0, main(["task", "--hub-root", str(root), "complete", task_id, "--evidence", "verification passed"]))
            self.assertEqual("completed", json.loads(completed.getvalue())["status"])
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["gateway", "--hub-root", str(root), "reserve-external", "--task-id", task_id, "--channel", "loadout-worker", "--fingerprint", "d" * 64, "--provider", "codex"])

    def test_improvement_observation_can_be_queued_without_running_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            changes = root / "changes.json"
            proposal = {"src/agent_hub/example.py": "VALUE = 1\n"}
            changes.write_text(json.dumps(proposal), encoding="utf-8")
            task_id = self._passing_review_task(root, proposal)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["improvement", "--hub-root", str(root), "observe", "--area", "quality", "--summary", "example", "--evidence", "test finding", "--changes-file", str(changes), "--task-id", task_id]))
            self.assertIn("candidate_id", output.getvalue())

    def test_subscription_memory_cli_requires_approved_trigger_before_cli_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            messages = root / "messages.json"
            messages.write_text(json.dumps([{"role": "user", "content": "Remember this"}]), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["memory-candidate", "--hub-root", str(root), "extract-cli", "--scope", "personal", "--task-id", "task-123", "--evidence", "test", "--trigger", "automatic", "--messages-file", str(messages)])

    def test_natural_language_command_returns_safe_execution_plan(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, main(["natural", "--text", "자가개선 켜줘"]))
        self.assertIn("improvement_enable", output.getvalue())

    def test_natural_apply_rejects_memory_extraction_without_inputs(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["natural", "--text", "이 대화 기억해줘", "--apply"])

    def test_natural_review_executes_only_when_required_paths_are_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            report = root / "checklist.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["natural", "--hub-root", str(root), "--text", "점검 리스트 만들어줘", "--apply", "--project-root", str(root), "--output", str(report)]))
            self.assertTrue(report.is_file())
            self.assertIn('"applied": true', output.getvalue())

    def test_natural_memory_requires_task_scope_and_message_before_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["natural", "--hub-root", str(root), "--text", "이 대화 기억해줘", "--apply"])

    def test_natural_orchestrator_switch_uses_same_task_and_requested_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            created = io.StringIO()
            with contextlib.redirect_stdout(created):
                self.assertEqual(0, main(["task", "--hub-root", str(root), "create", "--title", "Natural switch", "--scope", "personal"]))
            task_id = json.loads(created.getvalue())["task_id"]
            output = io.StringIO()
            decision = RouteDecision("claude", "claude", False, "requested provider is available")
            execution = ExecutionResult("claude", "succeeded", 0, "continued", (), None)
            with patch("agent_hub.cli.ProviderGateway.dispatch", return_value=GatewayDispatch(decision, execution)) as dispatched:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, main([
                        "natural", "--hub-root", str(root), "--text", "클로드를 오케스트레이터로 전환해줘", "--apply",
                        "--task-id", task_id, "--provider", "claude", "--instruction", "원장을 읽고 계속 진행해줘.", "--provider-timeout-seconds", "30",
                    ]))
            self.assertEqual(task_id, dispatched.call_args.kwargs["task_id"])
            self.assertEqual("claude", dispatched.call_args.kwargs["requested_provider"])
            self.assertTrue(dispatched.call_args.kwargs["allow_handoff"])
            self.assertIn('"provider": "claude"', output.getvalue())

    def test_natural_orchestrator_switch_rejects_unbounded_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main([
                    "natural", "--hub-root", str(root), "--text", "클로드를 오케스트레이터로 전환해줘", "--apply",
                    "--task-id", "task-0123456789abcdef0123456789abcdef", "--provider", "claude", "--instruction", "검토", "--provider-timeout-seconds", "121",
                ])

    def test_natural_orchestrator_switch_requires_explicit_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["natural", "--hub-root", str(root), "--text", "클로드를 오케스트레이터로 전환해줘", "--apply", "--task-id", "task-0123456789abcdef0123456789abcdef", "--provider", "claude"])

    def test_shared_skill_registry_cli_registers_searches_and_writes_manual(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            skill = root / "source-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: safe-review\ndescription: Safely review local code\n---\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["skill", "--hub-root", str(root), "register", "--source", str(skill), "--scope", "shared", "--kind", "portable", "--cost-tier", "low", "--provider", "codex=native"]))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["skill", "--hub-root", str(root), "search", "--query", "review"]))
            self.assertIn("safe-review", output.getvalue())
            manual = root / "skill-manual.md"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["skill", "--hub-root", str(root), "manual", "--output", str(manual)]))
            self.assertIn("스킬 설명서", manual.read_text(encoding="utf-8"))

    def test_shared_skill_registry_cli_accepts_unified_catalog_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialise_hub(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["skill", "--hub-root", str(root), "list", "--all-installed"]))
            self.assertIsInstance(json.loads(output.getvalue()), list)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["skill", "--hub-root", str(root), "catalog"]))
            self.assertIsInstance(json.loads(output.getvalue()), list)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, main(["natural", "--hub-root", str(root), "--text", "스킬 목록 보여줘", "--apply"]))
            result = json.loads(output.getvalue())
            self.assertEqual("skill_catalog", result["intent"])
            self.assertIsInstance(result["skills"], list)


class LoadoutBindConcurrencyTests(unittest.TestCase):
    def test_concurrent_binds_create_exactly_one_hub_task(self) -> None:
        import threading
        import time

        from agent_hub.ledger import TaskLedger
        from agent_hub.loadout_bridge import LoadoutBridgeError, bind_task

        class SlowLedger(TaskLedger):
            def create_task(self, title: str, scope: str) -> dict:
                created = super().create_task(title, scope)
                time.sleep(0.2)
                return created

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "hub-tasks").mkdir()
            task_file = root / "project" / "tasks" / "race" / "task.md"
            task_file.parent.mkdir(parents=True)
            task_file.write_text("```yaml\nagent_hub_task_id: N/A\n```\n", encoding="utf-8")
            ledger = SlowLedger(root / "hub-tasks")
            outcomes: list[str] = []

            def bind() -> None:
                try:
                    outcomes.append(bind_task(ledger, root / "project", "race", "Race", "personal")["task_id"])
                except LoadoutBridgeError:
                    outcomes.append("refused")

            threads = [threading.Thread(target=bind) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(1, outcomes.count("refused"))
            self.assertEqual(1, len(list((root / "hub-tasks").glob("task-*"))))
