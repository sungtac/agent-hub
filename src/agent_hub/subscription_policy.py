"""Dependency-free policy and persistence for the optional CLI chat bridge."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


POLICY_SCHEMA_VERSION = 1
SUPPORTED_PROVIDERS = frozenset({"codex", "claude", "antigravity"})
MAX_TIMEOUT_SECONDS = 600.0
POLICY_FILE_NAME = "subscription-langchain-policy.json"


@dataclass(frozen=True)
class SubscriptionBridgePolicy:
    """Validated, API-key-free limits for one subscription bridge instance."""

    schema_version: int = POLICY_SCHEMA_VERSION
    enabled: bool = False
    allowed_providers: frozenset[str] = SUPPORTED_PROVIDERS
    max_calls_per_task: int = 1
    timeout_seconds: float = 120.0
    allow_tools: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool) or self.schema_version != POLICY_SCHEMA_VERSION:
            raise ValueError("Unsupported subscription bridge policy schema.")
        if not isinstance(self.enabled, bool) or not isinstance(self.allow_tools, bool):
            raise ValueError("Subscription bridge boolean policy fields must be true or false.")
        if not isinstance(self.allowed_providers, frozenset) or not self.allowed_providers or not self.allowed_providers <= SUPPORTED_PROVIDERS:
            raise ValueError("Subscription bridge provider allowlist is invalid.")
        if not isinstance(self.max_calls_per_task, int) or isinstance(self.max_calls_per_task, bool) or self.max_calls_per_task < 1:
            raise ValueError("Subscription bridge max_calls_per_task must be a positive integer.")
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool) or not 0 < self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError("Subscription bridge timeout_seconds is outside the safe range.")
        if self.allow_tools:
            raise ValueError("Subscription bridge tool calling is not supported.")

    def require_dispatchable(self, provider: str) -> None:
        if not self.enabled:
            raise SubscriptionBridgeUnavailable("Subscription LangChain bridge is disabled by policy.")
        if provider not in self.allowed_providers:
            raise SubscriptionBridgeUnavailable(f"Provider is not allowed by subscription bridge policy: {provider}")


class SubscriptionBridgeUnavailable(RuntimeError):
    """Raised when an optional subscription bridge is disabled or unsafe."""


def subscription_bridge_policy(raw: Mapping[str, Any] | None = None) -> SubscriptionBridgePolicy:
    if raw is None:
        return SubscriptionBridgePolicy()
    allowed_keys = {"schema_version", "enabled", "allowed_providers", "max_calls_per_task", "timeout_seconds", "allow_tools"}
    unknown = set(raw) - allowed_keys
    if unknown:
        raise ValueError(f"Unknown subscription bridge policy fields: {', '.join(sorted(unknown))}")
    providers = raw.get("allowed_providers", SUPPORTED_PROVIDERS)
    if isinstance(providers, (str, bytes)):
        raise ValueError("Subscription bridge allowed_providers must be a list of provider names.")
    try:
        allowed_providers = frozenset(providers)
    except TypeError as error:
        raise ValueError("Subscription bridge allowed_providers must be iterable.") from error
    return SubscriptionBridgePolicy(
        schema_version=raw.get("schema_version", POLICY_SCHEMA_VERSION),
        enabled=raw.get("enabled", False),
        allowed_providers=allowed_providers,
        max_calls_per_task=raw.get("max_calls_per_task", 1),
        timeout_seconds=raw.get("timeout_seconds", 120.0),
        allow_tools=raw.get("allow_tools", False),
    )


def load_subscription_bridge_policy(state_directory: Path) -> SubscriptionBridgePolicy:
    directory = state_directory.expanduser().resolve(strict=False)
    if not directory.is_dir():
        raise ValueError(f"Subscription bridge state directory does not exist: {directory}")
    destination = directory / POLICY_FILE_NAME
    if not destination.exists():
        return subscription_bridge_policy()
    if destination.is_symlink() or not destination.is_file():
        raise ValueError("Subscription bridge policy must be a regular file.")
    try:
        raw = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Could not read subscription bridge policy.") from error
    if not isinstance(raw, dict):
        raise ValueError("Subscription bridge policy must be a JSON object.")
    return subscription_bridge_policy(raw)


def save_subscription_bridge_policy(state_directory: Path, policy: SubscriptionBridgePolicy) -> Path:
    directory = state_directory.expanduser().resolve(strict=False)
    if not directory.is_dir():
        raise ValueError(f"Subscription bridge state directory does not exist: {directory}")
    destination = directory / POLICY_FILE_NAME
    if destination.exists() and (destination.is_symlink() or not destination.is_file()):
        raise ValueError("Subscription bridge policy destination is unsafe.")
    payload = {
        "schema_version": policy.schema_version,
        "enabled": policy.enabled,
        "allowed_providers": sorted(policy.allowed_providers),
        "max_calls_per_task": policy.max_calls_per_task,
        "timeout_seconds": policy.timeout_seconds,
        "allow_tools": policy.allow_tools,
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        os.replace(temporary_path, destination)
        if os.name != "nt":
            destination.chmod(0o600)
        return destination
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
