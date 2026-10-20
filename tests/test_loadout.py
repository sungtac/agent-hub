from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from agent_hub.loadout import LoadoutCatalog


class LoadoutCatalogTests(unittest.TestCase):
    def test_unified_catalog_distinguishes_catalog_entries_from_project_markers(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / 'config'
            config.mkdir()
            catalog = LoadoutCatalog(config / 'loadout.json')
            catalog.initialise()
            plugin = root / 'plugin'
            fragments = plugin / 'skills/configure-store/generator/fragments'
            for name in ('agent-loop', 'guard', 'multiagent'):
                folder = fragments / name
                folder.mkdir(parents=True)
                (folder / 'meta.json').write_text(json.dumps({'label': name, 'corner': 'test', 'desc': 'description', 'available': True, 'scaffold': name == 'multiagent'}))
            project = root / 'project'
            project.mkdir()
            (project / 'CLAUDE.md').write_text('<!-- store:agent-loop:start -->\n')
            (project / '_shared').mkdir()
            (project / '_shared/orchestrator-rules.md').write_text('scaffold evidence\n')
            result = catalog.unified_catalog(plugin, project)
            items = {item['name']: item for item in result['loadout_fragments']}
            self.assertEqual(4, len(result['hub_modules']))
            self.assertEqual([True, True, True], [item['catalog_listed'] for item in result['loadout_fragments']])
            self.assertEqual('present', items['agent-loop']['managed_marker'])
            self.assertEqual(('detected', 'instruction_marker'), (items['agent-loop']['project_installation'], items['agent-loop']['installation_evidence']))
            self.assertEqual(('not_detected', 'none'), (items['guard']['project_installation'], items['guard']['installation_evidence']))
            self.assertEqual('absent', items['multiagent']['managed_marker'])
            self.assertEqual(('detected', 'scaffold_artifact'), (items['multiagent']['project_installation'], items['multiagent']['installation_evidence']))
            self.assertEqual(['not_checked', 'not_checked', 'not_checked'], [item['project_installation'] for item in catalog.unified_catalog(plugin)['loadout_fragments']])
            self.assertEqual(str(project.resolve()), result['project_root'])
            with self.assertRaises(ValueError):
                catalog.unified_catalog(root / 'missing')

    def _require_symlink(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink creation may require elevated Windows privileges")

    def test_enablement_manual_and_project_only_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config"
            config.mkdir()
            catalog = LoadoutCatalog(config / "loadout.json")
            self.assertTrue(any(module.enabled for module in catalog.initialise()))
            self.assertFalse(catalog.set_enabled("skill-registry", False).enabled)
            self.assertNotIn("skill-registry", {module.name for module in catalog.enabled_for("codex")})
            reports = root / "reports"
            reports.mkdir()
            manual = reports / "loadout.md"
            catalog.write_manual(manual)
            self.assertIn("꺼짐", manual.read_text(encoding="utf-8"))
            project = root / "project"
            project.mkdir()
            overlay = project / ".agent-hub" / "loadout.codex.md"
            catalog.write_new_overlay("codex", project, overlay)
            overlay_text = overlay.read_text(encoding="utf-8")
            self.assertIn("project-only", overlay_text)
            self.assertIn("공통 오케스트레이션 계약", overlay_text)
            self.assertIn("--task-id TASK", overlay_text)
            self.assertIn("# Architecture evidence gate", overlay_text)
            self.assertIn("CLI 진입점", overlay_text)
            self.assertEqual({"antigravity": "missing", "claude": "missing", "codex": "current"}, catalog.verify_overlays(project))
            with self.assertRaises(FileExistsError):
                catalog.write_new_overlay("codex", project, overlay)
            catalog.write_new_overlay("codex", project, overlay, replace_managed=True)
            self.assertIn("공통 오케스트레이션 계약", overlay.read_text(encoding="utf-8"))

    def test_rejects_overlay_outside_project_and_unknown_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config"
            config.mkdir()
            catalog = LoadoutCatalog(config / "loadout.json")
            catalog.initialise()
            project = root / "project"
            project.mkdir()
            with self.assertRaises(ValueError):
                catalog.write_new_overlay("codex", project, root / "outside.md")
            with self.assertRaises(ValueError):
                catalog.set_enabled("not-a-module", True)

    def test_refresh_refuses_unmanaged_or_symlink_overlay(self) -> None:
        self._require_symlink()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config"
            config.mkdir()
            catalog = LoadoutCatalog(config / "loadout.json")
            catalog.initialise()
            project = root / "project"
            project.mkdir()
            unmanaged = project / "unmanaged.md"
            unmanaged.write_text("user content\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                catalog.write_new_overlay("codex", project, unmanaged, replace_managed=True)
            target = root / "target.md"
            target.write_text("# Agent Hub Loadout overlay\nmanaged_by: agent-hub\nscope: project-only\n", encoding="utf-8")
            linked = project / "linked.md"
            linked.symlink_to(target)
            with self.assertRaises(ValueError):
                catalog.write_new_overlay("codex", project, linked, replace_managed=True)

    def test_disabled_handoff_module_is_omitted_from_new_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config"
            config.mkdir()
            catalog = LoadoutCatalog(config / "loadout.json")
            catalog.initialise()
            catalog.set_enabled("orchestration-handoff", False)
            project = root / "project"
            project.mkdir()
            overlay = project / ".agent-hub" / "claude.md"
            catalog.write_new_overlay("claude", project, overlay)
            self.assertNotIn("공통 오케스트레이션 계약", overlay.read_text(encoding="utf-8"))

    def test_verify_detects_missing_stale_unmanaged_and_unsafe_overlays(self) -> None:
        self._require_symlink()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config"
            config.mkdir()
            project = root / "project"
            project.mkdir()
            catalog = LoadoutCatalog(config / "loadout.json")
            catalog.initialise()
            catalog.write_new_overlay("codex", project, project / ".agent-hub" / "loadout.codex.md")
            catalog.set_enabled("orchestration-handoff", False)
            antigravity = project / ".agent-hub" / "loadout.antigravity.md"
            antigravity.write_text("user file\n", encoding="utf-8")
            target = root / "target.md"
            target.write_text("ignored\n", encoding="utf-8")
            (project / ".agent-hub" / "loadout.claude.md").symlink_to(target)
            self.assertEqual(
                {"antigravity": "unmanaged", "claude": "unsafe", "codex": "stale"},
                catalog.verify_overlays(project),
            )
