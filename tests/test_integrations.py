from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent_hub.integrations import ApprovalGuardedDeepAgent, IntegrationUnavailable, build_approval_guarded_scoped_deep_agent, build_deep_agent, build_memory_manager, build_scoped_deep_agent, configured_memory_model
from agent_hub.knowledge import KnowledgeStore
from agent_hub.skills import SkillRegistry


class ToolCapableFakeChatModel(FakeListChatModel):
    """DeepAgents binds its read-only tools before invoking a chat model."""

    def bind_tools(self, tools, **kwargs):  # type: ignore[no-untyped-def]
        return self


class OptionalIntegrationTests(unittest.TestCase):
    def test_approval_guard_blocks_subagent_until_restart_safe_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkpoint = Path(temporary_directory) / "approval.sqlite"
            agent = Mock()
            agent.invoke.return_value = {"messages": ["completed"]}
            with ApprovalGuardedDeepAgent(agent, checkpoint) as guarded:
                paused = guarded.start("deep-thread", "task-guarded", 1, "read approved notes")
                self.assertIn("__interrupt__", paused)
            with ApprovalGuardedDeepAgent(agent, checkpoint) as resumed:
                completed = resumed.resume_and_invoke("deep-thread", "approved", {"messages": [{"role": "user", "content": "read notes"}]})
                repeated = resumed.resume_and_invoke("deep-thread", "approved", {"messages": []})
            self.assertTrue(completed["agent_invoked"])
            self.assertEqual("completed", completed["approval"]["status"])
            agent.invoke.assert_called_once()
            self.assertFalse(repeated["agent_invoked"])
            agent.invoke.assert_called_once()

    def test_approval_guard_rejection_never_invokes_subagent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            agent = Mock()
            with ApprovalGuardedDeepAgent(agent, Path(temporary_directory) / "approval.sqlite") as guarded:
                guarded.start("deep-thread", "task-guarded", 1, "read approved notes")
                rejected = guarded.resume_and_invoke("deep-thread", "rejected", {"messages": []})
            self.assertFalse(rejected["agent_invoked"])
            agent.invoke.assert_not_called()

    def test_real_read_only_deepagent_runs_only_after_restart_safe_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config"
            config.mkdir()
            skill = root / "skills" / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: review\ndescription: Review local files\n---\n", encoding="utf-8")
            registry = SkillRegistry(config / "skills.json")
            registry.register(skill, "personal", "portable", "low", {"codex": "native"})
            knowledge_root = root / "knowledge"
            knowledge_root.mkdir()
            KnowledgeStore(knowledge_root).save("personal", "note", "Read-only notes are available.")
            kwargs = {
                "checkpoint_path": root / "guard.sqlite",
                "skill_registry": registry,
                "task_scope": "personal",
                "skill_names": ["review"],
                "knowledge_root": knowledge_root,
                "memory_ids": ["note"],
                "workspace_root": root,
            }
            with build_approval_guarded_scoped_deep_agent(ToolCapableFakeChatModel(responses=["done"]), **kwargs) as guarded:
                self.assertIn("__interrupt__", guarded.start("real-thread", "task-real", 1, "read local notes"))
            with build_approval_guarded_scoped_deep_agent(ToolCapableFakeChatModel(responses=["done"]), **kwargs) as resumed:
                completed = resumed.resume_and_invoke("real-thread", "approved", {"messages": [{"role": "user", "content": "Respond briefly."}]})
            self.assertTrue(completed["agent_invoked"])
            self.assertIn("messages", completed["agent_result"])

    def test_configured_memory_model_requires_explicit_langchain_identifier(self) -> None:
        self.assertEqual("openai:gpt-4.1-mini", configured_memory_model({"AGENT_HUB_LANGMEM_MODEL": "openai:gpt-4.1-mini"}))
        with self.assertRaises(IntegrationUnavailable):
            configured_memory_model({})

    def test_langmem_and_deepagents_construct_without_provider_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            skill = root / "skill"
            skill.mkdir()
            memory = root / "memory.md"
            memory.write_text("local memory", encoding="utf-8")
            model = FakeListChatModel(responses=["ok"])
            manager = build_memory_manager(model, "personal")
            agent = build_deep_agent(model, [skill], [memory], workspace_root=root)
            self.assertEqual("MemoryStoreManager", type(manager).__name__)
            self.assertEqual("CompiledStateGraph", type(agent).__name__)

    def test_integration_rejects_invalid_scope_and_assets(self) -> None:
        model = FakeListChatModel(responses=["ok"])
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / "missing"
            with self.assertRaises(ValueError):
                build_memory_manager(model, "cross-scope")
            with self.assertRaises(FileNotFoundError):
                build_deep_agent(model, [missing], workspace_root=Path(temporary_directory))

    def test_registered_scoped_agent_exposes_read_only_workspace_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config"
            config.mkdir()
            skill = root / "registered-skills" / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: review\ndescription: Review safely\n---\n", encoding="utf-8")
            registry = SkillRegistry(config / "skills.json")
            registry.register(skill, "personal", "portable", "low", {"codex": "native"})
            knowledge_root = root / "knowledge"
            knowledge_root.mkdir()
            KnowledgeStore(knowledge_root).save("personal", "approved-note", "Use tests before handoff")
            agent = build_scoped_deep_agent(
                FakeListChatModel(responses=["ok"]),
                skill_registry=registry,
                task_scope="personal",
                skill_names=["review"],
                knowledge_root=knowledge_root,
                memory_ids=["approved-note"],
                workspace_root=root,
            )
            tool_names = set(agent.nodes["tools"].node.steps[0]._tools_by_name)
            self.assertTrue({"ls", "read_file", "glob", "grep"} <= tool_names)
            self.assertFalse({"write_file", "edit_file", "delete", "execute"} & tool_names)

    def test_scoped_agent_denies_cross_scope_and_tombstoned_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config"
            config.mkdir()
            skill = root / "personal-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: private\ndescription: private\n---\n", encoding="utf-8")
            registry = SkillRegistry(config / "skills.json")
            registry.register(skill, "personal", "portable", "low", {"codex": "native"})
            knowledge_root = root / "knowledge"
            knowledge_root.mkdir()
            knowledge = KnowledgeStore(knowledge_root)
            knowledge.save("personal", "old-note", "retired fact")
            knowledge.delete("personal", "old-note")
            kwargs = {"skill_registry": registry, "knowledge_root": knowledge_root, "workspace_root": root}
            model = FakeListChatModel(responses=["ok"])
            with self.assertRaises(PermissionError):
                build_scoped_deep_agent(model, task_scope="company", skill_names=["private"], memory_ids=[], **kwargs)
            with self.assertRaises(PermissionError):
                build_scoped_deep_agent(model, task_scope="personal", skill_names=[], memory_ids=["old-note"], **kwargs)
