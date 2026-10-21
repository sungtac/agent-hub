from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_hub.skills import SkillRegistry, _block_scalar, parse_front_matter, parse_skill_metadata


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


def _write_skill(directory: Path, name: str, description: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description or name + ' description'}\n---\n", encoding="utf-8")
    return directory


class SharedSkillInstallationTests(unittest.TestCase):
    """Regression tests for the cross-provider skill-registry audit."""

    def _home_with_shared_skill(self, root: Path) -> tuple[Path, Path]:
        home = root / "home"
        shared = _write_skill(home / "agent-skills" / "shared-skill", "shared-skill", "Shared skill")
        for provider in (".codex", ".claude"):
            (home / provider / "skills").mkdir(parents=True)
            (home / provider / "skills" / "shared-skill").symlink_to(shared, target_is_directory=True)
        return home, shared

    def test_symlinked_shared_skill_is_installed_for_both_providers_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home, shared = self._home_with_shared_skill(Path(temporary_directory))
            discovered = SkillRegistry.discover(home)
            self.assertEqual(1, len(discovered))
            record = discovered[0]
            self.assertEqual({"claude": "installed", "codex": "installed"}, record.providers)
            self.assertEqual(str(shared.resolve()), record.path)
            self.assertEqual("personal-directory,codex,claude", record.origin)

    def test_provider_symlink_without_personal_directory_is_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home = root / "home"
            target = _write_skill(root / "elsewhere" / "linked", "linked")
            (home / ".claude" / "skills").mkdir(parents=True)
            (home / ".claude" / "skills" / "linked").symlink_to(target, target_is_directory=True)
            (home / ".claude" / "skills" / "loop").symlink_to(home / ".claude" / "skills", target_is_directory=True)
            discovered = SkillRegistry.discover(home)
            self.assertEqual([("linked", {"claude": "installed"})], [(item.name, item.providers) for item in discovered])

    def test_registered_shared_skill_reports_both_providers_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home, shared = self._home_with_shared_skill(root)
            registry = SkillRegistry(root / "skills.json")
            registry.register(shared, "personal", "conditional", "low", {"codex": "native", "claude": "native", "antigravity": "conditional"})
            catalog = registry.catalog(include_discovered=True, home_directory=home)
            self.assertEqual(1, len(catalog))
            self.assertEqual({"antigravity": "conditional", "claude": "native", "codex": "native"}, catalog[0].providers)
            self.assertEqual("registered", catalog[0].origin)
            # A registration that omits a provider gains that provider's installation evidence.
            other = SkillRegistry(root / "other.json")
            other.register(shared, "personal", "conditional", "low", {"codex": "native"})
            self.assertEqual({"claude": "installed", "codex": "native"}, other.catalog(include_discovered=True, home_directory=home)[0].providers)
            self.assertEqual(["shared-skill"], [item.name for item in registry.search("shared", include_discovered=True, home_directory=home)])

    def test_codex_and_claude_plugin_caches_are_scanned_symmetrically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            for provider in ("codex", "claude"):
                cache = home / f".{provider}" / "plugins" / "cache" / "loadout" / "loadout" / "0.5.2"
                _write_skill(cache / "skills" / "configure-store", "configure-store")
                _write_skill(cache / "examples" / "not-a-skill", "ignored")
            discovered = SkillRegistry.discover(home)
            self.assertEqual(
                [
                    ("claude-plugin:loadout/loadout@0.5.2", {"claude": "installed"}),
                    ("codex-plugin:loadout/loadout@0.5.2", {"codex": "installed"}),
                ],
                [(item.origin, item.providers) for item in discovered],
            )
            self.assertTrue(all(item.name == "configure-store" for item in discovered))


class FrontMatterBlockScalarTests(unittest.TestCase):
    def _parse(self, front_matter: str) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "SKILL.md"
            path.write_text(f"---\n{front_matter}---\n# Body\n", encoding="utf-8")
            return parse_skill_metadata(path)

    def test_folded_block_scalar(self) -> None:
        name, description = self._parse("name: folded\ndescription: >-\n  First line\n  continues here.\n\n  New paragraph.\nlicense: MIT\n")
        self.assertEqual("folded", name)
        self.assertEqual("First line continues here.\nNew paragraph.", description)

    def test_literal_block_scalar(self) -> None:
        _, description = self._parse("name: literal\ndescription: |\n  Line one\n    indented\n  Line three\n")
        self.assertEqual("Line one\n  indented\nLine three", description)

    def test_block_scalar_chomping_and_nested_mapping(self) -> None:
        self.assertEqual("a\nb\n\n", _block_scalar("|", "+", 0, ["  a", "  b", ""]))
        self.assertEqual("a b\n", _block_scalar(">", "", 0, ["  a", "  b", ""]))
        self.assertEqual("a b", _block_scalar(">", "-", 0, ["  a", "  b"]))
        self.assertEqual(" a\nb", _block_scalar("|", "-", 2, ["   a", "  b"]))
        metadata = parse_front_matter(["name: x", "metadata:", "  name: nested", "description: >", "  folded", "---", "ignored: yes"])
        self.assertEqual({"name": "x", "description": "folded\n"}, metadata)

    def test_plain_and_quoted_scalars_unchanged(self) -> None:
        self.assertEqual(("plain", "Review: code safely"), self._parse("name: plain\ndescription: Review: code safely\n"))
        self.assertEqual(("quoted", "Quoted text"), self._parse("name: 'quoted'\ndescription: \"Quoted text\"\n"))
