from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_hub.loadout import LoadoutCatalog
from agent_hub.loadout_manager import manage_fragment, manage_guard, manage_hub
from agent_hub.cli import main
from agent_hub.configuration import initialise_hub


class LoadoutManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        self.catalog = LoadoutCatalog(self.root / 'config/loadout.json')
        self.catalog.initialise()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.plugin = self.root / 'plugin'
        self.generator = self.plugin / 'skills/configure-store/generator'
        self.generator.mkdir(parents=True)
        (self.generator / 'store.py').write_text('# external installer fixture\n')
        self.fragment = self.generator / 'fragments/agent-loop'
        (self.fragment / 'files/prep').mkdir(parents=True)
        (self.fragment / 'files/prep/template.md').write_text('plugin template\n')
        (self.fragment / 'meta.json').write_text(json.dumps({'label': 'Loop'}))
        (self.fragment / 'fragment.md').write_text('official instructions\n')

    def test_hub_preview_and_apply_are_distinct(self) -> None:
        before = (self.root / 'config/loadout.json').read_bytes()
        preview = manage_hub(self.catalog, 'skill-registry', 'disable', False)
        self.assertFalse(preview['applied'])
        self.assertEqual(before, (self.root / 'config/loadout.json').read_bytes())
        manage_hub(self.catalog, 'skill-registry', 'disable', True)
        self.assertFalse(manage_hub(self.catalog, 'skill-registry', 'status', False)['configured_enabled'])
        manage_hub(self.catalog, 'skill-registry', 'enable', True)
        self.assertTrue(manage_hub(self.catalog, 'skill-registry', 'status', False)['configured_enabled'])
        with self.assertRaises(ValueError):
            manage_hub(self.catalog, 'skill-registry', 'remove', True)

    def test_fresh_hub_preview_does_not_create_loadout_state(self) -> None:
        state = self.root / 'config/loadout.json'
        state.unlink()
        status = manage_hub(self.catalog, 'skill-registry', 'status', False)
        self.assertTrue(status['using_default'])
        self.assertTrue(status['configured_enabled'])
        manage_hub(self.catalog, 'skill-registry', 'disable', False)
        self.assertFalse(state.exists())
        manage_hub(self.catalog, 'skill-registry', 'disable', True)
        self.assertFalse(manage_hub(self.catalog, 'skill-registry', 'status', False)['configured_enabled'])

    def test_fragment_preview_invokes_only_official_dry_run(self) -> None:
        with patch('agent_hub.loadout_manager._run', return_value=subprocess.CompletedProcess([], 0, 'preview', '')) as run:
            result = manage_fragment(self.plugin, self.project, 'agent-loop', 'install', 'claude', False)
        self.assertFalse(result['applied'])
        self.assertEqual(1, run.call_count)
        self.assertIn('--dry-run', run.call_args.args[0])
        self.assertEqual([], list(self.project.iterdir()))

    def test_doctor_is_project_scoped_and_needs_no_fragment_or_flavor(self) -> None:
        with patch('agent_hub.loadout_manager._run', return_value=subprocess.CompletedProcess([], 0, 'diagnosis', '')) as run:
            result = manage_fragment(self.plugin, self.project, None, 'doctor', None, False)
        self.assertEqual('doctor', result['action'])
        self.assertNotIn('--flavor', run.call_args.args[0])
        self.assertIn('--doctor', run.call_args.args[0])

    def test_modified_asset_and_malformed_guard_settings_are_never_overwritten(self) -> None:
        asset = self.project / 'prep/template.md'
        asset.parent.mkdir()
        asset.write_text('user version\n')
        with self.assertRaisesRegex(ValueError, 'modified asset'):
            manage_fragment(self.plugin, self.project, 'agent-loop', 'install', 'claude', True)
        self.assertEqual('user version\n', asset.read_text())
        settings = self.project / '.claude/settings.json'
        settings.parent.mkdir()
        settings.write_text('{invalid')
        guard = self.generator / 'fragments/guard'
        guard.mkdir(parents=True)
        (guard / 'meta.json').write_text(json.dumps({'claude_settings_hook': 'hook.json'}))
        with self.assertRaises(ValueError):
            manage_fragment(self.plugin, self.project, 'guard', 'install', 'claude', True)
        self.assertEqual('{invalid', settings.read_text())

    def test_modified_coach_hook_is_not_replaced_or_removed(self) -> None:
        guard = self.generator / 'fragments/guard'
        guard.mkdir(parents=True)
        (guard / 'meta.json').write_text(json.dumps({'claude_settings_hook': 'hook.json'}))
        canonical = {'hooks': [{'type': 'command', 'command': 'coach --hook --providers claude,codex'}]}
        (guard / 'hook.json').write_text(json.dumps(canonical))
        settings = self.project / '.claude/settings.json'
        settings.parent.mkdir()
        custom = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'coach --hook --providers claude'}]}]}}
        settings.write_text(json.dumps(custom))
        with patch('agent_hub.loadout_manager._run') as run:
            for action in ('install', 'remove'):
                with self.assertRaisesRegex(ValueError, 'modified coach Stop hook'):
                    manage_fragment(self.plugin, self.project, 'guard', action, 'claude', True)
            run.assert_not_called()
        self.assertEqual(custom, json.loads(settings.read_text()))

    def test_scaffold_metadata_protects_existing_project_files(self) -> None:
        scaffold = self.generator / 'fragments/new-scaffold'
        scaffold.mkdir(parents=True)
        (scaffold / 'meta.json').write_text(json.dumps({'scaffold': True}))
        (self.project / 'user-file').write_text('important')
        with patch('agent_hub.loadout_manager._run') as run:
            with self.assertRaisesRegex(ValueError, 'empty target'):
                manage_fragment(self.plugin, self.project, 'new-scaffold', 'install', 'codex', True)
            run.assert_not_called()
        self.assertEqual('important', (self.project / 'user-file').read_text())

    def test_failed_installer_restores_existing_and_new_files(self) -> None:
        instruction = self.project / 'CLAUDE.md'
        instruction.write_text('user instructions\n')
        asset = self.project / 'prep/template.md'
        def run(command: list[str], timeout: float = 90) -> subprocess.CompletedProcess[str]:
            if '--dry-run' in command:
                return subprocess.CompletedProcess(command, 0, 'preview', '')
            instruction.write_text('partially changed\n')
            asset.parent.mkdir()
            asset.write_text('partially installed\n')
            return subprocess.CompletedProcess(command, 2, '', 'failure')
        with patch('agent_hub.loadout_manager._run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'installer failed'):
                manage_fragment(self.plugin, self.project, 'agent-loop', 'install', 'claude', True)
        self.assertEqual('user instructions\n', instruction.read_text())
        self.assertFalse(asset.exists())

    def test_invalid_markers_and_symlink_targets_are_rejected(self) -> None:
        instruction = self.project / 'CLAUDE.md'
        instruction.write_text('<!-- store:agent-loop:start -->\n')
        with self.assertRaisesRegex(ValueError, 'malformed'):
            manage_fragment(self.plugin, self.project, 'agent-loop', 'remove', 'claude', True)
        instruction.unlink()
        try:
            instruction.symlink_to(self.root / 'external')
        except OSError:
            self.skipTest('symlink creation unavailable')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            manage_fragment(self.plugin, self.project, 'agent-loop', 'install', 'claude', True)

    def test_fragment_status_reports_malformed_markers_without_mutating(self) -> None:
        instruction = self.project / 'CLAUDE.md'
        text = '<!-- store:agent-loop:start -->\n'
        instruction.write_text(text)
        status = manage_fragment(self.plugin, self.project, 'agent-loop', 'status', 'claude', False)
        self.assertTrue(status['marker_start'])
        self.assertFalse(status['marker_end'])
        self.assertFalse(status['marker_structure_valid'])
        self.assertFalse(status['marker_present'])
        self.assertEqual('indeterminate', status['project_installation'])
        self.assertEqual('malformed_instruction_marker', status['installation_evidence'])
        self.assertIn('malformed', status['marker_error'])
        self.assertEqual(text, instruction.read_text())

    def test_fragment_status_distinguishes_scaffold_evidence_from_instruction_markers(self) -> None:
        scaffold = self.generator / 'fragments/multiagent'
        scaffold.mkdir(parents=True)
        (scaffold / 'meta.json').write_text(json.dumps({'scaffold': True}))
        (self.project / '_shared').mkdir()
        (self.project / '_shared/orchestrator-rules.md').write_text('installed scaffold\n')
        status = manage_fragment(self.plugin, self.project, 'multiagent', 'status', 'claude', False)
        self.assertFalse(status['marker_present'])
        self.assertTrue(status['scaffold_detected'])
        self.assertEqual('detected', status['project_installation'])
        self.assertEqual('scaffold_artifact', status['installation_evidence'])

    def test_nested_marker_blocks_are_rejected_before_removal(self) -> None:
        instruction = self.project / 'CLAUDE.md'
        text = ('<!-- store:agent-loop:start -->\n<!-- store:other:start -->\n'
                'important\n<!-- store:other:end -->\n<!-- store:agent-loop:end -->\n')
        instruction.write_text(text)
        with self.assertRaisesRegex(ValueError, 'nested'):
            manage_fragment(self.plugin, self.project, 'agent-loop', 'remove', 'claude', True)
        self.assertEqual(text, instruction.read_text())

    def test_modified_block_requires_explicit_replacement_even_in_preview(self) -> None:
        instruction = self.project / 'CLAUDE.md'
        text = '<!-- store:agent-loop:start -->\nuser changes\n<!-- store:agent-loop:end -->\n'
        instruction.write_text(text)
        with self.assertRaisesRegex(ValueError, 'modified or outdated instruction block'):
            manage_fragment(self.plugin, self.project, 'agent-loop', 'install', 'claude', False)
        with patch('agent_hub.loadout_manager._run', return_value=subprocess.CompletedProcess([], 0, 'preview', '')):
            result = manage_fragment(self.plugin, self.project, 'agent-loop', 'install', 'claude', False, True)
        self.assertTrue(result['replacing_modified_block'])
        self.assertFalse(result['applied'])
        self.assertEqual(text, instruction.read_text())

    def test_codex_variant_asset_is_the_final_expected_version(self) -> None:
        variant = self.fragment / 'files.codex/prep/template.md'
        variant.parent.mkdir(parents=True)
        variant.write_text('codex version\n')
        asset = self.project / 'prep/template.md'
        asset.parent.mkdir()
        asset.write_text('codex version\n')
        with patch('agent_hub.loadout_manager._run', return_value=subprocess.CompletedProcess([], 0, 'preview', '')):
            self.assertFalse(manage_fragment(self.plugin, self.project, 'agent-loop', 'install', 'codex', False)['applied'])

    def test_cli_rejects_project_scope_for_global_guard_and_catches_runtime_errors(self) -> None:
        hub = self.root / 'hub'
        hub.mkdir()
        initialise_hub(hub)
        prefix = ['loadout', '--hub-root', str(hub), 'manage', '--source', 'guard-runtime']
        with patch('agent_hub.cli.manage_guard') as guard:
            with self.assertRaisesRegex(SystemExit, 'global'):
                main(prefix + ['--action', 'enable', '--project-root', str(self.project), '--apply'])
            guard.assert_not_called()
        self.assertFalse((hub / 'config/loadout.json').exists())
        with patch('agent_hub.cli.manage_guard', side_effect=RuntimeError('could not verify')):
            with self.assertRaisesRegex(SystemExit, 'could not verify'):
                main(prefix + ['--action', 'enable', '--apply'])

    def test_cli_allows_project_wide_doctor_without_fragment_name_or_flavor(self) -> None:
        hub = self.root / 'hub'
        hub.mkdir()
        initialise_hub(hub)
        arguments = ['loadout', '--hub-root', str(hub), 'manage', '--source', 'fragment', '--project-root', str(self.project), '--plugin-root', str(self.plugin), '--action', 'doctor']
        with patch('agent_hub.cli.manage_fragment', return_value={'source': 'fragment', 'action': 'doctor', 'applied': False}) as doctor, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(arguments))
        self.assertEqual((self.plugin, self.project, None, 'doctor', None, False, False), doctor.call_args.args)

    def test_guard_runtime_preview_does_not_change_the_global_switch(self) -> None:
        with patch('agent_hub.loadout_manager.shutil.which', return_value='/fake/coach'), patch('agent_hub.loadout_manager._run') as run:
            self.assertFalse(manage_guard('enable', False)['applied'])
            run.assert_not_called()
            run.side_effect = [subprocess.CompletedProcess([], 0, 'requested on', ''), subprocess.CompletedProcess([], 0, '요금가드: on\n', '')]
            self.assertTrue(manage_guard('enable', True)['applied'])
            self.assertEqual(['/fake/coach', 'guard', 'on'], run.call_args_list[0].args[0])
            self.assertEqual(['/fake/coach', 'guard', 'status', '--providers', ''], run.call_args_list[1].args[0])

    def test_guard_mismatched_switch_state_is_not_reported_as_success(self) -> None:
        with patch('agent_hub.loadout_manager.shutil.which', return_value='/fake/coach'), patch('agent_hub.loadout_manager._run', side_effect=[subprocess.CompletedProcess([], 0, 'on', ''), subprocess.CompletedProcess([], 0, '요금가드: off\n', '')]):
            with self.assertRaisesRegex(RuntimeError, 'did not reach'):
                manage_guard('enable', True)


if __name__ == '__main__':
    unittest.main()
