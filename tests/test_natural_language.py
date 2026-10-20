from __future__ import annotations

import unittest

from agent_hub.natural_language import execute_safe, interpret


class NaturalLanguageTests(unittest.TestCase):
    def test_safe_scheduler_and_review_intents_are_distinguished(self) -> None:
        enabled = interpret("자가개선 켜줘")
        self.assertEqual("improvement_enable", enabled.intent)
        self.assertTrue(enabled.safe_to_execute)
        review = interpret("점검 리스트 만들어줘", "operations")
        self.assertEqual("review_checklist", review.intent)
        self.assertFalse(review.safe_to_execute)
        self.assertIn("project_root", review.required)

    def test_memory_and_ambiguous_requests_do_not_execute(self) -> None:
        memory = interpret("이 대화 기억해줘")
        self.assertEqual("memory_extract", memory.intent)
        self.assertFalse(memory.safe_to_execute)
        self.assertEqual(("task_id", "scope", "message"), memory.required)
        self.assertIn("--message", memory.next_command)
        self.assertEqual("guidance", interpret("알아서 해줘").intent)
        self.assertEqual("skill_manual", interpret("스킬 설명서 보여줘").intent)
        self.assertEqual("skill_catalog", interpret("스킬 목록 보여줘").intent)
        self.assertEqual("provider_switch", interpret("코덱스를 오케스트레이터로 전환해줘").intent)

    def test_subscription_bridge_toggle_is_explicit_and_reversible(self) -> None:
        enabled = interpret("구독형 LangChain 연동 켜줘")
        disabled = interpret("구독형 브리지 비활성화해줘")
        self.assertEqual("subscription_bridge_enable", enabled.intent)
        self.assertTrue(enabled.safe_to_execute)
        self.assertEqual("subscription_bridge_disable", disabled.intent)
        self.assertTrue(disabled.safe_to_execute)

    def test_only_reversible_intent_can_execute(self) -> None:
        called: list[str] = []
        result = execute_safe(interpret("자가개선 켜줘"), lambda: called.append("enable"), lambda: called.append("disable"))
        self.assertTrue(result["applied"])
        self.assertEqual(["enable"], called)
        with self.assertRaises(PermissionError):
            execute_safe(interpret("이 대화 기억해줘"), lambda: None, lambda: None)
