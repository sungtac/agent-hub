from __future__ import annotations

import unittest
import tempfile
import os
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from agent_hub.providers import ExecutionResult
from agent_hub.providers import ProviderCapability
from agent_hub.ledger import TaskLedger
from agent_hub.routing import ProviderRouter, RouteDecision
from agent_hub.subscription_langchain import ApprovalGuardedSubscriptionChat, MAX_RESPONSE_CHARACTERS, SubscriptionBridgePolicy, SubscriptionBridgeUnavailable, SubscriptionChatModel, SubscriptionDispatch, SubscriptionExecutionService, SubscriptionTextResponse, load_subscription_bridge_policy, normalise_subscription_text, save_subscription_bridge_policy, subscription_bridge_policy


class FakeAdapter:
    def __init__(self, provider: str, status: str = "succeeded") -> None:
        self.provider = provider
        self.status = status
        self.calls = 0

    def discover(self) -> ProviderCapability:
        return ProviderCapability(self.provider, "available", self.provider, "fake", None)

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(self.provider, self.status, 0, f"{self.provider}:{prompt}", (), "private diagnostic")


class RecordingService:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def invoke(self, **kwargs: object) -> SubscriptionDispatch:
        self.requests.append(kwargs)
        return SubscriptionDispatch(
            route=RouteDecision("claude", "claude", False, "test"),
            response=SubscriptionTextResponse("claude", "text response"),
        )


class SubscriptionBridgePolicyTests(unittest.TestCase):
    def test_absent_policy_is_disabled_and_rejects_dispatch(self) -> None:
        policy = subscription_bridge_policy()
        self.assertFalse(policy.enabled)
        self.assertFalse(policy.allow_tools)
        with self.assertRaises(SubscriptionBridgeUnavailable):
            policy.require_dispatchable("codex")

    def test_enabled_policy_has_an_explicit_provider_allowlist(self) -> None:
        policy = subscription_bridge_policy(
            {
                "enabled": True,
                "allowed_providers": ["claude", "antigravity"],
                "max_calls_per_task": 2,
                "timeout_seconds": 180,
            }
        )
        policy.require_dispatchable("claude")
        with self.assertRaises(SubscriptionBridgeUnavailable):
            policy.require_dispatchable("codex")

    def test_runtime_policy_is_absent_by_default_and_persists_only_explicit_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state = Path(temporary_directory)
            self.assertFalse(load_subscription_bridge_policy(state).enabled)
            policy = subscription_bridge_policy({"enabled": True, "allowed_providers": ["claude"], "max_calls_per_task": 2})
            saved = save_subscription_bridge_policy(state, policy)
            if os.name != "nt":
                self.assertEqual(0o600, saved.stat().st_mode & 0o777)
            loaded = load_subscription_bridge_policy(state)
            self.assertTrue(loaded.enabled)
            self.assertEqual(frozenset({"claude"}), loaded.allowed_providers)
            self.assertEqual(2, loaded.max_calls_per_task)

    def test_policy_rejects_unsafe_or_ambiguous_input(self) -> None:
        with self.assertRaises(ValueError):
            subscription_bridge_policy({"enabled": True, "allow_tools": True})
        with self.assertRaises(ValueError):
            subscription_bridge_policy({"enabled": True, "allowed_providers": ["unknown"]})
        with self.assertRaises(ValueError):
            subscription_bridge_policy({"enabled": True, "allowed_providers": "codex"})
        with self.assertRaises(ValueError):
            subscription_bridge_policy({"enabled": True, "max_calls_per_task": True})
        with self.assertRaises(ValueError):
            subscription_bridge_policy({"enabled": True, "timeout_seconds": 601})
        with self.assertRaises(ValueError):
            subscription_bridge_policy({"enabled": "true"})
        with self.assertRaises(ValueError):
            subscription_bridge_policy({"enabled": True, "timeout_seconds": "120"})
        with self.assertRaises(ValueError):
            subscription_bridge_policy({"credential": "must-not-be-accepted"})

    def test_policy_constructor_validates_direct_use_too(self) -> None:
        with self.assertRaises(ValueError):
            SubscriptionBridgePolicy(allowed_providers=frozenset())
        with self.assertRaises(ValueError):
            SubscriptionBridgePolicy(allowed_providers=("codex",))  # type: ignore[arg-type]

    def test_normalises_only_bounded_successful_provider_text(self) -> None:
        for provider in ("codex", "claude", "antigravity"):
            result = ExecutionResult(provider, "succeeded", 0, "  concise reply  ", ({"private": "event"},), "private diagnostic")
            response = normalise_subscription_text(result)
            self.assertEqual(provider, response.provider)
            self.assertEqual("concise reply", response.text)
        with self.assertRaises(SubscriptionBridgeUnavailable):
            normalise_subscription_text(ExecutionResult("claude", "limited", 1, "retry", (), None))
        with self.assertRaises(SubscriptionBridgeUnavailable):
            normalise_subscription_text(ExecutionResult("codex", "succeeded", 0, "", (), None))
        with self.assertRaises(SubscriptionBridgeUnavailable):
            normalise_subscription_text(ExecutionResult("unknown", "succeeded", 0, "text", (), None))
        with self.assertRaises(SubscriptionBridgeUnavailable):
            normalise_subscription_text(ExecutionResult("codex", "succeeded", 0, "x" * (MAX_RESPONSE_CHARACTERS + 1), (), None))

    def test_execution_service_reuses_router_and_ledger_without_exposing_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tasks = root / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("subscription text", "personal")
            claude = FakeAdapter("claude")
            service = SubscriptionExecutionService(
                ledger,
                ProviderRouter(root / "health.json"),
                {"claude": claude},
                subscription_bridge_policy({"enabled": True, "allowed_providers": ["claude"]}),
            )
            dispatch = service.invoke(task_id=task["task_id"], prompt="reply", working_directory=root, requested_provider="claude")
            self.assertEqual("claude", dispatch.route.provider)
            self.assertEqual("claude:reply", dispatch.response.text)
            self.assertEqual(1, claude.calls)
            snapshot = ledger.get_task(task["task_id"])
            self.assertIsNone(snapshot["lease"])
            self.assertEqual("succeeded", snapshot["executions"][0]["status"])
            self.assertEqual("subscription-langchain", snapshot["dispatches"][0]["channel"])
            self.assertNotIn("private diagnostic", str(snapshot))

    def test_execution_service_cannot_bypass_disabled_policy_or_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tasks = root / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("blocked subscription text", "personal")
            codex = FakeAdapter("codex")
            disabled = SubscriptionExecutionService(ledger, ProviderRouter(root / "health.json"), {"codex": codex}, subscription_bridge_policy())
            with self.assertRaises(SubscriptionBridgeUnavailable):
                disabled.invoke(task_id=task["task_id"], prompt="reply", working_directory=root, requested_provider="codex")
            enabled = SubscriptionExecutionService(
                ledger,
                ProviderRouter(root / "other-health.json"),
                {"codex": codex},
                subscription_bridge_policy({"enabled": True, "allowed_providers": ["claude"]}),
            )
            with self.assertRaises(SubscriptionBridgeUnavailable):
                enabled.invoke(task_id=task["task_id"], prompt="reply", working_directory=root, requested_provider="codex")
            self.assertEqual(0, codex.calls)

    def test_execution_service_filters_fallback_to_allowed_providers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tasks = root / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("limited subscription text", "personal")
            claude = FakeAdapter("claude", "limited")
            codex = FakeAdapter("codex")
            service = SubscriptionExecutionService(
                ledger,
                ProviderRouter(root / "health.json"),
                {"claude": claude, "codex": codex},
                subscription_bridge_policy({"enabled": True, "allowed_providers": ["claude"]}),
            )
            with self.assertRaises(SubscriptionBridgeUnavailable):
                service.invoke(task_id=task["task_id"], prompt="reply", working_directory=root, requested_provider="claude")
            self.assertEqual(1, claude.calls)
            self.assertEqual(0, codex.calls)

    def test_execution_service_budget_survives_restart_and_allows_one_routed_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tasks = root / "tasks"
            tasks.mkdir()
            ledger = TaskLedger(tasks)
            task = ledger.create_task("handoff subscription text", "personal")
            claude = FakeAdapter("claude", "limited")
            codex = FakeAdapter("codex")
            policy = subscription_bridge_policy({"enabled": True, "allowed_providers": ["claude", "codex"]})
            service = SubscriptionExecutionService(ledger, ProviderRouter(root / "health.json"), {"claude": claude, "codex": codex}, policy)
            dispatch = service.invoke(task_id=task["task_id"], prompt="reply", working_directory=root, requested_provider="claude")
            self.assertEqual("codex", dispatch.response.provider)
            self.assertEqual(1, claude.calls)
            self.assertEqual(1, codex.calls)
            restarted = SubscriptionExecutionService(TaskLedger(tasks), ProviderRouter(root / "other-health.json"), {"codex": codex}, policy)
            with self.assertRaises(SubscriptionBridgeUnavailable):
                restarted.invoke(task_id=task["task_id"], prompt="duplicate", working_directory=root, requested_provider="codex")
            self.assertEqual(1, codex.calls)

    def test_chat_facade_supports_only_text_invoke(self) -> None:
        service = RecordingService()
        model = SubscriptionChatModel(service=service, task_id="task-123", working_directory=Path.cwd(), requested_provider="claude")
        response = model.invoke([HumanMessage(content="  hello  ")])
        self.assertEqual("text response", response.content)
        self.assertEqual("claude", response.response_metadata["provider"])
        self.assertEqual("human: hello", service.requests[0]["prompt"])
        with self.assertRaises(SubscriptionBridgeUnavailable):
            model.bind_tools([])
        with self.assertRaises(SubscriptionBridgeUnavailable):
            model.stream("hello")
        with self.assertRaises(SubscriptionBridgeUnavailable):
            model.invoke([HumanMessage(content="hello")], stop=["."])
        with self.assertRaises(SubscriptionBridgeUnavailable):
            model.invoke([AIMessage(content="", tool_calls=[{"name": "unsafe", "args": {}, "id": "call-1"}])])

    def test_approval_guard_blocks_subscription_chat_until_restart_safe_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            service = RecordingService()
            model = SubscriptionChatModel(service=service, task_id="task-guarded", working_directory=root)
            with ApprovalGuardedSubscriptionChat(model, root / "approval.sqlite") as guarded:
                self.assertIn("__interrupt__", guarded.start("subscription-thread", "task-guarded", 1, "reply safely"))
            with ApprovalGuardedSubscriptionChat(model, root / "approval.sqlite") as resumed:
                completed = resumed.resume_and_invoke("subscription-thread", "approved", [HumanMessage(content="reply")])
                repeated = resumed.resume_and_invoke("subscription-thread", "approved", [HumanMessage(content="reply")])
            self.assertTrue(completed["model_invoked"])
            self.assertEqual("text response", completed["message"].content)
            self.assertEqual(1, len(service.requests))
            self.assertFalse(repeated["model_invoked"])
