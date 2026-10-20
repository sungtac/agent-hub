"""Bounded subscription-CLI memory extraction before owner review."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .memory_candidates import MemoryCandidateStore
from .ledger import TaskLedger
from .orchestration import ProviderAdapter
from .provider_gateway import ProviderGateway
from .providers import CodexAdapter, ExecutionResult, JsonCliAdapter, ProviderExecutionError, antigravity_executable, claude_adapter
from .routing import ProviderRouter, RouteDecision


ALLOWED_TRIGGERS = {"explicit-request", "task-completed", "repeated-preference"}
ALLOWED_KINDS = {"semantic", "procedural", "episodic"}
MAX_CANDIDATES = 3
MAX_CONTENT_CHARACTERS = 600
MAX_INPUT_CHARACTERS = 6_000
SENSITIVE_PATTERN = re.compile(r"(?i)(?:api[_ -]?key|access[_ -]?token|secret|password|passwd)\s*[:=]\s*\S+|\b(?:sk|ghp)_[A-Za-z0-9_-]{16,}\b|\b\d{6}-[1-4]\d{6}\b")

MEMORY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": MAX_CANDIDATES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "content", "confidence", "evidence"],
                "properties": {
                    "kind": {"type": "string", "enum": sorted(ALLOWED_KINDS)},
                    "content": {"type": "string", "minLength": 1, "maxLength": MAX_CONTENT_CHARACTERS},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string", "minLength": 1, "maxLength": 400},
                },
            },
        }
    },
}


def antigravity_memory_adapter(executable: str | None = None) -> JsonCliAdapter:
    """Use the owner-authenticated CLI; no API key is read or persisted."""

    return JsonCliAdapter(
        "antigravity",
        antigravity_executable(executable),
        ("--output-format", "json", "--mode", "plan", "--sandbox", "--json-schema", json.dumps(MEMORY_SCHEMA, separators=(",", ":")), "--print={prompt}"),
        "response",
    )


def subscription_memory_adapters() -> dict[str, ProviderAdapter]:
    """Return API-free CLI adapters under the shared candidate JSON contract.

    Antigravity can enforce the schema natively. Claude and Codex are prompted
    to return the same schema, then receive the stricter local validation below.
    """

    return {
        "codex": CodexAdapter(),
        "claude": claude_adapter(),
        "antigravity": antigravity_memory_adapter(),
    }


def extract_subscription_candidates(
    adapter: JsonCliAdapter,
    *,
    trigger: str,
    scope: str,
    task_id: str,
    evidence: str,
    messages: Sequence[Mapping[str, Any]],
    candidate_store: MemoryCandidateStore,
    working_directory: Path,
    timeout_seconds: float = 120.0,
) -> tuple[ExecutionResult, list[dict[str, Any]]]:
    """Extract only under an approved trigger and stage validated candidates."""

    prompt = _prompt(trigger, messages)
    result = adapter.execute(prompt, working_directory, timeout_seconds)
    return result, _stage_result(result, scope, task_id, evidence, candidate_store)


def extract_routed_subscription_candidates(
    ledger: TaskLedger,
    router: ProviderRouter,
    adapters: Mapping[str, ProviderAdapter],
    *,
    requested_provider: str | None,
    trigger: str,
    scope: str,
    task_id: str,
    evidence: str,
    messages: Sequence[Mapping[str, Any]],
    candidate_store: MemoryCandidateStore,
    working_directory: Path,
    timeout_seconds: float = 120.0,
) -> tuple[RouteDecision, ExecutionResult, list[dict[str, Any]]]:
    """Extract through the durable provider router; retry only safe failures."""

    dispatch = ProviderGateway(ledger, router, adapters).dispatch(
        task_id=task_id,
        channel="subscription-memory",
        maximum=1,
        prompt=_prompt(trigger, messages),
        working_directory=working_directory,
        requested_provider=requested_provider,
        timeout_seconds=timeout_seconds,
    )
    return dispatch.route, dispatch.result, _stage_result(dispatch.result, scope, task_id, evidence, candidate_store)


def _prompt(trigger: str, messages: Sequence[Mapping[str, Any]]) -> str:
    if trigger not in ALLOWED_TRIGGERS:
        raise PermissionError("Memory extraction requires an explicit approved trigger.")
    serialized = json.dumps(list(messages), ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > MAX_INPUT_CHARACTERS:
        raise ValueError("Memory extraction input is too large.")
    if SENSITIVE_PATTERN.search(serialized):
        raise PermissionError("Potential sensitive credential or identifier detected; extraction blocked.")
    return (
        "Do not call tools, commands, shell, files, or network resources; answer directly from the supplied JSON only. "
        "Extract at most three durable, non-sensitive memory candidates from the JSON conversation below. "
        "Return exactly one JSON object matching this schema: " + json.dumps(MEMORY_SCHEMA, separators=(",", ":")) + ". "
        "Return no candidate for temporary, speculative, or private details. Conversation:\n" + serialized
    )


def _stage_result(result: ExecutionResult, scope: str, task_id: str, evidence: str, candidate_store: MemoryCandidateStore) -> list[dict[str, Any]]:
    if result.status != "succeeded":
        raise ProviderExecutionError(f"Subscription memory extraction failed: {result.status}")
    return candidate_store.ingest(scope, task_id, evidence, _candidate_payload_from_response(result.final_message or ""))


def _candidate_payload_from_response(response: str) -> list[dict[str, Any]]:
    """Accept the first complete strictly-valid candidate document only.

    Antigravity may append a separate JSON tool-summary after its schema-bound
    result.  Parsing one document at a time preserves the schema boundary while
    refusing arbitrary prose or malformed candidate data.
    """

    decoder = json.JSONDecoder()
    position = 0
    while position < len(response):
        while position < len(response) and response[position].isspace():
            position += 1
        if position >= len(response):
            break
        try:
            payload, position = decoder.raw_decode(response, position)
        except json.JSONDecodeError as error:
            raise ValueError("Subscription provider did not return valid candidate JSON.") from error
        try:
            return _validated_candidates(payload)
        except ValueError:
            continue
    raise ValueError("Subscription provider did not return valid candidate JSON.")


def _validated_candidates(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"candidates"} or not isinstance(payload["candidates"], list):
        raise ValueError("Candidate response has an invalid shape.")
    if len(payload["candidates"]) > MAX_CANDIDATES:
        raise ValueError("Candidate response exceeds the allowed candidate count.")
    valid: list[dict[str, Any]] = []
    for candidate in payload["candidates"]:
        if not isinstance(candidate, dict) or set(candidate) != {"kind", "content", "confidence", "evidence"}:
            raise ValueError("Candidate response has invalid fields.")
        content = candidate["content"]
        if (
            candidate["kind"] not in ALLOWED_KINDS
            or not isinstance(content, str)
            or not content.strip()
            or len(content) > MAX_CONTENT_CHARACTERS
            or not isinstance(candidate["confidence"], (int, float))
            or isinstance(candidate["confidence"], bool)
            or not 0 <= candidate["confidence"] <= 1
            or not isinstance(candidate["evidence"], str)
            or not candidate["evidence"].strip()
            or SENSITIVE_PATTERN.search(content)
        ):
            raise ValueError("Candidate failed safety or quality validation.")
        valid.append({"kind": candidate["kind"], "content": content.strip()})
    return valid
