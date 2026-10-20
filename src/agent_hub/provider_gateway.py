"""Single durable gateway for subscription-backed provider dispatches."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .ledger import DispatchLimitError, TaskLedger
from .orchestration import ProviderAdapter, run_routed_task
from .providers import ExecutionResult
from .routing import ProviderRouter, RouteDecision


@dataclass(frozen=True)
class GatewayDispatch:
    """One logical request, including any safe provider handoff."""

    route: RouteDecision
    result: ExecutionResult


class ProviderGateway:
    """Reserve once, route once, and record all provider attempts in one ledger.

    A reservation is intentionally made before the first external process
    starts.  Therefore a crash may consume a request budget, but never causes
    an unrecorded duplicate invocation after restart.
    """

    def __init__(self, ledger: TaskLedger, router: ProviderRouter, adapters: Mapping[str, ProviderAdapter]) -> None:
        self._ledger = ledger
        self._router = router
        self._adapters = dict(adapters)

    def dispatch(
        self,
        *,
        task_id: str,
        channel: str,
        maximum: int,
        prompt: str,
        working_directory: Path,
        requested_provider: str | None = None,
        timeout_seconds: float = 120.0,
        allow_handoff: bool = True,
        record_shared_handoff: bool = False,
    ) -> GatewayDispatch:
        if not prompt.strip():
            raise ValueError("Gateway prompt must not be empty.")
        try:
            self._ledger.reserve_dispatch(task_id, channel, maximum)
        except DispatchLimitError:
            raise
        route, result = run_routed_task(
            self._ledger,
            self._router,
            self._adapters,
            task_id,
            prompt,
            working_directory,
            requested_provider=requested_provider,
            timeout_seconds=timeout_seconds,
            allow_handoff=allow_handoff,
            record_shared_handoff=record_shared_handoff,
        )
        return GatewayDispatch(route, result)

    def reserve_external(
        self, *, task_id: str, channel: str, maximum: int, fingerprint: str, provider: str
    ) -> dict[str, object]:
        """Reserve a Loadout-owned process under the same durable task ledger."""
        return self._ledger.reserve_external_dispatch(task_id, channel, maximum, fingerprint, provider)

    def complete_external(
        self, *, task_id: str, dispatch_id: str, status: str, return_code: int | None
    ) -> dict[str, object]:
        return self._ledger.complete_external_dispatch(task_id, dispatch_id, status, return_code)
