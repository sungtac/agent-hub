from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_hub.skills import SkillRegistry


class SkillRegistryTests(unittest.TestCase):
    def test_register_search_and_manual_without_copying_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            skill = root / "source-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: test-skill\ndescription: Review code safely\n---\n", encoding="utf-8")
            registry = SkillRegistry(root / "skills.json")
            record = registry.register(skill, "personal", "portable", "low", {"codex": "native"})
            self.assertEqual(str(skill), record.path)
            self.assertEqual("test-skill", registry.search("review")[0].name)
            manual = root / "manual.md"
            registry.write_manual(manual)
            self.assertIn("test-skill", manual.read_text(encoding="utf-8"))
            self.assertFalse((root / "test-skill").exists())

    def test_registry_rejects_invalid_scope_and_malformed_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            skill = root / "source-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: test-skill\ndescription: Review code safely\n---\n", encoding="utf-8")
            registry = SkillRegistry(root / "skills.json")
            with self.assertRaises(ValueError):
                registry.register(skill, "invalid", "portable", "low", {})
            registry.registry_path.write_text('{"schema_version": 1, "skills": [{"name": "missing"}]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                registry.records()

    def test_catalog_discovers_installed_sources_without_copying_or_duplicate_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home = root / "home"
            registered = home / "agent-skills" / "shared-review"
            codex = home / ".codex" / "skills" / "docs"
            claude = home / ".claude" / "plugins" / "cache" / "loadout" / "loadout" / "1.0.0" / "skills" / "store"
            antigravity = home / ".gemini" / "antigravity-cli" / "builtin" / "skills" / "browser"
            for directory, name in ((registered, "shared-review"), (codex, "codex-docs"), (claude, "configure-store"), (antigravity, "browser")):
                directory.mkdir(parents=True)
                (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} description\n---\n", encoding="utf-8")
            registry = SkillRegistry(root / "skills.json")
            registry.register(registered, "personal", "portable", "low", {"codex": "native"})
            catalog = registry.catalog(include_discovered=True, home_directory=home)
            self.assertEqual(["browser", "codex-docs", "configure-store", "shared-review"], [item.name for item in catalog])
            self.assertEqual("codex", next(item for item in catalog if item.name == "codex-docs").origin)
            self.assertEqual({"claude": "installed"}, next(item for item in catalog if item.name == "configure-store").providers)
            self.assertEqual({"antigravity": "installed"}, next(item for item in catalog if item.name == "browser").providers)
            self.assertEqual("registered", next(item for item in catalog if item.name == "shared-review").origin)
