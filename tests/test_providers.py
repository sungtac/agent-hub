from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_hub.providers import CodexAdapter, JsonCliAdapter, ProviderExecutionError, TemplateCliAdapter, antigravity_adapter, antigravity_executable, claude_adapter, classify_failure, limited_retry_after_seconds, parse_events, platform_command, provider_environment


class CodexAdapterTests(unittest.TestCase):
    def make_fake_codex(self, directory: Path, body: str) -> Path:
        if os.name == "nt":
            script = directory / "fake-codex.py"
            script.write_text(body, encoding="utf-8")
            executable = directory / "fake-codex.cmd"
            executable.write_text(f'@echo off\r\n"{sys.executable}" "%~dp0fake-codex.py" %*\r\n', encoding="utf-8")
            return executable
        executable = directory / "fake-codex"
        executable.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return executable

    @unittest.skipIf(os.name == "nt", "POSIX script fixture is not a native Windows executable")
    def test_discovery_and_structured_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            executable = self.make_fake_codex(
                directory,
                "import json, pathlib, sys\n"
                "if '--version' in sys.argv: print('codex-cli fake'); raise SystemExit(0)\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
                "out.write_text('READY', encoding='utf-8')\n"
                "print(json.dumps({'type': 'result', 'ok': True}))\n",
            )
            adapter = CodexAdapter(str(executable))
            self.assertEqual("available", adapter.discover().state)
            result = adapter.execute("reply", directory)
            self.assertEqual("succeeded", result.status)
            self.assertEqual("READY", result.final_message)
            self.assertEqual("result", result.events[0]["type"])

    @unittest.skipIf(os.name == "nt", "POSIX script fixture is not a native Windows executable")
    def test_timeout_terminates_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            executable = self.make_fake_codex(directory, "import time\ntime.sleep(30)\n")
            result = CodexAdapter(str(executable)).execute("reply", directory, timeout_seconds=0.01)
            self.assertEqual("timed_out", result.status)

    def test_input_and_failure_normalization(self) -> None:
        with self.assertRaises(ProviderExecutionError):
            CodexAdapter("/missing/codex").execute(" ", Path.cwd())
        self.assertEqual("limited", classify_failure("", "usage limit reached"))
        self.assertEqual("unavailable", classify_failure("", "unauthorized"))
        self.assertEqual("unavailable", classify_failure("", "network is unreachable"))
        self.assertEqual(6 * 86_400 + 18 * 3_600 + 41, limited_retry_after_seconds("Resets in 6d18h41s."))
        self.assertIsNone(limited_retry_after_seconds("quota exhausted without a reset estimate"))
        self.assertEqual(({"event": "ok"},), parse_events('{"event": "ok"}\nnot-json'))

    @unittest.skipIf(os.name == "nt", "POSIX script fixture is not a native Windows executable")
    def test_template_adapter_has_explicit_safe_argv_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            executable = self.make_fake_codex(
                directory,
                "import sys\n"
                "if '--version' in sys.argv: print('fake-provider 1'); raise SystemExit(0)\n"
                "import json\n"
                "print(json.dumps({'result': sys.argv[-1]}))\n",
            )
            adapter = JsonCliAdapter("claude", str(executable), ("run", "--print={prompt}"), "result")
            self.assertEqual("available", adapter.discover().state)
            self.assertEqual("--print=hello", adapter.execute("hello", directory).final_message)
            schema_adapter = JsonCliAdapter("antigravity", str(executable), ("--json-schema", '{"type":"object","properties":{"value":{"type":"string"}}}', "--print={prompt}"), "result")
            self.assertEqual("--print=hello", schema_adapter.execute("hello", directory).final_message)
            with self.assertRaises(ValueError):
                TemplateCliAdapter("claude", str(executable), ("run",))
            with self.assertRaises(ValueError):
                TemplateCliAdapter("claude", str(executable), ("run", "{unknown}", "{prompt}"))

    @unittest.skipIf(os.name == "nt", "POSIX script fixture is not a native Windows executable")
    def test_json_adapter_marks_blank_or_invalid_structured_output_as_non_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            blank = self.make_fake_codex(
                directory,
                "import sys\n"
                "if '--version' in sys.argv: print('fake'); raise SystemExit(0)\n"
                "print('{\\\"result\\\": \\\"  \\\"}')\n",
            )
            self.assertEqual("empty", JsonCliAdapter("claude", str(blank), ("--print={prompt}",), "result").execute("review", directory).status)
            invalid = self.make_fake_codex(
                directory,
                "import sys\n"
                "if '--version' in sys.argv: print('fake'); raise SystemExit(0)\n"
                "print('not-json')\n",
            )
            self.assertEqual("failed", JsonCliAdapter("claude", str(invalid), ("--print={prompt}",), "result").execute("review", directory).status)

    def test_antigravity_adapter_accepts_explicit_agy_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            executable = self.make_fake_codex(Path(temporary_directory), "if '--version' in __import__('sys').argv: print('fake')\n")
            self.assertEqual("antigravity", antigravity_adapter(str(executable)).provider)

    def test_antigravity_prefers_owner_read_only_wrapper(self) -> None:
        from unittest.mock import patch

        with patch("agent_hub.providers.shutil.which", return_value="/opt/agy-readonly-review") as locate:
            self.assertEqual("/opt/agy-readonly-review", antigravity_executable())
        locate.assert_called_once_with("agy-readonly-review")

    def test_antigravity_raw_executable_override_is_not_an_escape_hatch(self) -> None:
        with patch("agent_hub.providers.shutil.which", return_value="/opt/agy-readonly-review"):
            self.assertEqual("/opt/agy-readonly-review", antigravity_executable("/opt/agy"))

    def test_antigravity_is_unavailable_without_the_readonly_wrapper(self) -> None:
        with patch("agent_hub.providers.shutil.which", return_value=None):
            self.assertIsNone(antigravity_executable())

    def test_read_only_subscription_adapters_bound_reasoning_effort(self) -> None:
        template = claude_adapter().command_template
        self.assertIn("low", template)
        self.assertIn("--safe-mode", template)
        self.assertIn("--restricted", template)
        self.assertIn("--permission-prompts", template)
        self.assertIn("low", antigravity_adapter().command_template)

    def test_provider_profile_override_does_not_leak_to_other_cli(self) -> None:
        inherited = {
            "CODEX_HOME": "/profiles/codex",
            "CLAUDE_CONFIG_DIR": "/profiles/claude",
            "ANTIGRAVITY_HOME": "/profiles/antigravity",
            "SAFE_VALUE": "retained",
        }
        codex_environment = provider_environment("codex", inherited)
        self.assertEqual("/profiles/codex", codex_environment["CODEX_HOME"])
        self.assertNotIn("CLAUDE_CONFIG_DIR", codex_environment)
        self.assertNotIn("ANTIGRAVITY_HOME", codex_environment)
        self.assertEqual("retained", codex_environment["SAFE_VALUE"])
        self.assertEqual("/profiles/claude", provider_environment("claude", inherited)["CLAUDE_CONFIG_DIR"])
        with self.assertRaises(ValueError):
            provider_environment("unknown", inherited)

    def test_windows_batch_launchers_are_rejected_before_prompt_interpretation(self) -> None:
        from unittest.mock import patch

        with patch("agent_hub.providers.os.name", "nt"):
            with self.assertRaises(ProviderExecutionError):
                platform_command(["C:/tools/provider.cmd", "--print=hello & unsafe"])
            with self.assertRaises(ProviderExecutionError):
                platform_command(["C:/tools/PROVIDER.BAT", "--print=hello & unsafe"])
