"""Explicit, fail-closed binding between one Loadout task and one Hub task."""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any
from pathlib import Path

from .ledger import LedgerError, TASK_ID_PATTERN, TaskLedger, fsync_directory


class LoadoutBridgeError(ValueError):
    """Raised when a task cannot safely be linked to the Hub ledger."""


_TASK_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,99}$")
_HUB_ID_LINE = re.compile(r"(?m)^(?P<prefix>[ \t]*agent_hub_task_id:[ \t]*)(?P<value>[^\r\n#]*)(?P<comment>[ \t]*(?:#.*)?)$")
_YAML_FENCE = re.compile(r"(?ms)^```yaml\s*$.*?^```\s*$")
_LEDGER_BLOCK = re.compile(r"(?ms)^<!-- agent-hub:ledger:start -->.*?^<!-- agent-hub:ledger:end -->\s*")


def _task_file(project_root: Path, task_name: str) -> Path:
    if _TASK_NAME.fullmatch(task_name) is None:
        raise LoadoutBridgeError("Loadout task name must be a safe kebab-case name.")
    root = project_root.expanduser().resolve(strict=True)
    tasks_root = (root / "tasks").resolve(strict=True)
    task_file = (tasks_root / task_name / "task.md").resolve(strict=True)
    try:
        task_file.relative_to(tasks_root)
    except ValueError as error:
        raise LoadoutBridgeError("Loadout task file must stay below project tasks/.") from error
    if not task_file.is_file():
        raise LoadoutBridgeError("Loadout task.md does not exist.")
    return task_file


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _hub_id(content: str) -> str | None:
    match = _HUB_ID_LINE.search(content)
    if match is None:
        return None
    return match.group("value").strip().strip('"\'')


def _ensure_id_slot(content: str) -> str:
    """Add the bridge field inside the first metadata YAML fence, not elsewhere."""
    if _HUB_ID_LINE.search(content):
        return content
    fence = _YAML_FENCE.search(content)
    if fence is None:
        raise LoadoutBridgeError("Loadout task.md has no metadata YAML fence for agent_hub_task_id.")
    block = fence.group(0)
    marker = "```"
    position = block.rfind(marker)
    updated_block = block[:position] + "agent_hub_task_id: N/A\n" + block[position:]
    return content[: fence.start()] + updated_block + content[fence.end() :]


def _title(content: str, task_name: str) -> str:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", content)
    return match.group(1).strip() if match else task_name


def bind_task(ledger: TaskLedger, project_root: Path, task_name: str, title: str, scope: str) -> dict[str, str]:
    """Create a Hub task and atomically bind its id into a fresh Loadout task.

    Rebinding is refused: the original durable task remains the authority for
    every external worker receipt, so silently replacing it would permit a
    second call path for the same human task.
    """
    task_file = _task_file(project_root, task_name)
    content = task_file.read_text(encoding="utf-8")
    match = _HUB_ID_LINE.search(content)
    if match is None:
        raise LoadoutBridgeError("Loadout task.md has no agent_hub_task_id field.")
    current = _hub_id(content)
    if current != "N/A":
        if TASK_ID_PATTERN.fullmatch(current):
            raise LoadoutBridgeError("Loadout task is already bound; rebinding is refused.")
        raise LoadoutBridgeError("agent_hub_task_id must be N/A before binding.")
    created = ledger.create_task(title, scope)
    replacement = f"{match.group('prefix')}{created['task_id']}{match.group('comment')}"
    updated = content[: match.start()] + replacement + content[match.end() :]
    _atomic_write(task_file, updated)
    return {"task_id": created["task_id"], "loadout_task": str(task_file)}


def migrate_tasks(ledger: TaskLedger, project_root: Path, scope: str, apply: bool = False) -> list[dict[str, str]]:
    """Preview or one-way bind every real Loadout task below ``tasks/``.

    A legacy task receives only the missing metadata key and a new Hub task.
    Existing valid bindings are reported but never replaced.
    """
    root = project_root.expanduser().resolve(strict=True)
    tasks_root = (root / "tasks").resolve(strict=True)
    results: list[dict[str, str]] = []
    for candidate in sorted(tasks_root.glob("*/task.md")):
        task_name = candidate.parent.name
        if _TASK_NAME.fullmatch(task_name) is None:
            continue
        try:
            task_file = _task_file(root, task_name)
        except LoadoutBridgeError:
            results.append({"task_name": task_name, "status": "unsafe_path", "task_id": ""})
            continue
        content = task_file.read_text(encoding="utf-8")
        current = _hub_id(content)
        if current and TASK_ID_PATTERN.fullmatch(current):
            results.append({"task_name": task_name, "status": "already_bound", "task_id": current})
            continue
        if current not in {None, "N/A"}:
            results.append({"task_name": task_name, "status": "invalid_binding", "task_id": current})
            continue
        if not apply:
            results.append({"task_name": task_name, "status": "would_bind", "task_id": ""})
            continue
        if current is None:
            _atomic_write(task_file, _ensure_id_slot(content))
        bound = bind_task(ledger, root, task_name, _title(content, task_name), scope)
        results.append({"task_name": task_name, "status": "bound", "task_id": bound["task_id"]})
    return results


def sync_task(ledger: TaskLedger, project_root: Path, task_name: str) -> dict[str, Any]:
    """Render a Hub-owned status block into a bound Loadout task file."""
    task_file = _task_file(project_root, task_name)
    content = task_file.read_text(encoding="utf-8")
    task_id = _hub_id(content)
    if task_id is None or TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise LoadoutBridgeError("Loadout task is not bound to a valid Hub task.")
    snapshot = ledger.get_task(task_id)
    external = snapshot["external_dispatches"]
    pending = sum(item["status"] == "reserved" for item in external)
    block = "\n".join(
        (
            "<!-- agent-hub:ledger:start -->",
            "## Agent Hub Ledger (managed)",
            "",
            f"- Hub task: `{task_id}`",
            f"- Hub status: `{snapshot['status']}`",
            f"- External worker receipts: `{len(external)}` total, `{pending}` pending",
            f"- Last synchronized event count: `{snapshot['event_count']}`",
            "<!-- agent-hub:ledger:end -->",
            "",
        )
    )
    updated = _LEDGER_BLOCK.sub("", content).rstrip() + "\n\n" + block
    _atomic_write(task_file, updated)
    return {"task_id": task_id, "loadout_task": str(task_file), "hub_status": snapshot["status"], "pending_external_dispatches": pending}
