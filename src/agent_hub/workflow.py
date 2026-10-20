"""LangGraph approval/resume workflow with SQLite checkpoint persistence."""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class ApprovalState(TypedDict, total=False):
    task_id: str
    generation: int
    request: str
    status: str
    receipt_key: str


def approval_gate(state: ApprovalState) -> ApprovalState:
    """Interrupt before any material operation; this node remains side-effect free."""

    decision = interrupt(
        {
            "kind": "approval",
            "task_id": state["task_id"],
            "generation": state["generation"],
            "request": state["request"],
        }
    )
    if decision != "approved":
        return {"status": "rejected"}
    return {"status": "approved"}


def route_after_approval(state: ApprovalState) -> str:
    return "record_receipt" if state.get("status") == "approved" else "finish"


def record_receipt(state: ApprovalState) -> ApprovalState:
    """Create a deterministic receipt key after approval, safe to recompute on resume."""

    return {
        "status": "completed",
        "receipt_key": f"{state['task_id']}:{state['generation']}",
    }


def build_graph(checkpointer: SqliteSaver):
    graph = StateGraph(ApprovalState)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("record_receipt", record_receipt)
    graph.add_edge(START, "approval_gate")
    graph.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {"record_receipt": "record_receipt", "finish": END},
    )
    graph.add_edge("record_receipt", END)
    return graph.compile(checkpointer=checkpointer)


class ApprovalWorkflow:
    """Own a SQLite checkpointer for one local Hub runtime."""

    def __init__(self, checkpoint_path: Path) -> None:
        path = checkpoint_path.expanduser().resolve(strict=False)
        if not path.parent.is_dir():
            raise ValueError(f"Checkpoint parent does not exist: {path.parent}")
        self._connection = sqlite3.connect(path, check_same_thread=False)
        if os.name != "nt":
            path.chmod(0o600)
        self._checkpointer = SqliteSaver(self._connection)
        self._graph = build_graph(self._checkpointer)

    def close(self) -> None:
        self._connection.close()

    def start(self, thread_id: str, task_id: str, generation: int, request: str) -> dict:
        return self._graph.invoke(
            {"task_id": task_id, "generation": generation, "request": request},
            config={"configurable": {"thread_id": thread_id}},
        )

    def resume(self, thread_id: str, decision: str) -> dict:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Approval decision must be approved or rejected.")
        config = {"configurable": {"thread_id": thread_id}}
        prior = dict(self._graph.get_state(config).values)
        if prior.get("status") in {"completed", "rejected"}:
            # LangGraph can replay a completed thread when commanded again.
            # Marking this explicitly makes downstream material actions
            # idempotent across duplicate UI events and process restarts.
            return {**prior, "already_finalized": True}
        return self._graph.invoke(Command(resume=decision), config=config)

    def __enter__(self) -> "ApprovalWorkflow":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
