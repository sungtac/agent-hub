"""Safety contract for the optional subscription-CLI LangChain bridge.

This module deliberately contains no CLI invocation and no LangChain model
implementation.  It is the policy boundary that later bridge layers must
honour before they can dispatch a subscription-backed request through the
existing ProviderRouter and TaskLedger.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .ledger import DispatchLimitError, TaskLedger
from .orchestration import ProviderAdapter
from .provider_gateway import ProviderGateway
from .providers import ExecutionResult
from .routing import ProviderRouter, RouteDecision
from .subscription_policy import (
    MAX_TIMEOUT_SECONDS,
    POLICY_FILE_NAME,
    POLICY_SCHEMA_VERSION,
    SUPPORTED_PROVIDERS,
    SubscriptionBridgePolicy,
    SubscriptionBridgeUnavailable,
    load_subscription_bridge_policy,
    save_subscription_bridge_policy,
    subscription_bridge_policy,
)

try:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
except ImportError as error:  # pragma: no cover - exercised in minimal installs
    raise ImportError("Install agent-hub[subscription-langchain] to use the subscription chat facade.") from error


MAX_RESPONSE_CHARACTERS = 16_000
DISPATCH_CHANNEL = "subscription-langchain"


@dataclass(frozen=True)
class SubscriptionTextResponse:
    """The only provider output exposed to the later LangChain facade."""

    provider: str
    text: str


@dataclass(frozen=True)
class SubscriptionDispatch:
    """Redacted outcome of one routed subscription text request."""

    route: RouteDecision
    response: SubscriptionTextResponse


def normalise_subscription_text(result: ExecutionResult) -> SubscriptionTextResponse:
    """Convert an existing Hub result to bounded plain text.

    CLI-specific events, raw JSON envelopes, diagnostics and process metadata
    remain in the adapter/ledger boundary.  The LangChain layer receives only
    a successful, non-empty final message; it cannot infer native tool calls
    or replay provider-specific protocol data.
    """

    if result.status != "succeeded":
        raise SubscriptionBridgeUnavailable(f"Subscription provider execution did not succeed: {result.status}")
    if not isinstance(result.final_message, str):
        raise SubscriptionBridgeUnavailable("Subscription provider returned no text response.")
    text = result.final_message.strip()
    if not text:
        raise SubscriptionBridgeUnavailable("Subscription provider returned an empty text response.")
    if len(text) > MAX_RESPONSE_CHARACTERS:
        raise SubscriptionBridgeUnavailable("Subscription provider response exceeds the bridge safety limit.")
    if result.provider not in SUPPORTED_PROVIDERS:
        raise SubscriptionBridgeUnavailable(f"Unsupported subscription provider response: {result.provider}")
    return SubscriptionTextResponse(provider=result.provider, text=text)




class SubscriptionExecutionService:
    """Route subscription text only through the Hub's durable executor.

    This is deliberately a small composition root.  It has no subprocess
    logic, never writes provider output to its own state, and exposes neither
    diagnostics nor provider event envelopes to callers.  Persistent call
    receipts and restart-safe budgets are added in C5 before the facade is
    exposed through a user-facing configuration path.
    """

    def __init__(
        self,
        ledger: TaskLedger,
        router: ProviderRouter,
        adapters: Mapping[str, ProviderAdapter],
        policy: SubscriptionBridgePolicy,
    ) -> None:
        self._ledger = ledger
        self._router = router
        self._policy = policy
        self._adapters = {
            name: adapter
            for name, adapter in adapters.items()
            if name in policy.allowed_providers and adapter.provider == name
        }

    def invoke(
        self,
        *,
        task_id: str,
        prompt: str,
        working_directory: Path,
        requested_provider: str | None = None,
    ) -> SubscriptionDispatch:
        if requested_provider is not None:
            self._policy.require_dispatchable(requested_provider)
        elif not self._policy.enabled:
            raise SubscriptionBridgeUnavailable("Subscription LangChain bridge is disabled by policy.")
        if not self._adapters:
            raise SubscriptionBridgeUnavailable("No policy-allowed subscription provider adapter is registered.")
        try:
            dispatch = ProviderGateway(self._ledger, self._router, self._adapters).dispatch(
                task_id=task_id,
                channel=DISPATCH_CHANNEL,
                maximum=self._policy.max_calls_per_task,
                prompt=prompt,
                working_directory=working_directory,
                requested_provider=requested_provider,
                timeout_seconds=self._policy.timeout_seconds,
            )
        except DispatchLimitError as error:
            raise SubscriptionBridgeUnavailable("Subscription bridge task dispatch budget is exhausted.") from error
        return SubscriptionDispatch(route=dispatch.route, response=normalise_subscription_text(dispatch.result))


class SubscriptionChatModel(BaseChatModel):
    """Minimal LangChain chat facade over the policy-gated text service.

    It is intentionally not a native provider model: there is no streaming,
    structured tool binding, tool call parsing, or provider conversation state.
    These omissions are explicit failures rather than degraded approximations.
    """

    service: Any
    task_id: str
    working_directory: Path
    requested_provider: str | None = None

    @property
    def _llm_type(self) -> str:
        return "agent-hub-subscription-cli"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"requested_provider": self.requested_provider, "transport": "subscription-cli"}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if stop:
            raise SubscriptionBridgeUnavailable("Subscription chat facade does not support stop sequences.")
        if kwargs:
            raise SubscriptionBridgeUnavailable("Subscription chat facade does not support provider-specific generation options.")
        dispatch = self.service.invoke(
            task_id=self.task_id,
            prompt=_messages_to_prompt(messages),
            working_directory=self.working_directory,
            requested_provider=self.requested_provider,
        )
        message = AIMessage(
            content=dispatch.response.text,
            response_metadata={"provider": dispatch.response.provider, "transport": "subscription-cli"},
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"provider": dispatch.response.provider, "transport": "subscription-cli"},
        )

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        raise SubscriptionBridgeUnavailable("Subscription chat facade does not support tool binding.")

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        raise SubscriptionBridgeUnavailable("Subscription chat facade does not support streaming.")

    def astream(self, *args: Any, **kwargs: Any) -> Any:
        raise SubscriptionBridgeUnavailable("Subscription chat facade does not support async streaming.")


def _messages_to_prompt(messages: list[BaseMessage]) -> str:
    """Use a small, text-only portable transcript with no tool payloads."""

    if not messages:
        raise SubscriptionBridgeUnavailable("Subscription chat facade requires at least one text message.")
    transcript: list[str] = []
    for message in messages:
        if not isinstance(message.content, str) or not message.content.strip():
            raise SubscriptionBridgeUnavailable("Subscription chat facade accepts non-empty text messages only.")
        if getattr(message, "tool_calls", ()) or message.additional_kwargs.get("tool_calls"):
            raise SubscriptionBridgeUnavailable("Subscription chat facade does not accept tool call messages.")
        transcript.append(f"{message.type}: {message.content.strip()}")
    return "\n\n".join(transcript)


class ApprovalGuardedSubscriptionChat:
    """LangGraph approval gate for the text-only subscription chat facade.

    DeepAgents is not accepted here because it calls ``bind_tools`` by design.
    Existing API-model DeepAgents integrations remain separate; this wrapper
    provides the safe subscription equivalent for a single text interaction.
    """

    def __init__(self, model: SubscriptionChatModel, checkpoint_path: Path) -> None:
        try:
            from .workflow import ApprovalWorkflow
        except ImportError as error:
            raise SubscriptionBridgeUnavailable(
                "Subscription approval guard requires agent-hub[workflow]."
            ) from error
        self._model = model
        self._workflow = ApprovalWorkflow(checkpoint_path)

    def close(self) -> None:
        self._workflow.close()

    def start(self, thread_id: str, task_id: str, generation: int, request: str) -> dict[str, Any]:
        return self._workflow.start(thread_id, task_id, generation, request)

    def resume_and_invoke(self, thread_id: str, decision: str, messages: list[BaseMessage]) -> dict[str, Any]:
        approval = self._workflow.resume(thread_id, decision)
        if approval.get("status") != "completed" or approval.get("already_finalized"):
            return {"approval": approval, "model_invoked": False}
        return {"approval": approval, "model_invoked": True, "message": self._model.invoke(messages)}

    def __enter__(self) -> "ApprovalGuardedSubscriptionChat":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()
