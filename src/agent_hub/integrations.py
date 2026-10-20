"""Narrow LangMem/DeepAgents bridge that preserves Hub policy boundaries."""

from __future__ import annotations

from pathlib import Path
import os
import re
from typing import Any, Mapping, Sequence

class IntegrationUnavailable(RuntimeError):
    """Raised when an optional integration is absent or unsafe to initialise."""


class ApprovalGuardedDeepAgent:
    """Require a durable LangGraph approval before invoking a read-only DeepAgent.

    The approval workflow owns restart-safe state. The wrapped DeepAgent is not
    called at all on an interrupt or rejection, so a subagent cannot bypass the
    owner's decision merely by being recreated after a process restart.
    """

    def __init__(self, agent: Any, checkpoint_path: Path) -> None:
        try:
            from .workflow import ApprovalWorkflow
        except ImportError as error:
            raise IntegrationUnavailable("Install agent-hub[workflow] to use approval-guarded agents.") from error
        self._agent = agent
        self._workflow = ApprovalWorkflow(checkpoint_path)

    def close(self) -> None:
        self._workflow.close()

    def start(self, thread_id: str, task_id: str, generation: int, request: str) -> dict[str, Any]:
        return self._workflow.start(thread_id, task_id, generation, request)

    def resume_and_invoke(
        self,
        thread_id: str,
        decision: str,
        agent_input: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        approval = self._workflow.resume(thread_id, decision)
        if approval.get("status") != "completed" or approval.get("already_finalized"):
            return {"approval": approval, "agent_invoked": False}
        return {
            "approval": approval,
            "agent_invoked": True,
            "agent_result": self._agent.invoke(dict(agent_input), config=dict(config or {})),
        }

    def __enter__(self) -> "ApprovalGuardedDeepAgent":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


def configured_memory_model(environment: dict[str, str] | None = None) -> str:
    """Read only the model identifier; provider credentials stay in the environment.

    LangMem accepts LangChain model identifiers such as ``openai:gpt-4.1-mini``
    or ``anthropic:claude-sonnet-4-5``.  A CLI subscription is deliberately not
    treated as an API credential.
    """

    value = (environment or os.environ).get("AGENT_HUB_LANGMEM_MODEL", "").strip()
    if not value or ":" not in value:
        raise IntegrationUnavailable("Set AGENT_HUB_LANGMEM_MODEL to a LangChain provider:model identifier before live extraction.")
    return value


def build_configured_memory_manager(scope: str, environment: dict[str, str] | None = None) -> Any:
    return build_memory_manager(configured_memory_model(environment), scope)


def build_memory_manager(model: Any, scope: str) -> Any:
    """Create a LangMem manager with a scope namespace and no auto-deletion.

    Candidate extraction may be automatic later, but destructive memory actions
    remain disabled here; the Hub knowledge tombstone process is authoritative.
    """

    if scope not in {"company", "personal", "shared", "quarantine"}:
        raise ValueError("Invalid knowledge scope.")
    try:
        from langmem import create_memory_store_manager
        from langgraph.store.memory import InMemoryStore
    except ImportError as error:
        raise IntegrationUnavailable("LangMem is not installed in this environment.") from error
    return create_memory_store_manager(
        model,
        enable_deletes=False,
        store=InMemoryStore(),
        namespace=("agent-hub", "{langgraph_user_id}", scope),
    )


def build_deep_agent(
    model: Any,
    skill_directories: Sequence[Path],
    memory_documents: Sequence[Path] = (),
    *,
    workspace_root: Path,
) -> Any:
    """Build a read-only DeepAgents graph over an explicit workspace and assets.

    Skills and memory are mounted as separate read routes. The main agent only
    receives listing/search/read tools. The default subagent inherits a deny
    rule for all writes, and the FilesystemBackend does not provide shell
    execution.
    """

    workspace = _validated_path(workspace_root, "workspace", directory=True)
    skill_paths = _existing_directories(skill_directories, "skill")
    memory_paths = _existing_files(memory_documents, "memory")
    try:
        from deepagents import FilesystemMiddleware, FilesystemPermission, create_deep_agent
        from deepagents.backends import CompositeBackend, FilesystemBackend
    except ImportError as error:
        raise IntegrationUnavailable("DeepAgents is not installed in this environment.") from error

    routes: dict[str, Any] = {}
    skill_sources: list[str] = []
    memory_sources: list[str] = []
    for index, path in enumerate(skill_paths):
        mount = f"/hub-skills-{index}"
        routes[mount] = FilesystemBackend(root_dir=Path(path).parent)
        skill_sources.append(f"{mount}/{Path(path).name}")
    for index, path in enumerate(memory_paths):
        mount = f"/hub-memory-{index}"
        routes[mount] = FilesystemBackend(root_dir=Path(path).parent)
        memory_sources.append(f"{mount}/{Path(path).name}")

    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=workspace),
        routes=routes,
        artifacts_root="/",
    )
    deny_writes = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
    read_only_filesystem = FilesystemMiddleware(
        backend=backend,
        tools=["ls", "read_file", "glob", "grep"],
        _permissions=deny_writes,
    )
    return create_deep_agent(
        model=model,
        skills=skill_sources,
        memory=memory_sources,
        backend=backend,
        middleware=[read_only_filesystem],
        permissions=deny_writes,
    )


def build_scoped_deep_agent(
    model: Any,
    *,
    skill_registry: Any,
    task_scope: str,
    skill_names: Sequence[str],
    knowledge_root: Path,
    memory_ids: Sequence[str],
    workspace_root: Path,
) -> Any:
    """Resolve only approved same-scope skills and live memories before graph creation."""
    from .knowledge import SCOPES

    if task_scope not in SCOPES:
        raise ValueError("Invalid task scope.")
    records = {record.name: record for record in skill_registry.records()}
    skill_directories: list[Path] = []
    for name in skill_names:
        record = records.get(name)
        if record is None or record.status != "approved" or record.kind == "hosted-only":
            raise PermissionError(f"Skill is missing, unapproved, or hosted-only: {name}")
        if record.scope not in {task_scope, "shared"}:
            raise PermissionError(f"Skill scope does not match task scope: {name}")
        skill_directory = Path(record.path).expanduser()
        if skill_directory.is_symlink() or not (skill_directory / "SKILL.md").is_file():
            raise PermissionError(f"Registered skill asset is not a regular local skill: {name}")
        skill_directories.append(skill_directory)

    scoped_knowledge = _validated_path(knowledge_root, "knowledge root", directory=True) / task_scope
    memory_documents: list[Path] = []
    for identifier in memory_ids:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,79}", identifier):
            raise ValueError("Invalid knowledge identifier.")
        item_directory = scoped_knowledge / identifier
        if item_directory.is_symlink() or (item_directory / "tombstone.json").exists():
            raise PermissionError("Knowledge item is deleted or unavailable.")
        versions = sorted(
            (path for path in item_directory.glob("v*.md") if path.stem[1:].isdigit() and not path.is_symlink()),
            key=lambda path: int(path.stem[1:]),
        )
        if not versions:
            raise PermissionError(f"No active knowledge document: {identifier}")
        memory_documents.append(versions[-1])
    return build_deep_agent(
        model,
        skill_directories,
        memory_documents,
        workspace_root=workspace_root,
    )


def build_approval_guarded_scoped_deep_agent(
    model: Any,
    *,
    checkpoint_path: Path,
    skill_registry: Any,
    task_scope: str,
    skill_names: Sequence[str],
    knowledge_root: Path,
    memory_ids: Sequence[str],
    workspace_root: Path,
) -> ApprovalGuardedDeepAgent:
    """Build a scoped read-only DeepAgent whose invocation is approval-gated."""

    return ApprovalGuardedDeepAgent(
        build_scoped_deep_agent(
            model,
            skill_registry=skill_registry,
            task_scope=task_scope,
            skill_names=skill_names,
            knowledge_root=knowledge_root,
            memory_ids=memory_ids,
            workspace_root=workspace_root,
        ),
        checkpoint_path,
    )


def _existing_directories(paths: Sequence[Path], label: str) -> list[str]:
    resolved: list[str] = []
    for path in paths:
        original = path.expanduser()
        if _has_symlink_component(original):
            raise ValueError(f"Invalid {label} directory: {path}")
        candidate = original.resolve(strict=True)
        if not candidate.is_dir():
            raise ValueError(f"Invalid {label} directory: {path}")
        resolved.append(str(candidate))
    return resolved


def _existing_files(paths: Sequence[Path], label: str) -> list[str]:
    resolved: list[str] = []
    for path in paths:
        original = path.expanduser()
        if _has_symlink_component(original):
            raise ValueError(f"Invalid {label} file: {path}")
        candidate = original.resolve(strict=True)
        if not candidate.is_file():
            raise ValueError(f"Invalid {label} file: {path}")
        resolved.append(str(candidate))
    return resolved


def _validated_path(path: Path, label: str, *, directory: bool) -> Path:
    original = path.expanduser()
    if _has_symlink_component(original):
        raise ValueError(f"Invalid {label} path: {path}")
    candidate = original.resolve(strict=True)
    if directory and not candidate.is_dir():
        raise ValueError(f"Invalid {label} path: {path}")
    return candidate


def _has_symlink_component(path: Path) -> bool:
    return any(part.is_symlink() for part in (path, *path.parents))
