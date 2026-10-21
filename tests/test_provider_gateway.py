from __future__ import annotations

import tempfile
import unittest
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from agent_hub.cli import main
from agent_hub.configuration import initialise_hub
from agent_hub.ledger import DispatchLimitError, LedgerError, TaskLedger
from agent_hub.provider_gateway import ProviderGateway
from agent_hub.providers import ExecutionResult, ProviderCapability
from agent_hub.routing import ProviderRouter


class FakeAdapter:
    def __init__(self, provider: str, status: str = "succeeded") -> None:
        self.provider = provider
        self.status = status
        self.calls = 0

    def discover(self) -> ProviderCapability:
        return ProviderCapability(self.provider, "available", self.provider, "test", None)

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(self.provider, self.status, 0, "result", (), None)


class ProviderGatewayTests(unittest.TestCase):
    def test_shared_handoff_is_saved_and_used_by_next_provider(self) -> None:
        class SharedAdapter(FakeAdapter):
            def __init__(self, provider: str, record: dict[str, str]) -> None:
                super().__init__(provider)
                self.record = record
                self.prompts: list[str] = []

            def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
                self.prompts.append(prompt)
                return ExecutionResult(self.provider, 'succeeded', 0, json.dumps(self.record), (), None)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialise_hub(root)
            ledger = TaskLedger(root / 'runtime/tasks')
            task_id = ledger.create_task('shared handoff', 'quarantine')['task_id']
            first = dict(goal='sort', criteria='sorted unique', completed='initial check', verification='failed', remaining='correct', next_action='sort values')
            second = dict(goal='sort', criteria='sorted unique', completed='sorted', verification='passed', remaining='none', next_action='none')
            claude, codex = SharedAdapter('claude', first), SharedAdapter('codex', second)
            def switch(provider: str, switch_id: str) -> int:
                return main(['natural', '--hub-root', str(root), '--text', 'codex 오케스트레이터', '--apply', '--task-id', task_id, '--provider', provider, '--instruction', 'continue safely', '--switch-id', switch_id, '--shared-handoff', '--strict-provider'])
            with patch('agent_hub.cli.claude_adapter', return_value=claude), patch('agent_hub.cli.CodexAdapter', return_value=codex), patch('agent_hub.cli.antigravity_adapter', return_value=FakeAdapter('antigravity')), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, switch('claude', 'first'))
                self.assertEqual(0, switch('codex', 'second'))
            state = TaskLedger(root / 'runtime/tasks').get_task(task_id)
            self.assertEqual([first, second], [x['record'] for x in state['handoffs']])
            self.assertIn('sort values', codex.prompts[0])
            self.assertIsNone(state['lease'])

    def test_invalid_shared_output_does_not_persist_or_retain_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialise_hub(root)
            ledger = TaskLedger(root / 'runtime/tasks')
            task_id = ledger.create_task('invalid handoff', 'quarantine')['task_id']
            with patch('agent_hub.cli.claude_adapter', return_value=FakeAdapter('claude')), patch('agent_hub.cli.CodexAdapter', return_value=FakeAdapter('codex')), patch('agent_hub.cli.antigravity_adapter', return_value=FakeAdapter('antigravity')):
                with self.assertRaises(SystemExit):
                    main(['natural', '--hub-root', str(root), '--text', 'codex 오케스트레이터', '--apply', '--task-id', task_id, '--provider', 'claude', '--instruction', 'continue', '--switch-id', 'first', '--shared-handoff', '--strict-provider'])
            state = ledger.get_task(task_id)
            self.assertEqual([], state['handoffs'])
            self.assertIsNone(state['lease'])

    def test_sensitive_shared_record_is_rejected_under_active_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = root / 'tasks'
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task_id = ledger.create_task('sensitive check', 'quarantine')['task_id']
            lease = ledger.acquire_lease(task_id, 'claude', 'orchestrator')['lease']
            ledger.record_execution(task_id, lease['lease_id'], lease['generation'], 'claude', 'succeeded', 0)
            record = dict(goal='test', criteria='test', completed='test', verification='test', remaining='none', next_action='password=example')
            with self.assertRaises(LedgerError):
                ledger.record_handoff(task_id, lease['lease_id'], lease['generation'], 'claude', record)
            self.assertEqual([], ledger.get_task(task_id)['handoffs'])
            ledger.release_lease(task_id, lease['lease_id'], lease['generation'], 'test complete')

    def test_distinct_switch_ids_allow_round_trip_but_reuse_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialise_hub(root)
            ledger = TaskLedger(root / 'runtime/tasks')
            task_id = ledger.create_task('switch test', 'personal')['task_id']
            claude, codex = FakeAdapter('claude'), FakeAdapter('codex')
            def switch(provider: str, switch_id: str) -> int:
                return main(['natural', '--hub-root', str(root), '--text', '코덱스 오케스트레이터', '--apply', '--task-id', task_id, '--provider', provider, '--instruction', 'continue', '--switch-id', switch_id, '--strict-provider'])
            with patch('agent_hub.cli.claude_adapter', return_value=claude), patch('agent_hub.cli.CodexAdapter', return_value=codex), patch('agent_hub.cli.antigravity_adapter', return_value=FakeAdapter('antigravity')), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(0, switch('claude', 'first'))
                self.assertEqual(0, switch('codex', 'second'))
                with self.assertRaises(SystemExit):
                    switch('codex', 'second')
            state = ledger.get_task(task_id)
            self.assertEqual(['claude', 'codex'], [item['provider'] for item in state['executions']])
            self.assertIsNone(state['lease'])
            self.assertEqual(1, codex.calls)

    def test_one_reservation_allows_one_logical_request_and_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tasks = root / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("gateway", "personal")
            claude = FakeAdapter("claude", "limited")
            codex = FakeAdapter("codex")
            gateway = ProviderGateway(ledger, ProviderRouter(root / "health.json"), {"claude": claude, "codex": codex})
            dispatch = gateway.dispatch(task_id=task["task_id"], channel="review", maximum=1, prompt="review", working_directory=root, requested_provider="claude")
            self.assertEqual("codex", dispatch.result.provider)
            self.assertEqual(1, claude.calls)
            self.assertEqual(1, codex.calls)
            with self.assertRaises(DispatchLimitError):
                gateway.dispatch(task_id=task["task_id"], channel="review", maximum=1, prompt="review", working_directory=root, requested_provider="codex")

    def test_sensitive_switch_instruction_is_never_dispatched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialise_hub(root)
            ledger = TaskLedger(root / 'runtime/tasks')
            task_id = ledger.create_task('sensitive instruction', 'personal')['task_id']
            claude, codex = FakeAdapter('claude'), FakeAdapter('codex')
            with patch('agent_hub.cli.claude_adapter', return_value=claude), patch('agent_hub.cli.CodexAdapter', return_value=codex), patch('agent_hub.cli.antigravity_adapter', return_value=FakeAdapter('antigravity')):
                with self.assertRaises(SystemExit):
                    main(['natural', '--hub-root', str(root), '--text', '클로드를 오케스트레이터로 전환해줘', '--apply', '--task-id', task_id, '--provider', 'claude', '--instruction', 'continue with api_key=abc123', '--switch-id', 'one'])
            self.assertEqual(0, claude.calls + codex.calls)
            state = ledger.get_task(task_id)
            self.assertEqual([], state['dispatches'])
            self.assertEqual([], state['executions'])


class GatewayPromptValidationTests(unittest.TestCase):
    def test_refused_prompt_does_not_consume_the_dispatch_budget(self) -> None:
        from agent_hub.providers import CodexAdapter, ProviderExecutionError, claude_adapter

        class Codex(CodexAdapter):
            def discover(self) -> ProviderCapability:
                return ProviderCapability("codex", "available", "codex", "test", None)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialise_hub(root)
            ledger = TaskLedger(root / "runtime/tasks")
            task_id = ledger.create_task("switch", "personal")["task_id"]
            gateway = ProviderGateway(ledger, ProviderRouter(root / "runtime/state/provider-health.json"), {"codex": Codex("/missing/codex"), "claude": claude_adapter("/missing/claude")})
            for prompt in ("--sandbox=danger-full-access", "update"):
                with self.assertRaises(ProviderExecutionError):
                    gateway.dispatch(task_id=task_id, channel="orchestrator-switch", maximum=1, prompt=prompt, working_directory=root, requested_provider="codex")
            self.assertEqual([], ledger.get_task(task_id)["dispatches"])
