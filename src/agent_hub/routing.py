"""Durable provider selection that never routes to unknown or limited clients."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .providers import ProviderCapability


PROVIDERS = ("codex", "claude", "antigravity")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RouteDecision:
    provider: str
    requested_provider: str | None
    fallback: bool
    reason: str


class ProviderRouter:
    """Small persisted health ledger; capability discovery remains authoritative."""

    def __init__(self, state_path: Path, cooldown_seconds: int = 900) -> None:
        self.state_path = state_path.expanduser().resolve(strict=False)
        self.cooldown_seconds = cooldown_seconds
        if cooldown_seconds < 1 or not self.state_path.parent.is_dir():
            raise ValueError("Invalid provider-health state location or cooldown.")

    def select(
        self,
        capabilities: Iterable[ProviderCapability],
        requested_provider: str | None = None,
        at: datetime | None = None,
    ) -> RouteDecision:
        if requested_provider is not None and requested_provider not in PROVIDERS:
            raise ValueError(f"Unsupported provider: {requested_provider}")
        observed = {item.provider: item for item in capabilities if item.provider in PROVIDERS}
        health = self._load()
        timestamp = at or utc_now()
        candidates = [
            provider for provider in PROVIDERS
            if observed.get(provider) is not None
            and observed[provider].state == "available"
            and not self._limited(health.get(provider), timestamp)
        ]
        if requested_provider in candidates:
            return RouteDecision(requested_provider, requested_provider, False, "requested provider is available")
        if requested_provider is not None and requested_provider not in candidates and candidates:
            return RouteDecision(candidates[0], requested_provider, True, "requested provider is unavailable or cooling down")
        if requested_provider is None and candidates:
            return RouteDecision(candidates[0], None, False, "first available provider")
        raise RuntimeError("No installed, authenticated, and non-limited provider is available.")

    def record_result(self, provider: str, status: str, at: datetime | None = None, cooldown_seconds: int | None = None) -> None:
        if provider not in PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")
        if cooldown_seconds is not None and cooldown_seconds < 1:
            raise ValueError("Cooldown override must be positive.")
        health = self._load()
        timestamp = at or utc_now()
        item: dict[str, str] = {"last_status": status, "updated_at": timestamp.isoformat()}
        if status in {"limited", "unavailable", "timed_out", "empty"}:
            duration = cooldown_seconds if status == "limited" and cooldown_seconds is not None else self.cooldown_seconds
            item["cooldown_until"] = (timestamp + timedelta(seconds=duration)).isoformat()
        health[provider] = item
        self._write(health)

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.state_path.exists():
            return {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Could not read provider-health state.") from error
        if value.get("schema_version") != 1 or not isinstance(value.get("providers"), dict):
            raise ValueError("Unsupported provider-health state.")
        return {str(provider): dict(data) for provider, data in value["providers"].items() if isinstance(data, dict)}

    def _write(self, health: dict[str, dict[str, str]]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_path.parent, delete=False) as stream:
                json.dump({"schema_version": 1, "providers": health}, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                temporary = Path(stream.name)
            os.replace(temporary, self.state_path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _limited(item: dict[str, str] | None, timestamp: datetime) -> bool:
        if not item or not item.get("cooldown_until"):
            return False
        try:
            return datetime.fromisoformat(item["cooldown_until"]) > timestamp
        except ValueError:
            return True
