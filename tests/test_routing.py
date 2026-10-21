from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_hub.providers import ProviderCapability
from agent_hub.routing import ProviderRouter


class ProviderRouterTests(unittest.TestCase):
    def test_switches_only_between_available_and_nonlimited_providers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            router = ProviderRouter(Path(temporary_directory) / "provider-health.json", cooldown_seconds=60)
            capabilities = [
                ProviderCapability("codex", "available", "codex", "1", None),
                ProviderCapability("claude", "available", "claude", "1", None),
                ProviderCapability("antigravity", "unknown", None, None, None),
            ]
            time = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self.assertEqual("claude", router.select(capabilities, "claude", time).provider)
            router.record_result("claude", "limited", time)
            decision = router.select(capabilities, "claude", time + timedelta(seconds=1))
            self.assertEqual("codex", decision.provider)
            self.assertTrue(decision.fallback)
            self.assertEqual("claude", router.select(capabilities, "claude", time + timedelta(seconds=61)).provider)

    def test_never_selects_unknown_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            router = ProviderRouter(Path(temporary_directory) / "provider-health.json")
            capabilities = [ProviderCapability("codex", "unknown", None, None, None)]
            with self.assertRaises(RuntimeError):
                router.select(capabilities)

    def test_limited_provider_honours_reported_quota_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            router = ProviderRouter(Path(temporary_directory) / "provider-health.json", cooldown_seconds=60)
            time = datetime(2026, 1, 1, tzinfo=timezone.utc)
            router.record_result("antigravity", "limited", time, cooldown_seconds=86_400)
            capabilities = [
                ProviderCapability("codex", "available", "codex", "1", None),
                ProviderCapability("antigravity", "available", "agy", "1", None),
            ]
            self.assertEqual("codex", router.select(capabilities, "antigravity", time + timedelta(hours=1)).provider)
            self.assertEqual("antigravity", router.select(capabilities, "antigravity", time + timedelta(days=1, seconds=1)).provider)

    def test_concurrent_health_updates_are_serialised(self) -> None:
        import threading
        import time as clock
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary_directory:
            router = ProviderRouter(Path(temporary_directory) / "provider-health.json")
            original = ProviderRouter._load
            active = [0]
            peak = [0]
            guard = threading.Lock()

            def slow_load(instance: ProviderRouter) -> dict[str, dict[str, str]]:
                with guard:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                try:
                    value = original(instance)
                    clock.sleep(0.05)
                    return value
                finally:
                    with guard:
                        active[0] -= 1

            with patch.object(ProviderRouter, "_load", slow_load):
                threads = [threading.Thread(target=router.record_result, args=(provider, "limited")) for provider in ("codex", "claude")]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            self.assertEqual(1, peak[0])
            self.assertEqual({"codex", "claude"}, set(router._load()))
