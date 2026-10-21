"""Run every Agent Hub feature family through non-destructive simulated tests.

The suite deliberately uses fixture provider CLIs, fake chat models, temporary
Hub roots, and a fake sandbox runner.  It never calls a subscribed provider,
edits the real Hub runtime, or enables a scheduler.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FEATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("environment_and_initialization", ("tests.test_environment", "tests.test_initialization")),
    ("ledger_and_provider_handoff", ("tests.test_ledger", "tests.test_providers", "tests.test_routing", "tests.test_orchestration", "tests.test_provider_gateway")),
    ("approval_and_optional_integrations", ("tests.test_workflow", "tests.test_integrations", "tests.test_subscription_langchain")),
    ("knowledge_memory_and_skills", ("tests.test_knowledge", "tests.test_memory_candidates", "tests.test_subscription_memory", "tests.test_skills")),
    ("loadout_natural_language_and_review", ("tests.test_loadout", "tests.test_loadout_manager", "tests.test_natural_language", "tests.test_cli_knowledge", "tests.test_review")),
    ("backup_and_bounded_self_improvement", ("tests.test_backup", "tests.test_improvement", "tests.test_observer", "tests.test_sandbox", "tests.test_scheduler", "tests.test_discovery", "tests.test_multiagent_improvement", "tests.test_improvement_e2e")),
)


def run_feature(name: str, modules: tuple[str, ...]) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", *modules],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "feature": name,
        "modules": list(modules),
        "return_code": completed.returncode,
        "stdout": completed.stdout[-4_000:],
        "stderr": completed.stderr[-4_000:],
        "passed": completed.returncode == 0,
    }


def main() -> int:
    results = [run_feature(name, modules) for name, modules in FEATURES]
    report = {
        "kind": "non_destructive_feature_simulation",
        "platform": platform.platform(),
        "python": sys.version,
        "root": str(ROOT),
        "provider_calls": "none; fixture adapters only",
        "runtime_mutations": "none; temporary test roots only",
        "features": results,
        "passed": all(item["passed"] for item in results),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
