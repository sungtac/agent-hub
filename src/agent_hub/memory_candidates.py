"""Owner-reviewed staging area between LangMem extraction and live knowledge."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .file_lock import exclusive_lock
from .knowledge import KnowledgeStore, SCOPES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryCandidateStore:
    """Candidates are searchable only through this explicit review queue."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path.expanduser().resolve(strict=False)
        if not self.state_path.parent.is_dir():
            raise ValueError("Candidate state directory must already exist.")
        self.lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")

    def ingest(self, scope: str, source_task_id: str, evidence: str, extracted: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if scope not in SCOPES or not source_task_id.strip() or not evidence.strip():
            raise ValueError("Scope, source task, and evidence are required.")
        saved: list[dict[str, Any]] = []
        with exclusive_lock(self.lock_path):
            state = self._load()
            for item in extracted:
                content = item.get("content")
                kind = item.get("kind", "semantic")
                if not isinstance(content, (str, dict, list)) or not content:
                    continue
                canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(f"{scope}\n{kind}\n{canonical}".encode("utf-8")).hexdigest()
                candidate_id = f"candidate-{digest[:24]}"
                current = state["candidates"].get(candidate_id)
                if current is None:
                    current = {
                        "candidate_id": candidate_id,
                        "digest": digest,
                        "scope": scope,
                        "source_task_id": source_task_id,
                        "evidence": evidence,
                        "kind": str(kind),
                        "content": content,
                        "status": "candidate",
                        "created_at": _now(),
                        "updated_at": _now(),
                    }
                    state["candidates"][candidate_id] = current
                saved.append(dict(current))
            self._write(state)
        return saved

    def pending(self, scope: str) -> list[dict[str, Any]]:
        if scope not in SCOPES:
            raise ValueError("Invalid candidate scope.")
        with exclusive_lock(self.lock_path):
            state = self._load()
        return [
            dict(item)
            for item in state["candidates"].values()
            if item["scope"] == scope and item["status"] == "candidate"
        ]

    def reject(self, candidate_id: str) -> dict[str, Any]:
        return self._change_status(candidate_id, "rejected")

    def promote(self, candidate_id: str, knowledge: KnowledgeStore) -> Path:
        """Explicit owner action that copies one reviewed item into live knowledge."""
        with exclusive_lock(self.lock_path):
            state = self._load()
            candidate = state["candidates"].get(candidate_id)
            if candidate is None or candidate["status"] != "candidate":
                raise ValueError("Only a pending candidate can be promoted.")
            content = candidate["content"]
            body = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True)
            identifier = f"memory-{candidate['digest'][:20]}"
            path = knowledge.save(candidate["scope"], identifier, body)
            candidate["status"] = "approved"
            candidate["knowledge_id"] = identifier
            candidate["updated_at"] = _now()
            self._write(state)
            return path

    def _change_status(self, candidate_id: str, status: str) -> dict[str, Any]:
        with exclusive_lock(self.lock_path):
            state = self._load()
            candidate = state["candidates"].get(candidate_id)
            if candidate is None or candidate["status"] != "candidate":
                raise ValueError("Only a pending candidate can change status.")
            candidate["status"] = status
            candidate["updated_at"] = _now()
            self._write(state)
            return dict(candidate)

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"schema_version": 1, "candidates": {}}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Could not read LangMem candidate state.") from error
        if value.get("schema_version") != 1 or not isinstance(value.get("candidates"), dict):
            raise ValueError("Unsupported LangMem candidate state.")
        return value

    def _write(self, state: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_path.parent, delete=False) as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                temporary = Path(stream.name)
            os.replace(temporary, self.state_path)
            if os.name != "nt":
                self.state_path.chmod(0o600)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def extract_langmem_candidates(
    manager: Any,
    messages: Sequence[Mapping[str, Any]],
    user_id: str,
    scope: str,
    source_task_id: str,
    evidence: str,
    candidate_store: MemoryCandidateStore,
) -> list[dict[str, Any]]:
    """Run the LangMem manager in its in-memory namespace and stage outputs.

    `build_memory_manager` supplies an ephemeral in-memory LangGraph store and
    disables deletes. The manager output is never written directly to live Hub
    knowledge; all extracted records enter this review queue first.
    """
    if scope not in SCOPES or not user_id.strip():
        raise ValueError("Valid scope and owner identity are required.")
    extracted = manager.invoke(
        {"messages": list(messages)},
        config={"configurable": {"langgraph_user_id": user_id}},
    )
    if not isinstance(extracted, list):
        raise ValueError("LangMem returned an unsupported result.")
    normalized: list[dict[str, Any]] = []
    for item in extracted:
        if not isinstance(item, Mapping):
            continue
        value = item.get("value", item)
        if not isinstance(value, Mapping) or "content" not in value:
            continue
        normalized.append({"kind": value.get("kind", "semantic"), "content": value["content"]})
    return candidate_store.ingest(scope, source_task_id, evidence, normalized)
