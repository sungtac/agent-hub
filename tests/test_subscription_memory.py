from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_hub.memory_candidates import MemoryCandidateStore
from agent_hub.ledger import TaskLedger
from agent_hub.providers import ExecutionResult, ProviderCapability
from agent_hub.routing import ProviderRouter
from agent_hub.subscription_memory import antigravity_memory_adapter, extract_routed_subscription_candidates, extract_subscription_candidates


class FakeAdapter:
    def __init__(self, status: str, payload: object) -> None:
        self.status = status
        self.payload = payload

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float) -> ExecutionResult:
        self.prompt = prompt
        return ExecutionResult("antigravity", self.status, 0 if self.status == "succeeded" else 1, json.dumps(self.payload), (), None)


class RoutedFakeAdapter(FakeAdapter):
    def __init__(self, provider: str, status: str, payload: object) -> None:
        super().__init__(status, payload)
        self.provider = provider

    def discover(self) -> ProviderCapability:
        return ProviderCapability(self.provider, "available", self.provider, "fake", None)

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float) -> ExecutionResult:
        return ExecutionResult(self.provider, self.status, 0 if self.status == "succeeded" else 1, json.dumps(self.payload), (), None)


class ConcatenatedJsonAdapter(FakeAdapter):
    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float) -> ExecutionResult:
        candidate = json.dumps(self.payload, ensure_ascii=False)
        summary = json.dumps({"candidates": [], "toolAction": "summary"})
        return ExecutionResult("antigravity", self.status, 0, candidate + "\n" + summary, (), None)


class SubscriptionMemoryTests(unittest.TestCase):
    def test_antigravity_memory_adapter_requires_the_readonly_wrapper(self) -> None:
        with patch("agent_hub.providers.shutil.which", return_value="/opt/agy-readonly-review") as locate:
            adapter = antigravity_memory_adapter()
        self.assertEqual([("agy-readonly-review",)], [item.args for item in locate.call_args_list])
        self.assertEqual("/opt/agy-readonly-review", adapter.executable)

    def test_explicit_safe_structured_candidate_is_staged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = MemoryCandidateStore(root / "candidates.json")
            adapter = FakeAdapter("succeeded", {"candidates": [{"kind": "semantic", "content": "Prefer concise Korean responses.", "confidence": 0.9, "evidence": "User requested concise replies."}]})
            result, candidates = extract_subscription_candidates(
                adapter, trigger="explicit-request", scope="personal", task_id="task-123", evidence="owner requested memory", messages=[{"role": "user", "content": "Please stay concise."}], candidate_store=store, working_directory=root,
            )
            self.assertEqual("succeeded", result.status)
            self.assertEqual(1, len(candidates))
            self.assertEqual([], store.pending("company"))

    def test_first_strict_candidate_document_is_used_before_antigravity_tool_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = MemoryCandidateStore(root / "candidates.json")
            adapter = ConcatenatedJsonAdapter("succeeded", {"candidates": [{"kind": "episodic", "content": "Keep Korean output concise.", "confidence": 0.9, "evidence": "owner preference"}]})
            _, candidates = extract_subscription_candidates(
                adapter, trigger="explicit-request", scope="personal", task_id="task-123", evidence="owner request", messages=[{"role": "user", "content": "Use Korean."}], candidate_store=store, working_directory=root,
            )
            self.assertEqual(1, len(candidates))

    def test_trigger_sensitive_input_bad_json_and_limit_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = MemoryCandidateStore(root / "candidates.json")
            adapter = FakeAdapter("succeeded", {"not_candidates": []})
            kwargs = dict(adapter=adapter, scope="personal", task_id="task-123", evidence="evidence", messages=[{"role": "user", "content": "safe"}], candidate_store=store, working_directory=root)
            with self.assertRaises(PermissionError):
                extract_subscription_candidates(trigger="automatic", **kwargs)
            with self.assertRaises(ValueError):
                extract_subscription_candidates(trigger="task-completed", **kwargs)
            with self.assertRaises(PermissionError):
                extract_subscription_candidates(trigger="explicit-request", **{**kwargs, "messages": [{"role": "user", "content": "api_key=sk_abcdefghijklmnopqrstuv"}]})
            limited = FakeAdapter("limited", {"candidates": []})
            with self.assertRaises(Exception):
                extract_subscription_candidates(adapter=limited, trigger="explicit-request", **{key: value for key, value in kwargs.items() if key != "adapter"})

    def test_limited_provider_hands_off_under_same_task_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tasks = root / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("Memory handoff", "personal")
            store = MemoryCandidateStore(root / "candidates.json")
            adapters = {
                "antigravity": RoutedFakeAdapter("antigravity", "limited", {"candidates": []}),
                "codex": RoutedFakeAdapter("codex", "succeeded", {"candidates": [{"kind": "semantic", "content": "Use Korean.", "confidence": 0.9, "evidence": "request"}]}),
            }
            decision, result, candidates = extract_routed_subscription_candidates(
                ledger, ProviderRouter(root / "health.json"), adapters, requested_provider="antigravity", trigger="explicit-request", scope="personal", task_id=task["task_id"], evidence="owner request", messages=[{"role": "user", "content": "Use Korean"}], candidate_store=store, working_directory=root,
            )
            self.assertEqual("codex", decision.provider)
            self.assertEqual("codex", result.provider)
            self.assertEqual(1, len(candidates))
            self.assertEqual(["antigravity", "codex"], [item["provider"] for item in ledger.get_task(task["task_id"])["executions"]])
