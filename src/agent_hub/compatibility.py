"""Deterministic compatibility assessment for bootstrap and full Hub profiles."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence


BOOTSTRAP_PYTHON = "3.10"
FULL_PYTHON = "3.11"
PROVIDER_COMMANDS = ("codex", "claude", "agy")


@dataclass(frozen=True)
class RequirementResult:
    name: str
    required: str
    observed: str
    status: str
    action: str


def version_at_least(observed: str, required: str) -> bool:
    """Compare numeric version prefixes without introducing a bootstrap dependency."""

    def parse(value: str) -> tuple[int, ...]:
        match = re.match(r"(\d+(?:\.\d+)*)", value)
        if match is None:
            return ()
        return tuple(int(part) for part in match.group(1).split("."))

    observed_parts = parse(observed)
    required_parts = parse(required)
    if not observed_parts or not required_parts:
        return False
    width = max(len(observed_parts), len(required_parts))
    return observed_parts + (0,) * (width - len(observed_parts)) >= required_parts + (0,) * (width - len(required_parts))


def command_statuses(report: Mapping[str, object]) -> Mapping[str, str]:
    commands = report.get("commands", [])
    if not isinstance(commands, (list, tuple)):
        return {}
    return {
        str(command.get("name")): str(command.get("status"))
        for command in commands
        if isinstance(command, Mapping)
    }


def assess(report: Mapping[str, object]) -> list[RequirementResult]:
    """Classify current differences; this function neither installs nor invokes tools."""

    platform = report.get("platform", {})
    python_version = str(platform.get("python", "unknown")) if isinstance(platform, Mapping) else "unknown"
    statuses = command_statuses(report)
    results = [
        RequirementResult(
            name="bootstrap-python",
            required=f">={BOOTSTRAP_PYTHON}",
            observed=python_version,
            status="ready" if version_at_least(python_version, BOOTSTRAP_PYTHON) else "blocked",
            action="Use an isolated supported Python before bootstrap implementation." if not version_at_least(python_version, BOOTSTRAP_PYTHON) else "No action.",
        ),
        RequirementResult(
            name="full-profile-python",
            required=f">={FULL_PYTHON}",
            observed=python_version,
            status="ready" if version_at_least(python_version, FULL_PYTHON) else "conditional",
            action="Install and validate isolated Python 3.11+ before DeepAgents integration." if not version_at_least(python_version, FULL_PYTHON) else "No action.",
        ),
    ]
    for provider in PROVIDER_COMMANDS:
        available = statuses.get(provider) == "available"
        results.append(
            RequirementResult(
                name=f"provider-{provider}",
                required="installed and conformance-tested",
                observed="command available" if available else "not detected",
                status="conditional" if available else "unknown",
                action="Run adapter conformance tests." if available else "Install, authenticate, then run adapter conformance tests.",
            )
        )
    return results


def serialise_results(results: Sequence[RequirementResult]) -> list[dict[str, str]]:
    """Expose only the public compatibility contract to the CLI."""

    return [asdict(item) for item in results]
