"""Bounded self-improvement candidate queue; it cannot alter protected policy."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .file_lock import exclusive_lock


PROTECTED_AREAS = {"policy", "security", "budget", "provider-routing", "retention"}


@dataclass(frozen=True)
class ImprovementCandidate:
    identifier: str
    area: str
    summary: str
    evidence: str
    created_at: str


class ImprovementQueue:
    """Deduplicated candidate storage with a daily promotion budget."""

    def __init__(self, state_path: Path, max_promotions_per_day: int = 2) -> None:
        self.state_path = state_path.expanduser().resolve(strict=False)
        self.max_promotions_per_day = max_promotions_per_day
        if not self.state_path.parent.is_dir() or max_promotions_per_day < 1:
            raise ValueError("Invalid improvement queue configuration.")
        # Serialise read-modify-write so concurrent callers cannot clobber each
        # other's updates. The observer holds its own lock, but the queue is a
        # separate state file that promote() mutates outside that lock (and the
        # queue may be driven directly), so it needs its own.
        self.lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")

    def propose(self, area: str, summary: str, evidence: str, at: datetime | None = None) -> ImprovementCandidate:
        # The observer classifies risk from the exact changed paths.  ``area``
        # is descriptive metadata and must never bypass or block that policy.
        if not area.strip() or not summary.strip() or not evidence.strip():
            raise ValueError("Candidate area, summary, and evidence are required.")
        timestamp = (at or datetime.now(timezone.utc)).isoformat()
        identifier = hashlib.sha256(f"{area}\n{summary}\n{evidence}".encode("utf-8")).hexdigest()[:20]
        with exclusive_lock(self.lock_path):
            state = self._load()
            cooldown = state["cooldowns"].get(area)
            if cooldown:
                try:
                    if datetime.fromisoformat(cooldown["until"]) > (at or datetime.now(timezone.utc)):
                        raise PermissionError(f"Improvement area is cooling down: {area}")
                except (KeyError, ValueError):
                    raise ValueError("Invalid improvement cooldown state.") from None
            if identifier not in state["candidates"]:
                state["candidates"][identifier] = {"area": area, "summary": summary, "evidence": evidence, "created_at": timestamp, "status": "candidate"}
                self._write(state)
            item = state["candidates"][identifier]
        return ImprovementCandidate(identifier, item["area"], item["summary"], item["evidence"], item["created_at"])

    def promote(self, identifier: str, checks_passed: bool, at: datetime | None = None) -> str:
        with exclusive_lock(self.lock_path):
            state = self._load()
            candidate = state["candidates"].get(identifier)
            if candidate is None or candidate["status"] != "candidate":
                raise ValueError("Candidate is not eligible for promotion.")
            if not checks_passed:
                candidate["status"] = "rejected"
                state["cooldowns"][candidate["area"]] = {
                    "until": ((at or datetime.now(timezone.utc)) + timedelta(minutes=240)).isoformat(),
                    "reason": "candidate checks failed",
                }
                self._write(state)
                return "rejected"
            date = (at or datetime.now(timezone.utc)).date().isoformat()
            promoted_today = state["promotions"].get(date, [])
            if len(promoted_today) >= self.max_promotions_per_day:
                return "deferred"
            candidate["status"] = "promoted"
            promoted_today.append(identifier)
            state["promotions"][date] = promoted_today
            self._write(state)
            return "promoted"

    def get_candidate(self, identifier: str) -> dict[str, str]:
        candidate = self._load()["candidates"].get(identifier)
        if candidate is None:
            raise ValueError("Unknown improvement candidate.")
        return dict(candidate)

    def set_cooldown(self, area: str, reason: str, seconds: int = 14_400, at: datetime | None = None) -> None:
        if not area.strip() or seconds < 1:
            raise ValueError("Invalid cooldown area or duration.")
        with exclusive_lock(self.lock_path):
            state = self._load()
            timestamp = at or datetime.now(timezone.utc)
            state["cooldowns"][area] = {"until": (timestamp + timedelta(seconds=seconds)).isoformat(), "reason": reason}
            self._write(state)

    def _load(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {"schema_version": 1, "candidates": {}, "promotions": {}, "cooldowns": {}}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Could not read improvement state.") from error
        if value.get("schema_version") != 1 or not isinstance(value.get("candidates"), dict) or not isinstance(value.get("promotions"), dict):
            raise ValueError("Unsupported improvement state.")
        value.setdefault("cooldowns", {})
        if not isinstance(value["cooldowns"], dict):
            raise ValueError("Unsupported improvement cooldown state.")
        return value

    def _write(self, state: dict[str, object]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_path.parent, delete=False) as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                temporary = Path(stream.name)
            os.replace(temporary, self.state_path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
