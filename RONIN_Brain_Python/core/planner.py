"""VYRX Planner: autonomous ReAct agent (Thought -> Action -> Observation).

Every user request flows through:

    1. Planner   : identify semantic capability requirements, query the active
                   Registry, and inject the current capability plan
    2. Agent loop: LLM emits native function calls or ``<tool>`` JSON tags
    3. Executor  : Brain tools run inline (memory / web / shell); device tools
                   (open app / read screen / click / …) are dispatched to the
                   Kotlin Body as a pending ``AgentAction``
    4. Observer  : the Body POSTs the execution result to ``/agent/result``
                   and the loop continues — verify, self-correct, repeat —
                   until the goal is achieved and the agent speaks plain text.

Each step emits an action-log entry and a state change, which the body
consumes through ``GET /api/state``, ``GET /api/action_logs`` and the SSE
stream ``GET /api/events``.

``POST /ask_ronin`` can additionally run the whole loop *inside* a live SSE
stream (see :mod:`core.streaming`): ``thinking`` / ``thought`` / ``tool_call``
/ ``observation`` / ``self_correction`` frames are emitted as they happen, the
final plain-text answer is streamed chunk-by-chunk for the Body's typing
effect, and a dispatched device tool is awaited over the same connection
(:data:`core.streaming.TOOL_RESULT_BRIDGE`) instead of ending the request.
Streaming is purely additive: with no stream bound, every function below behaves
exactly as it always did.

Legacy keyword routes (``legacy_route``) survive as offline fallbacks when no
AI provider is reachable, so explicit commands keep working with zero keys.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from core.action_log import ActionLog
from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    ExecutionOutcome,
    ExecutionResult,
    SemanticCapabilityType,
    classify_exception,
    outcome_for_failure,
)
from core.capability_executor import execute_capability as _execute_capability_fn
from core.execution_boundary import (
    execute_boundary,
    execute_llm_boundary,
    execute_search_boundary,
    execute_tool_boundary,
)
from core.knowledge.engine import KnowledgeEngine
from core.knowledge.integration import (
    KNOWLEDGE_CAPABILITY_ID,
    KNOWLEDGE_TOOL_NAME,
    knowledge_tool_schema,
)
from core.knowledge.watcher import KnowledgeIngestionWatcher
from core.local_reasoning import LOCAL_REASONING_CAPABILITY_ID, LocalReasoningAdapter
from core.llm_handler import (
    SYSTEM_PROMPT,
    LLMError,
    build_tool_instructions,
    complete,
    extract_tool_calls,
    strip_tool_tags,
)
from core.provider_manager import ProviderManager
from core.registry import CapabilityRegistry
from core.router import legacy_route, route_request
from core.schemas import AgentAction, AskRequest, AskResponse, ToolResultRequest, UpdateProposal
from core.state_manager import StateManager
from core.stats import StatsTracker
from core.streaming import (
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_OBSERVATION,
    EVENT_SELF_CORRECTION,
    EVENT_START,
    EVENT_THINKING,
    EVENT_TOOL_CALL,
    TOOL_RESULT_BRIDGE,
    TOOL_RESULT_TIMEOUT_SECONDS,
    AgentStreamer,
    bind_stream,
    current_stream,
    emit_event,
    unbind_stream,
)
from core.tool_registry import execute_tool, get_available_tools
from core.tools_catalog import (
    HIDDEN_LEGACY_TOOLS,
    device_tool_schemas,
    is_device_tool,
    tool_label,
)
from memory.db_manager import recent_history, save_conversation, search_facts, store_fact
from memory.memory_engine import MemoryEngine
from tools.system_control import command_for_request
from tools.web_search import search


# Keep the original callables so the compatibility shim below can preserve
# the planner's established test/injection seam while production execution
# always goes through the specialized normalized boundaries.  The shim is
# intentionally local to this module; it does not change boundary signatures
# or pass registry state into them.
_ORIGINAL_COMPLETE = complete
_ORIGINAL_EXECUTE_TOOL = execute_tool
_ORIGINAL_EXECUTE_LLM_BOUNDARY = execute_llm_boundary
_ORIGINAL_EXECUTE_TOOL_BOUNDARY = execute_tool_boundary


#: Maximum LLM round-trips per user request (device callbacks included).
MAX_AGENT_STEPS = 8

#: Maximum alternative implementations attempted for one failed sub-goal.
#: This is deliberately separate from the ReAct step budget.
MAX_CAPABILITY_RECOVERY_ATTEMPTS = 2

#: Failure classes for which backend substitution is appropriate by default.
#: Validation, authorization, policy, and deterministic input failures remain
#: available to the ReAct correction path instead of being blindly swapped.
_RECOVERY_ELIGIBLE_FAILURES = frozenset({
    CanonicalFailureClass.TRANSIENT,
    CanonicalFailureClass.NETWORK_ISOLATED,
    CanonicalFailureClass.UNSUPPORTED_OPERATION,
    CanonicalFailureClass.UNKNOWN_FATAL,
})

#: Truncation for tool observations fed back into the context window.
MAX_OBSERVATION_CHARS = 6000

#: Politeness gap between ReAct steps (GROQ rate limit bypass). Kept as a
#: module constant so tests and local runs can retune it without patching
#: ``asyncio.sleep`` globally.
RATE_LIMIT_PAUSE_SECONDS: float = 2.0


# ---------------------------------------------------------------------------
# Capability-aware task planning
# ---------------------------------------------------------------------------
#
# This is intentionally a capability plan, not a second execution graph.
# ``AgentAction`` and the existing ReAct message/tool protocol remain the
# execution representation.  These small records only describe which semantic
# capabilities a task/sub-goal needs and which registered implementations are
# currently eligible to satisfy it.


@dataclass(frozen=True)
class CapabilityRequirement:
    """One semantic capability requirement for one planner sub-goal."""

    sub_goal_id: str
    description: str
    capability_type: SemanticCapabilityType
    requires_internet: bool | None = None
    requires_local: bool | None = None
    required_permissions: tuple[str, ...] = ()


@dataclass
class CapabilitySubGoal:
    """Capability requirement plus current selectable implementations.

    ``candidates`` contains only implementations that the current planner
    policy may select: AVAILABLE and DEGRADED.  UNKNOWN implementations are
    retained separately so uncertainty is visible without being mislabeled as
    healthy or selectable.
    """

    requirement: CapabilityRequirement
    candidates: tuple[CapabilityDescriptor, ...] = ()
    unknown_candidates: tuple[CapabilityDescriptor, ...] = ()
    unavailable_candidates: tuple[CapabilityDescriptor, ...] = ()
    selected_capability_id: str | None = None
    # Per-plan-instance execution history. This is intentionally scoped to
    # this sub-goal so an alternative in one semantic domain cannot blacklist
    # an unrelated sub-goal.
    attempted_capabilities: list[str] = field(default_factory=list)
    # Internal, sanitized recovery audit trail. It is not included in user
    # observations or prompts.
    recovery_history: list[dict[str, Any]] = field(default_factory=list)

    def mark_attempted(self, capability_id: str | None) -> None:
        """Record one attempted implementation exactly once."""
        if capability_id and capability_id not in self.attempted_capabilities:
            self.attempted_capabilities.append(capability_id)

    def record_recovery_event(self, **event: Any) -> None:
        """Keep a sanitized internal record of one recovery decision."""
        self.recovery_history.append({
            key: value for key, value in event.items()
            if key in {
                "event", "capability_id", "failure_class", "outcome",
                "reason", "selected", "attempt",
            }
        })

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(candidate.id for candidate in self.candidates)

    @property
    def unknown_candidate_ids(self) -> tuple[str, ...]:
        return tuple(candidate.id for candidate in self.unknown_candidates)

    @property
    def unavailable_candidate_ids(self) -> tuple[str, ...]:
        return tuple(candidate.id for candidate in self.unavailable_candidates)

    @property
    def available(self) -> bool:
        """Whether this sub-goal has a currently selectable implementation."""
        return bool(self.candidates)

    @property
    def health_state(self) -> CapabilityHealth:
        """Summarize Registry health without collapsing UNKNOWN into healthy."""
        if any(candidate.health == CapabilityHealth.AVAILABLE for candidate in self.candidates):
            return CapabilityHealth.AVAILABLE
        if self.candidates:
            return CapabilityHealth.DEGRADED
        if self.unknown_candidates:
            return CapabilityHealth.UNKNOWN
        return CapabilityHealth.UNAVAILABLE


@dataclass
class CapabilityPlan:
    """A task-level semantic plan; actions still belong to the ReAct loop."""

    task: str
    sub_goals: list[CapabilitySubGoal] = field(default_factory=list)
    outcomes: dict[str, ExecutionOutcome] = field(default_factory=dict)

    def sub_goal(self, sub_goal_id: str) -> CapabilitySubGoal | None:
        return next((item for item in self.sub_goals if item.requirement.sub_goal_id == sub_goal_id), None)

    def requirement_for_type(self, capability_type: SemanticCapabilityType) -> CapabilityRequirement | None:
        for item in self.sub_goals:
            if item.requirement.capability_type == capability_type:
                return item.requirement
        return None

    @property
    def outcome(self) -> ExecutionOutcome | None:
        """Summarize completed sub-goals without hiding partial success."""
        if not self.outcomes:
            return None
        values = list(self.outcomes.values())
        # PARTIAL_SUCCESS is a producer assertion, never an aggregate guess
        # made from one successful and one failed sub-goal.
        if any(value == ExecutionOutcome.PARTIAL_SUCCESS for value in values):
            return ExecutionOutcome.PARTIAL_SUCCESS
        for value in values:
            if value != ExecutionOutcome.SUCCESS:
                return value
        return ExecutionOutcome.SUCCESS

    def record_outcome(self, sub_goal_id: str, result: ExecutionResult) -> None:
        """Record one sub-goal result; unrelated sub-goals remain untouched."""
        current = self.sub_goal(sub_goal_id)
        if current is not None:
            self.outcomes[sub_goal_id] = result.outcome
            current.mark_attempted(result.capability_id or current.selected_capability_id)


# The planner consumes these states; it does not calculate or mutate health.
# UNKNOWN is deliberately not a selectable/dispatchable candidate.  The
# separate ``unknown_candidates`` field preserves uncertainty for a future,
# explicitly defined validation policy without implementing that policy here.
_USABLE_CAPABILITY_HEALTH = frozenset({
    CapabilityHealth.AVAILABLE,
    CapabilityHealth.DEGRADED,
})
_CAPABILITY_HEALTH_ORDER = {
    CapabilityHealth.AVAILABLE: 0,
    CapabilityHealth.DEGRADED: 1,
    CapabilityHealth.UNKNOWN: 2,
}

# Semantic mapping for the existing action/tool surface.  No provider or model
# identities appear here.
_TOOL_CAPABILITY_TYPES: dict[str, SemanticCapabilityType] = {
    "search": SemanticCapabilityType.WEB_RETRIEVAL,
    "web_search": SemanticCapabilityType.WEB_RETRIEVAL,
    KNOWLEDGE_TOOL_NAME: SemanticCapabilityType.LOCAL_KNOWLEDGE_SEARCH,
    "save_memory": SemanticCapabilityType.MEMORY_PERSISTENCE,
    "retrieve_memory": SemanticCapabilityType.MEMORY_PERSISTENCE,
    "read_file": SemanticCapabilityType.FILE_SYSTEM_IO,
    "write_file": SemanticCapabilityType.FILE_SYSTEM_IO,
    "list_files": SemanticCapabilityType.FILE_SYSTEM_IO,
    "run_termux_command": SemanticCapabilityType.SYSTEM_COMMAND,
    "open_app": SemanticCapabilityType.DEVICE_INTERACTION,
    "list_apps": SemanticCapabilityType.DEVICE_INTERACTION,
    "read_screen": SemanticCapabilityType.DEVICE_INTERACTION,
    "click": SemanticCapabilityType.DEVICE_INTERACTION,
    "click_xy": SemanticCapabilityType.DEVICE_INTERACTION,
    "click_node": SemanticCapabilityType.DEVICE_INTERACTION,
    "set_text": SemanticCapabilityType.DEVICE_INTERACTION,
    "scroll": SemanticCapabilityType.DEVICE_INTERACTION,
    "press_back": SemanticCapabilityType.DEVICE_INTERACTION,
    "press_home": SemanticCapabilityType.DEVICE_INTERACTION,
    "get_notifications": SemanticCapabilityType.DEVICE_INTERACTION,
}


def _add_capability_requirement(
    requirements: list[CapabilityRequirement],
    *,
    sub_goal_id: str,
    description: str,
    capability_type: SemanticCapabilityType,
    requires_internet: bool | None = None,
    requires_local: bool | None = None,
    required_permissions: tuple[str, ...] = (),
) -> None:
    if any(item.capability_type == capability_type for item in requirements):
        return
    requirements.append(CapabilityRequirement(
        sub_goal_id=sub_goal_id,
        description=description,
        capability_type=capability_type,
        requires_internet=requires_internet,
        requires_local=requires_local,
        required_permissions=required_permissions,
    ))


def identify_required_capabilities(task: str) -> tuple[CapabilityRequirement, ...]:
    """Infer semantic requirements without selecting a vendor or model.

    This is deliberately conservative: it identifies requirements implied by
    the request, while the Registry remains the source of truth for whether an
    implementation exists and is usable.  It does not implement retrieval,
    routing policy, or an offline fallback chain.
    """
    normalized = " ".join(str(task or "").casefold().split())
    requirements: list[CapabilityRequirement] = []

    # Every current ReAct task needs a reasoning step.  The meta-capability
    # used for health bookkeeping is excluded later from implementation
    # candidates, so this remains a semantic requirement rather than a vendor.
    _add_capability_requirement(
        requirements,
        sub_goal_id="subgoal-reasoning",
        description="Understand the request and orchestrate the next ReAct step.",
        capability_type=SemanticCapabilityType.REASONING,
    )

    local_knowledge_requested = (
        "local knowledge" in normalized
        or "knowledge base" in normalized
        or "indexed knowledge" in normalized
        or "knowledge index" in normalized
        or "search my documents" in normalized
        or "search local documents" in normalized
        or "local docs" in normalized
        or "offline knowledge" in normalized
    )

    if any(token in normalized for token in (
        "current", "latest", "today", "news", "web", "internet", "external",
        "online", "up-to-date", "up to date",
    )) or ("search" in normalized and not local_knowledge_requested):
        _add_capability_requirement(
            requirements,
            sub_goal_id="subgoal-web-retrieval",
            description="Retrieve current external information.",
            capability_type=SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True,
        )

    if not local_knowledge_requested and any(token in normalized for token in (
        "file", "files", "project", "codebase", "repository", "repo", "folder",
        "directory", "path", "local", "workspace",
    )):
        _add_capability_requirement(
            requirements,
            sub_goal_id="subgoal-local-files",
            description="Inspect or operate on local project/file data.",
            capability_type=SemanticCapabilityType.FILE_SYSTEM_IO,
            requires_local=True,
        )

    if local_knowledge_requested:
        _add_capability_requirement(
            requirements,
            sub_goal_id="subgoal-local-knowledge",
            description="Search locally available knowledge.",
            capability_type=SemanticCapabilityType.LOCAL_KNOWLEDGE_SEARCH,
            requires_local=True,
        )

    if any(token in normalized for token in ("compare", "comparison", "contrast", "synthesize")):
        _add_capability_requirement(
            requirements,
            sub_goal_id="subgoal-synthesis",
            description="Compare or synthesize the independently gathered results.",
            capability_type=SemanticCapabilityType.SYNTHESIS,
        )

    if any(token in normalized for token in (
        "open app", "launch", "click", "tap", "screen", "notification", "device",
        "phone",
    )):
        _add_capability_requirement(
            requirements,
            sub_goal_id="subgoal-device",
            description="Interact with the Android device.",
            capability_type=SemanticCapabilityType.DEVICE_INTERACTION,
        )

    if any(token in normalized for token in (
        "calculate", "compute", "formula", "sort", "transform", "arithmetic",
    )):
        _add_capability_requirement(
            requirements,
            sub_goal_id="subgoal-compute",
            description="Perform deterministic computation.",
            capability_type=SemanticCapabilityType.DETERMINISTIC_COMPUTE,
        )

    if any(token in normalized for token in (
        "remember", "memory", "recall preference", "save this",
    )):
        _add_capability_requirement(
            requirements,
            sub_goal_id="subgoal-memory",
            description="Persist or recall user-provided memory.",
            capability_type=SemanticCapabilityType.MEMORY_PERSISTENCE,
            requires_local=True,
        )

    return tuple(requirements)


def _descriptor_matches_requirement(
    descriptor: CapabilityDescriptor,
    requirement: CapabilityRequirement,
) -> bool:
    """Apply semantic/dependency matching without inspecting health."""
    if descriptor.id == _REASONING_META_CAP_ID:
        return False
    if requirement.requires_internet is not None \
            and descriptor.requires_internet != requirement.requires_internet:
        return False
    if requirement.requires_local is not None \
            and descriptor.is_local != requirement.requires_local:
        return False
    if requirement.required_permissions and not set(requirement.required_permissions).issubset(
        set(descriptor.required_permissions)
    ):
        return False
    return True


def _candidate_satisfies(
    descriptor: CapabilityDescriptor,
    requirement: CapabilityRequirement,
    *,
    include_unknown: bool = False,
) -> bool:
    allowed_health = _USABLE_CAPABILITY_HEALTH
    if include_unknown:
        allowed_health = allowed_health | {CapabilityHealth.UNKNOWN}
    if descriptor.health not in allowed_health:
        return False
    return _descriptor_matches_requirement(descriptor, requirement)


def query_capability_candidates(
    registry: CapabilityRegistry | None,
    requirement: CapabilityRequirement,
    *,
    include_unknown: bool = False,
) -> tuple[CapabilityDescriptor, ...]:
    """Query Registry-backed implementations for planner selection.

    By default only AVAILABLE and DEGRADED implementations are selectable.
    ``include_unknown`` is an explicit inspection hook for a future validation
    or probing policy; no planner path enables it for dispatch selection.
    UNAVAILABLE implementations are always excluded.
    """
    if registry is None:
        return ()
    descriptors = registry.query_by_semantic_type(requirement.capability_type)
    eligible = [
        descriptor
        for descriptor in descriptors
        if _candidate_satisfies(
            descriptor,
            requirement,
            include_unknown=include_unknown,
        )
    ]
    # This is only a deterministic presentation preference.  It does not
    # encode a primary/secondary/offline fallback chain or a health policy.
    eligible.sort(key=lambda descriptor: (
        _CAPABILITY_HEALTH_ORDER.get(descriptor.health, 99), descriptor.id,
    ))
    return tuple(eligible)


def build_capability_plan(
    task: str,
    registry: CapabilityRegistry | None,
) -> CapabilityPlan:
    """Identify requirements and attach Registry-backed candidates."""
    sub_goals: list[CapabilitySubGoal] = []
    for requirement in identify_required_capabilities(task):
        candidates = query_capability_candidates(registry, requirement)
        matching = [] if registry is None else [
            descriptor
            for descriptor in registry.query_by_semantic_type(requirement.capability_type)
            if _descriptor_matches_requirement(descriptor, requirement)
        ]
        unknown = tuple(
            descriptor for descriptor in matching
            if descriptor.health == CapabilityHealth.UNKNOWN
        )
        unavailable = tuple(
            descriptor for descriptor in matching
            if descriptor.health == CapabilityHealth.UNAVAILABLE
        )
        sub_goals.append(CapabilitySubGoal(
            requirement=requirement,
            candidates=candidates,
            unknown_candidates=unknown,
            unavailable_candidates=unavailable,
            selected_capability_id=candidates[0].id if candidates else None,
        ))
    return CapabilityPlan(task=task, sub_goals=sub_goals)


def _capability_plan_prompt(
    plan: CapabilityPlan,
    *,
    affected_sub_goal_id: str | None = None,
) -> str:
    """Describe semantic availability to the reasoning model, not internals."""
    lines = ["", "[CAPABILITY PLAN — semantic requirements and current availability]"]
    for sub_goal in plan.sub_goals:
        requirement = sub_goal.requirement
        state = sub_goal.health_state.value
        marker = " (reconsider this sub-goal)" if requirement.sub_goal_id == affected_sub_goal_id else ""
        lines.append(
            f"- {requirement.sub_goal_id}: {requirement.capability_type.value} — {state}{marker}"
        )
    if affected_sub_goal_id:
        lines.append("Only reconsider the affected sub-goal; retain independent verified work.")
    return "\n".join(lines)


def _tool_semantic_type(tool_name: str) -> SemanticCapabilityType | None:
    return _TOOL_CAPABILITY_TYPES.get(str(tool_name or "").strip())


def _filter_provider_payload_by_capability_plan(
    provider_payload: list[dict],
    plan: CapabilityPlan | None,
) -> list[dict]:
    """Apply Registry health without presenting UNKNOWN as healthy.

    AVAILABLE and DEGRADED providers are selected from the plan.  UNKNOWN
    providers remain in the legacy transport input only when no validated
    implementation exists; the plan labels that sub-goal ``unknown`` and does
    not select one.  This preserves first-use compatibility without inventing
    a probing subsystem or changing execution-boundary contracts.  Providers
    whose sub-goal has no known implementation are not attempted.
    """
    if plan is None:
        return provider_payload
    reasoning = plan.sub_goal("subgoal-reasoning")
    if reasoning is None:
        return provider_payload
    if reasoning.health_state == CapabilityHealth.UNKNOWN:
        blocked = set(reasoning.unavailable_candidate_ids)
        return [provider for provider in provider_payload
                if f"provider-{str(provider.get('provider') or '').lower().strip()}"
                not in blocked]
    if not reasoning.available:
        # No descriptor at all means a manually-created/legacy context has not
        # run application bootstrap; preserve its old transport behavior.
        return provider_payload if not reasoning.unavailable_candidate_ids else [
            provider for provider in provider_payload
            if f"provider-{str(provider.get('provider') or '').lower().strip()}"
            not in set(reasoning.unavailable_candidate_ids)
        ]
    selected_ids = set(reasoning.candidate_ids)
    return [
        provider for provider in provider_payload
        if f"provider-{str(provider.get('provider') or '').lower().strip()}"
        in selected_ids
    ]


def _filter_tools_by_capability_plan(
    tools: list[dict],
    plan: CapabilityPlan | None,
) -> list[dict]:
    """Remove tools whose required semantic capability has no candidate."""
    if plan is None:
        return tools
    unavailable_types = {
        sub_goal.requirement.capability_type
        for sub_goal in plan.sub_goals
        if sub_goal.health_state == CapabilityHealth.UNAVAILABLE
        and sub_goal.requirement.capability_type != SemanticCapabilityType.REASONING
    }
    if not unavailable_types:
        return tools
    return [
        tool for tool in tools
        if _tool_semantic_type(tool.get("function", {}).get("name", ""))
        not in unavailable_types
    ]


def _sub_goal_for_tool(plan: CapabilityPlan | None, tool_name: str) -> str | None:
    if plan is None:
        return None
    capability_type = _tool_semantic_type(tool_name)
    if capability_type is None:
        return None
    requirement = plan.requirement_for_type(capability_type)
    return requirement.sub_goal_id if requirement else None


def recovery_eligible(result: ExecutionResult) -> bool:
    """Decide whether backend substitution is appropriate for this failure."""
    if result.outcome in {ExecutionOutcome.SUCCESS, ExecutionOutcome.PARTIAL_SUCCESS}:
        return False
    return _failure_class_for_result(result) in _RECOVERY_ELIGIBLE_FAILURES


def _affected_sub_goal_id(
    plan: CapabilityPlan,
    result: ExecutionResult,
    tool_name: str | None,
) -> str | None:
    """Resolve one failure to one plan sub-goal without touching siblings."""
    if result.sub_goal_id and plan.sub_goal(result.sub_goal_id) is not None:
        return result.sub_goal_id
    if result.capability_id:
        matching = next(
            (
                item for item in plan.sub_goals
                if result.capability_id in item.candidate_ids
                or result.capability_id == item.selected_capability_id
            ),
            None,
        )
        if matching is not None:
            return matching.requirement.sub_goal_id
    return _sub_goal_for_tool(plan, tool_name or "")


def _refresh_sub_goal_candidates(
    current: CapabilitySubGoal,
    registry: CapabilityRegistry,
) -> None:
    """Refresh only one sub-goal's compatible registry snapshots."""
    current.candidates = query_capability_candidates(registry, current.requirement)
    matching = [
        descriptor
        for descriptor in registry.query_by_semantic_type(current.requirement.capability_type)
        if _descriptor_matches_requirement(descriptor, current.requirement)
    ]
    current.unknown_candidates = tuple(
        descriptor for descriptor in matching
        if descriptor.health == CapabilityHealth.UNKNOWN
    )
    current.unavailable_candidates = tuple(
        descriptor for descriptor in matching
        if descriptor.health == CapabilityHealth.UNAVAILABLE
    )


def select_next_capability(
    sub_goal: CapabilitySubGoal,
    registry: CapabilityRegistry,
) -> CapabilityDescriptor | None:
    """Select the next healthy, compatible, untried implementation."""
    _refresh_sub_goal_candidates(sub_goal, registry)
    for candidate in sub_goal.candidates:
        if candidate.id not in sub_goal.attempted_capabilities:
            return candidate
    return None


def recover_failed_subgoal(
    plan: CapabilityPlan | None,
    registry: CapabilityRegistry | None,
    result: ExecutionResult,
    *,
    tool_name: str | None = None,
    execute_candidate: Callable[[CapabilityDescriptor], ExecutionResult] | None = None,
) -> tuple[ExecutionResult, str | None, bool]:
    """Boundedly hot-swap one failed sub-goal through a supplied boundary.

    The executor is deliberately supplied by the caller so this helper can
    reuse the existing tool/provider boundary without inventing a second
    execution system. It never updates registry health; execution boundaries
    remain responsible for health policy side effects.
    """
    if plan is None or registry is None:
        return result, None, False
    sub_goal_id = _affected_sub_goal_id(plan, result, tool_name)
    if sub_goal_id is None:
        return result, None, False
    current = plan.sub_goal(sub_goal_id)
    if current is None:
        return result, None, False

    failed_id = result.capability_id or current.selected_capability_id
    current.mark_attempted(failed_id)
    failure_class = _failure_class_for_result(result)
    current.record_recovery_event(
        event="failed",
        capability_id=failed_id,
        failure_class=failure_class.value if failure_class else None,
        outcome=result.outcome.value,
    )
    if not recovery_eligible(result):
        current.record_recovery_event(
            event="recovery_skipped",
            capability_id=failed_id,
            reason="failure_class_not_eligible",
        )
        plan.record_outcome(sub_goal_id, result)
        return result, None, False

    _refresh_sub_goal_candidates(current, registry)
    for descriptor in registry.query_by_semantic_type(current.requirement.capability_type):
        if not _descriptor_matches_requirement(descriptor, current.requirement):
            continue
        if descriptor.id in current.attempted_capabilities:
            reason = "already_attempted"
        elif descriptor.health == CapabilityHealth.UNKNOWN:
            reason = "unknown_health"
        elif descriptor.health == CapabilityHealth.UNAVAILABLE:
            reason = "unavailable"
        else:
            continue
        current.record_recovery_event(
            event="candidate_rejected",
            capability_id=descriptor.id,
            reason=reason,
        )

    last_result = result
    if execute_candidate is None:
        current.record_recovery_event(
            event="recovery_skipped",
            reason="no_executor",
        )
        plan.record_outcome(sub_goal_id, last_result)
        return last_result, sub_goal_id, False

    # The failed implementation is not an alternative. Persisted attempted
    # state therefore also enforces the bound if recovery is invoked again for
    # the same sub-goal instance.
    alternatives_already_tried = max(0, len(current.attempted_capabilities) - 1)
    remaining_attempts = max(
        0,
        MAX_CAPABILITY_RECOVERY_ATTEMPTS - alternatives_already_tried,
    )
    for _ in range(remaining_attempts):
        candidate = select_next_capability(current, registry)
        if candidate is None:
            break
        # Mark before invocation so a faulty callback cannot cause a loop to
        # retry the same implementation.
        current.mark_attempted(candidate.id)
        current.selected_capability_id = candidate.id
        current.record_recovery_event(
            event="candidate_selected",
            capability_id=candidate.id,
            selected=True,
            reason="first_eligible_untried_candidate",
            attempt=len(current.attempted_capabilities) - 1,
        )
        try:
            candidate_result = execute_candidate(candidate)
        except Exception as exc:
            failure_class, diagnostics = classify_exception(exc)
            candidate_result = ExecutionResult(
                outcome=outcome_for_failure(failure_class),
                failure_class=failure_class,
                diagnostics=diagnostics,
            )
        if not isinstance(candidate_result, ExecutionResult):
            candidate_result = ExecutionResult(
                outcome=ExecutionOutcome.FATAL_FAILURE,
                failure_class=CanonicalFailureClass.VALIDATION_FAILED,
            )
        candidate_result = candidate_result.model_copy(update={
            "sub_goal_id": sub_goal_id,
            "capability_id": candidate.id,
        })
        last_result = candidate_result
        candidate_failure = _failure_class_for_result(candidate_result)
        if candidate_result.outcome in {
            ExecutionOutcome.SUCCESS,
            ExecutionOutcome.PARTIAL_SUCCESS,
        }:
            current.record_recovery_event(
                event="recovered",
                capability_id=candidate.id,
                outcome=candidate_result.outcome.value,
                reason="validated_execution_result",
            )
            plan.record_outcome(sub_goal_id, candidate_result)
            return candidate_result, sub_goal_id, True
        current.record_recovery_event(
            event="candidate_rejected",
            capability_id=candidate.id,
            failure_class=candidate_failure.value if candidate_failure else None,
            outcome=candidate_result.outcome.value,
            reason="execution_failed",
        )

    current.record_recovery_event(
        event="recovery_exhausted",
        reason="no_eligible_untried_alternative_or_attempt_bound",
    )
    plan.record_outcome(sub_goal_id, last_result)
    return last_result, sub_goal_id, False


def replan_affected_subgoal(
    plan: CapabilityPlan | None,
    registry: CapabilityRegistry | None,
    result: ExecutionResult,
    *,
    tool_name: str | None = None,
) -> tuple[CapabilityPlan | None, str | None]:
    """Refresh only the failed sub-goal's candidates from current Registry state."""
    if plan is None or registry is None:
        return plan, None
    sub_goal_id = _affected_sub_goal_id(plan, result, tool_name)
    if sub_goal_id is None:
        return plan, None
    current = plan.sub_goal(sub_goal_id)
    if current is None:
        return plan, None
    current.mark_attempted(result.capability_id or current.selected_capability_id)
    _refresh_sub_goal_candidates(current, registry)
    current.selected_capability_id = (
        current.candidates[0].id if current.candidates else None
    )
    plan.record_outcome(sub_goal_id, result)
    return plan, sub_goal_id


def _record_capability_result(
    plan: CapabilityPlan | None,
    result: ExecutionResult,
    *,
    tool_name: str | None = None,
) -> None:
    if plan is None:
        return
    sub_goal_id = _affected_sub_goal_id(plan, result, tool_name)
    if sub_goal_id:
        plan.record_outcome(sub_goal_id, result)


async def _rate_limit_pause() -> None:
    if RATE_LIMIT_PAUSE_SECONDS > 0:
        await asyncio.sleep(RATE_LIMIT_PAUSE_SECONDS)


def _clip(text: str, limit: int = 64) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _response_text(message: dict) -> str:
    content = message.get("content")
    if content is None:
        return "No response generated."
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


def _assistant_message(completion: dict) -> dict:
    choices = completion.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return {"role": "assistant", "content": "No response generated."}
    message = choices[0].get("message")
    return message if isinstance(message, dict) else {"role": "assistant", "content": "No response generated."}


def _conversation_messages(message: str, history: list[dict[str, str]], system_prompt: str) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for item in history:
        messages.append({"role": "user", "content": item["user_message"]})
        messages.append({"role": "assistant", "content": item["assistant_response"]})
    messages.append({"role": "user", "content": message})
    return messages


def _assistant_replay_message(assistant_message: dict, tool_calls: list[dict]) -> dict:
    """Rebuild the assistant turn with OpenAI-wire tool calls for replay."""
    content = assistant_message.get("content")
    if content is not None and not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    wire_calls = []
    for call in tool_calls:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        args = function.get("arguments", {})
        wire_calls.append({
            "id": call.get("id", "") if isinstance(call, dict) else "",
            "type": "function",
            "function": {
                "name": str(function.get("name", "")),
                "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str),
            },
        })
    return {"role": "assistant", "content": content, "tool_calls": wire_calls}


PERSONALITY_SUFFIX: dict[str, str] = {
    "professional": " PERSONA OVERRIDE: Respond in a formal, professional tone.",
    "friendly": " PERSONA OVERRIDE: Respond in a warm, casual, friendly tone.",
    "creative": " PERSONA OVERRIDE: Respond with creative, imaginative phrasing.",
    "developer": " PERSONA OVERRIDE: Respond with a technical, developer-focused tone.",
}

RESPONSE_MODE_SUFFIX: dict[str, str] = {
    "fast": " RESPONSE MODE: Keep responses short and direct; prefer the minimum useful detail.",
    "balanced": "",
    "deep": " RESPONSE MODE: Reason deeply and provide detailed, thorough explanations.",
}


def build_system_prompt(personality: str | None, response_mode: str | None) -> str:
    prompt = SYSTEM_PROMPT
    if personality and personality.lower() in PERSONALITY_SUFFIX:
        prompt += PERSONALITY_SUFFIX[personality.lower()]
    if response_mode and response_mode.lower() in RESPONSE_MODE_SUFFIX:
        prompt += RESPONSE_MODE_SUFFIX[response_mode.lower()]
    return prompt


async def _memory_context(ctx: BrainContext, message: str, limit: int = 5) -> str:
    """Standing orders from persistent memory, injected into every prompt."""
    try:
        engine = getattr(ctx, "memory_engine", None)
        if engine is None:
            return ""
        relevant = await engine.search_relevant(message, limit)
        facts = await search_facts(message, 3)
        if not relevant and not facts:
            return ""
        lines = ["", "[MEMORY — standing orders from past learnings; obey unless Boss overrides them now]"]
        for item in (relevant or [])[:limit]:
            lines.append(
                f"- ({item.get('category', 'personal')}|importance {item.get('importance', 3)}) "
                f"{item.get('title', '')}: {str(item.get('content', ''))[:300]}"
            )
        for fact in (facts or [])[:3]:
            lines.append(f"- (fact) {str(fact.get('fact_value', ''))[:300]}")
        return "\n".join(lines)
    except Exception:
        return ""


@dataclass
class BrainContext:
    """Everything the planner needs, owned by the app lifespan in main.py."""
    log: ActionLog
    state: StateManager
    stats: StatsTracker
    provider_manager: ProviderManager
    memory_engine: MemoryEngine
    device_status: dict = field(default_factory=dict)
    # The application owns registration/bootstrap during its lifespan.  The
    # planner receives this registry as read/write health state only.
    capability_registry: CapabilityRegistry = field(default_factory=CapabilityRegistry)
    # The application creates this once during its lifespan.  The planner
    # receives the established instance and never constructs or initializes it.
    knowledge_engine: KnowledgeEngine | None = None
    # The application lifecycle owns this optional periodic scanner. The
    # planner does not start, stop, or call it.
    knowledge_watcher: KnowledgeIngestionWatcher | None = None
    # The application lifecycle may establish one serialized local reasoning
    # adapter. The planner only consumes it when the capability plan selects
    # its registry candidate.
    local_reasoning: LocalReasoningAdapter | None = None


# ---------------------------------------------------------------------------
# Live stream helpers
#
# A request either owns an AgentStreamer (streamed /ask_ronin) or none at all
# (legacy JSON contract). Every helper below degrades to a no-op in the second
# case, which is what keeps the pre-existing behaviour byte-for-byte identical.
# ---------------------------------------------------------------------------

def _stream() -> AgentStreamer | None:
    return current_stream()


def _thinking(text: str, *, step: int = 0, phase: str = "plan") -> None:
    emit_event(EVENT_THINKING, step=step, phase=phase, text=text)


def _capability_transport_metadata(
    descriptor: CapabilityDescriptor | None,
    producer_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble transport metadata with one authoritative locality source.

    Producer metadata may carry orthogonal facts such as retrieval method, but
    locality is never accepted from it. The selected descriptor overwrites any
    conflicting semantic metadata and is the only source allowed to add
    locality.
    """
    metadata = {
        key: value
        for key, value in (producer_metadata or {}).items()
        if key != "locality" and value is not None
    }
    if descriptor is None:
        return metadata
    metadata["semantic_capability"] = descriptor.capability_type.value
    metadata.pop("locality", None)
    if descriptor.is_local is True:
        metadata["locality"] = "local"
    elif descriptor.is_local is False:
        metadata["locality"] = "remote"
    return metadata


def _tool_call(step: int, tool: str, arguments: dict, *, device: bool,
               label: str | None = None, thought: str | None = None,
               action: dict | None = None, call_id: str | None = None,
               descriptor: CapabilityDescriptor | None = None,
               attempt: int = 0) -> None:
    event_call_id = call_id or f"step-{step}:{tool}"
    emit_event(
        EVENT_TOOL_CALL,
        step=step,
        tool=tool,
        call_id=event_call_id,
        label=label or tool_label(_tool_catalog_id(tool)),
        args=arguments,
        device=device,
        thought=thought or None,
        action=action,
        attempt=attempt,
        **_capability_transport_metadata(descriptor),
    )


def _observation(step: int, tool: str, ok: bool, result: str, ms: int,
                 *, call_id: str | None = None, outcome: str | None = None,
                 metadata: dict[str, Any] | None = None,
                 attempt: int = 0) -> None:
    payload: dict[str, Any] = {
        "step": step,
        "tool": tool,
        "call_id": call_id or f"step-{step}:{tool}",
        "ok": bool(ok),
        "ms": int(ms),
        "result": _clip(str(result), 320),
    }
    if outcome is not None:
        payload["outcome"] = outcome
    payload["attempt"] = attempt
    if metadata:
        payload.update({key: value for key, value in metadata.items() if value is not None})
    emit_event(EVENT_OBSERVATION, **payload)


def _self_correction(step: int, tool: str | None, reason: str, strategy: str,
                     attempt: int = 0, *, call_id: str | None = None,
                     transition_type: str = "retry",
                     semantic_capability: str | None = None,
                     previous_candidate: str | None = None,
                     selected_candidate: str | None = None,
                     outcome: str | None = None,
                     locality: str | None = None) -> None:
    """Emit a typed recovery event without treating strategy prose as schema."""
    payload: dict[str, Any] = {
        "step": step,
        "tool": tool,
        "call_id": call_id,
        "reason": _clip(reason, 240),
        "strategy": strategy,
        "attempt": attempt,
        "transition_type": transition_type,
        "reflexion": _clip(reason, 240),
    }
    for key, value in {
        "semantic_capability": semantic_capability,
        "previous_candidate": previous_candidate,
        "selected_candidate": selected_candidate,
        "outcome": outcome,
        "locality": locality,
    }.items():
        if value is not None:
            payload[key] = value
    emit_event(EVENT_SELF_CORRECTION, **payload)


def _correction_for_failure(result_text: str, tool_name: str) -> str:
    """Turn a raw tool failure into the agent's stated recovery strategy."""
    lowered = str(result_text or "").casefold()
    if "timeout" in lowered:
        return "retry with a shorter timeout, then degrade gracefully"
    if "not found" in lowered or "no such" in lowered or "error" in lowered:
        return f"change strategy instead of repeating {tool_name} (re-inspect, different args, or another tool)"
    return "re-read the state and try a different approach"


# ---------------------------------------------------------------------------
# Sanitized failure feedback helpers
#
# These convert ExecutionResult failures into clean, structured observations
# for the ReAct loop.  Raw stack traces, sensitive diagnostics, and internal
# telemetry are NEVER exposed to the LLM context or user-facing UI.
# ---------------------------------------------------------------------------

#: Canonical failure class → human-readable reason (safe for ReAct context).
_FAILURE_REASON: dict[CanonicalFailureClass, str] = {
    CanonicalFailureClass.TRANSIENT: "service temporarily unavailable",
    CanonicalFailureClass.NETWORK_ISOLATED: "unable to reach the service",
    CanonicalFailureClass.AUTH_DENIED: "authentication failed",
    CanonicalFailureClass.POLICY_BLOCKED: "request blocked by policy",
    CanonicalFailureClass.VALIDATION_FAILED: "invalid request data",
    CanonicalFailureClass.DETERMINISTIC_ERROR: "requested file was not found or command not found",
    CanonicalFailureClass.UNSUPPORTED_OPERATION: "operation not supported",
    CanonicalFailureClass.UNKNOWN_FATAL: "unexpected error",
}


def _failure_class_reason(failure_class: CanonicalFailureClass | None) -> str:
    """Map a failure class to a sanitized, human-readable reason string."""
    if failure_class is None:
        return "unknown error"
    return _FAILURE_REASON.get(failure_class, "unknown error")


def _failure_class_label(failure_class: CanonicalFailureClass | None) -> str:
    """Canonical label for logging (e.g. 'transient', 'auth_denied')."""
    return failure_class.value if failure_class is not None else "unknown"


def _tool_failure_observation(
    tool_name: str,
    failure_class: CanonicalFailureClass | None,
) -> str:
    """Create a sanitized operational observation for a tool failure.

    The observation deliberately contains only the canonical class and a
    stable, human-readable reason.  In particular, it never copies
    ``ExecutionResult.data`` or ``diagnostics.raw_message`` because either may
    contain an exception message, a filesystem path, or provider telemetry.
    """
    label = failure_class.name if failure_class is not None else "UNKNOWN_FATAL"
    reason = _failure_class_reason(failure_class)
    return f"Tool {tool_name} failed. Failure class: {label}. Reason: {reason}."


def _llm_failure_observation(
    failure_class: CanonicalFailureClass | None,
    *,
    context: str = "Reasoning",
) -> str:
    """Create a sanitized observation for an LLM reasoning failure."""
    label = failure_class.name if failure_class is not None else "UNKNOWN_FATAL"
    reason = _failure_class_reason(failure_class)
    return f"{context} failed. Failure class: {label}. Reason: {reason}."


def _correction_for_failure_class(
    failure_class: CanonicalFailureClass | None,
    tool_name: str,
) -> str:
    """Map a canonical failure class to the agent's stated recovery strategy.

    Replaces string-matching heuristics with deterministic class-based routing.
    """
    if failure_class == CanonicalFailureClass.TRANSIENT:
        return "retry with a shorter timeout, then degrade gracefully"
    if failure_class == CanonicalFailureClass.DETERMINISTIC_ERROR:
        return f"change strategy instead of repeating {tool_name} (re-inspect, different args, or another tool)"
    if failure_class == CanonicalFailureClass.UNSUPPORTED_OPERATION:
        return f"try a different tool or approach instead of {tool_name}"
    if failure_class == CanonicalFailureClass.AUTH_DENIED:
        return "check credentials or try a different provider"
    if failure_class == CanonicalFailureClass.NETWORK_ISOLATED:
        return "check network connectivity or try an offline approach"
    if failure_class == CanonicalFailureClass.VALIDATION_FAILED:
        return f"check the arguments passed to {tool_name} and retry with valid input"
    if failure_class == CanonicalFailureClass.POLICY_BLOCKED:
        return "this action is not permitted; try an alternative approach"
    return "re-read the state and try a different approach"


def _execute_llm_request(message: str, history: list, providers: list,
                         **kwargs: Any) -> ExecutionResult:
    """Run one selected reasoning candidate through the LLM boundary.

    Local reasoning is selected by the normal capability plan and passed as a
    callable to the existing boundary. It is not a remote-failure branch or a
    planner-owned fallback hierarchy. The compatibility seams for tests and
    older callers remain unchanged when no local candidate is selected.
    """
    local_adapter = kwargs.pop("local_adapter", None)
    selected_capability_id = kwargs.pop("selected_capability_id", None)

    def _tag(result: ExecutionResult) -> ExecutionResult:
        # Successful remote calls report actual provider success through the
        # existing callback bridge; only failed selected candidates need a
        # candidate identity for bounded semantic recovery. Local calls always
        # carry their identity so local health is updated as well.
        if selected_capability_id and (
            selected_capability_id == LOCAL_REASONING_CAPABILITY_ID
            or result.outcome != ExecutionOutcome.SUCCESS
        ):
            return result.model_copy(update={"capability_id": selected_capability_id})
        return result

    if (
        selected_capability_id == LOCAL_REASONING_CAPABILITY_ID
        and isinstance(local_adapter, LocalReasoningAdapter)
    ):
        result = execute_llm_boundary(
            message,
            history,
            providers,
            _completion_callable=local_adapter.complete,
            **kwargs,
        )
        return _tag(result)
    if execute_llm_boundary is not _ORIGINAL_EXECUTE_LLM_BOUNDARY:
        return _tag(execute_llm_boundary(message, history, providers, **kwargs))
    if complete is not _ORIGINAL_COMPLETE:
        return _tag(execute_boundary(complete, message, history, providers, **kwargs))
    return _tag(execute_llm_boundary(message, history, providers, **kwargs))


def _selected_reasoning_candidate(plan: CapabilityPlan | None) -> str | None:
    """Return the Registry-selected reasoning implementation, if any."""
    if plan is None:
        return None
    sub_goal = plan.sub_goal("subgoal-reasoning")
    return sub_goal.selected_capability_id if sub_goal is not None else None


def _selected_candidate_descriptor(
    plan: CapabilityPlan | None,
    registry: CapabilityRegistry | None,
    tool_name: str,
) -> CapabilityDescriptor | None:
    """Resolve the plan-selected implementation; locality comes from it."""
    if plan is None or registry is None:
        return None
    capability = _tool_semantic_type(tool_name)
    if capability is None:
        return None
    for sub_goal in plan.sub_goals:
        if sub_goal.requirement.capability_type != capability:
            continue
        selected = sub_goal.selected_capability_id
        return registry.get(selected) if selected else None
    return None


def _descriptor_for_tool(
    ctx: BrainContext,
    tool_name: str,
    plan: CapabilityPlan | None = None,
) -> CapabilityDescriptor | None:
    """Find an existing registered binding for direct transport-only routes."""
    registry = _get_registry(ctx)
    selected = _selected_candidate_descriptor(plan, registry, tool_name)
    if selected is not None:
        return selected
    capability = _tool_semantic_type(tool_name)
    if registry is None or capability is None:
        return None
    candidates = registry.query_by_semantic_type(capability)
    for candidate in candidates:
        metadata = candidate.metadata or {}
        if metadata.get("function_name") == tool_name or metadata.get("tool_name") == tool_name:
            return candidate
        if candidate.id in {f"brain-{tool_name}", f"tool-{tool_name}"}:
            return candidate
    return candidates[0] if len(candidates) == 1 else None


def _candidate_locality(ctx: BrainContext, capability_id: str | None) -> str | None:
    registry = _get_registry(ctx)
    descriptor = registry.get(capability_id) if registry is not None and capability_id else None
    if descriptor is None:
        return None
    return _capability_transport_metadata(descriptor).get("locality")


def _execute_reasoning_candidate(
    candidate: CapabilityDescriptor,
    *,
    message: str,
    history: list,
    provider_payload: list[dict],
    ctx: BrainContext,
    kwargs: dict[str, Any],
) -> ExecutionResult:
    """Execute one semantic reasoning candidate through the same LLM boundary."""
    if candidate.id == LOCAL_REASONING_CAPABILITY_ID:
        return _execute_llm_request(
            message,
            history,
            [],
            local_adapter=getattr(ctx, "local_reasoning", None),
            selected_capability_id=candidate.id,
            **kwargs,
        )
    candidate_payload = [
        provider for provider in provider_payload
        if f"provider-{str(provider.get('provider') or '').strip().casefold()}" == candidate.id
    ]
    if not candidate_payload:
        return ExecutionResult(
            outcome=ExecutionOutcome.UNSUPPORTED,
            capability_id=candidate.id,
            failure_class=CanonicalFailureClass.UNSUPPORTED_OPERATION,
        )
    return _execute_llm_request(
        message,
        history,
        candidate_payload,
        selected_capability_id=candidate.id,
        **kwargs,
    )


def _recover_reasoning_failure(
    ctx: BrainContext,
    plan: CapabilityPlan | None,
    result: ExecutionResult,
    *,
    message: str,
    history: list,
    provider_payload: list[dict],
    kwargs: dict[str, Any],
) -> tuple[ExecutionResult, CapabilityPlan | None, bool]:
    """Use Phase 11 bounded candidate recovery for any reasoning backend."""
    registry = _get_registry(ctx)
    if registry is None or result.outcome == ExecutionOutcome.SUCCESS:
        return result, plan, False
    recovered, _, did_recover = recover_failed_subgoal(
        plan,
        registry,
        result,
        execute_candidate=lambda candidate: _execute_reasoning_candidate(
            candidate,
            message=message,
            history=history,
            provider_payload=provider_payload,
            ctx=ctx,
            kwargs=kwargs,
        ),
    )
    return recovered, plan, did_recover


def _execute_tool_request(
    tool_name: str,
    arguments: dict[str, Any],
    ctx: BrainContext | None = None,
) -> ExecutionResult:
    """Run a Brain tool through the existing normalized execution boundary.

    ``search_local_knowledge`` is the one context-bound server tool: its
    callable closes over the lifecycle-owned engine from ``BrainContext``.
    The boundary still owns exception capture, canonical classification, and
    diagnostic retention.  No global engine or planner-created engine exists.
    """
    if tool_name == KNOWLEDGE_TOOL_NAME and execute_tool_boundary is not _ORIGINAL_EXECUTE_TOOL_BOUNDARY:
        # Preserve the established planner test/injection seam.
        result = execute_tool_boundary(tool_name, arguments)
    elif tool_name == KNOWLEDGE_TOOL_NAME:
        result = execute_tool_boundary(tool_name, arguments, context=ctx)
    elif execute_tool_boundary is not _ORIGINAL_EXECUTE_TOOL_BOUNDARY:
        result = execute_tool_boundary(tool_name, arguments)
    elif execute_tool is not _ORIGINAL_EXECUTE_TOOL:
        result = execute_boundary(execute_tool, tool_name, arguments)
    else:
        result = execute_tool_boundary(tool_name, arguments)
    return _normalize_tool_result(result)


def _normalize_tool_result(result: ExecutionResult) -> ExecutionResult:
    """Defensively classify JSON error payloads from compatibility transports."""
    if result.outcome != ExecutionOutcome.SUCCESS or not isinstance(result.data, str):
        return result
    try:
        payload = json.loads(result.data)
    except (json.JSONDecodeError, TypeError):
        return result
    if not isinstance(payload, dict) or "error" not in payload:
        return result

    failure_class, diagnostics = classify_exception(
        RuntimeError(str(payload.get("error") or "tool execution failed")),
        extra_details={"source": "tool_registry"},
    )
    return ExecutionResult(
        outcome=outcome_for_failure(failure_class),
        data=result.data,
        partial_data=result.partial_data,
        failure_class=failure_class,
        diagnostics=diagnostics,
        metadata=result.metadata,
        elapsed_ms=result.elapsed_ms,
        sub_goal_id=result.sub_goal_id,
        capability_id=result.capability_id,
    )


def _capability_tool_name(
    candidate: CapabilityDescriptor,
    fallback: str,
) -> str | None:
    """Resolve a registry descriptor to an existing Brain tool binding."""
    metadata = candidate.metadata or {}
    raw_name = (
        metadata.get("function_name")
        or metadata.get("tool_name")
        or metadata.get("function")
    )
    if not raw_name and candidate.id.startswith("brain-"):
        raw_name = candidate.id.removeprefix("brain-")
    if not raw_name and candidate.id.startswith("tool-"):
        raw_name = candidate.id.removeprefix("tool-")
    raw_name = str(raw_name or "").strip()
    if raw_name == "web_search":
        return "search"
    if raw_name in _TOOL_CAPABILITY_TYPES:
        return raw_name
    # A same-capability implementation may be represented by the original
    # tool binding when a test/application supplies that binding explicitly.
    if candidate.id == fallback:
        return fallback
    return None


def _execute_registered_candidate(
    candidate: CapabilityDescriptor,
    *,
    original_tool_name: str,
    arguments: dict[str, Any],
    ctx: BrainContext,
) -> ExecutionResult:
    """Execute one alternative using the existing tool boundary."""
    tool_name = _capability_tool_name(candidate, original_tool_name)
    if tool_name is None:
        return ExecutionResult(
            outcome=ExecutionOutcome.UNSUPPORTED,
            capability_id=candidate.id,
            failure_class=CanonicalFailureClass.UNSUPPORTED_OPERATION,
        )
    return _execute_tool_request(tool_name, arguments, ctx)


def _failure_class_for_result(result: ExecutionResult) -> CanonicalFailureClass | None:
    """Recover a safe canonical class when a producer supplied outcome only."""
    if result.failure_class is not None:
        return result.failure_class
    return {
        ExecutionOutcome.RETRYABLE_FAILURE: CanonicalFailureClass.TRANSIENT,
        ExecutionOutcome.BLOCKED: CanonicalFailureClass.POLICY_BLOCKED,
        ExecutionOutcome.DENIED: CanonicalFailureClass.AUTH_DENIED,
        ExecutionOutcome.UNSUPPORTED: CanonicalFailureClass.UNSUPPORTED_OPERATION,
        ExecutionOutcome.FATAL_FAILURE: CanonicalFailureClass.UNKNOWN_FATAL,
    }.get(result.outcome)


def _tool_execution_observation(tool_name: str, result: ExecutionResult) -> tuple[str, bool]:
    """Interpret one tool ``ExecutionResult`` for the ReAct context and UI."""
    if result.outcome == ExecutionOutcome.SUCCESS:
        return _result_text(result.data), True
    if result.outcome == ExecutionOutcome.PARTIAL_SUCCESS:
        partial = result.partial_data if result.partial_data is not None else result.data
        return (
            f"Tool {tool_name} partially succeeded. Verified result: {_result_text(partial)}",
            True,
        )
    # All failure outcomes intentionally discard result.data and diagnostics.
    return _tool_failure_observation(tool_name, _failure_class_for_result(result)), False


def _result_text(value: Any) -> str:
    """Render successful/partial data without exposing Python object reprs."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "(no usable result)"


def _device_failure_observation(tool_name: str, *, timed_out: bool = False) -> str:
    """Sanitize a Body callback failure before it reaches ReAct or the UI."""
    if timed_out:
        return (
            f"Tool {tool_name} failed. Failure class: TRANSIENT. "
            "Reason: Body callback TIMEOUT waiting for an observation."
        )
    return _tool_failure_observation(tool_name, CanonicalFailureClass.UNKNOWN_FATAL)


def _llm_completion(result: ExecutionResult) -> dict | None:
    """Extract usable completion data while preserving partial-success semantics."""
    if result.outcome not in {ExecutionOutcome.SUCCESS, ExecutionOutcome.PARTIAL_SUCCESS}:
        return None
    data = (
        result.partial_data if result.outcome == ExecutionOutcome.PARTIAL_SUCCESS
        and result.partial_data is not None else result.data
    )
    return data if isinstance(data, dict) else None


def _llm_failure_response(result: ExecutionResult, *, steps: int = 0) -> AskResponse:
    """Return a bounded, sanitized response for a failed reasoning boundary."""
    failure_class = _failure_class_for_result(result)
    observation = _llm_failure_observation(failure_class)
    _self_correction(
        steps,
        None,
        observation,
        "stop this reasoning step and use only an explicitly available fallback",
        attempt=steps,
        transition_type="retry",
        semantic_capability=SemanticCapabilityType.REASONING.value,
    )
    return AskResponse(
        response=f"{observation} The reasoning service is temporarily unavailable.",
        route="agent_final" if steps else "llm",
        error="llm_unavailable",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Pending device-action sessions (Body executes, then calls back)
# ---------------------------------------------------------------------------

_AGENT_SESSIONS: dict[str, dict[str, Any]] = {}
_AGENT_LOCK = threading.Lock()


def _save_agent_session(session_id: str, payload: dict[str, Any]) -> None:
    with _AGENT_LOCK:
        if len(_AGENT_SESSIONS) >= 100:
            _AGENT_SESSIONS.pop(next(iter(_AGENT_SESSIONS)), None)
        _AGENT_SESSIONS[session_id] = payload


def _load_agent_session(session_id: str) -> dict[str, Any] | None:
    with _AGENT_LOCK:
        session = _AGENT_SESSIONS.get(session_id)
        return dict(session) if session is not None else None


def _clear_agent_session(session_id: str) -> None:
    with _AGENT_LOCK:
        _AGENT_SESSIONS.pop(session_id, None)


# ---------------------------------------------------------------------------
# Settings helpers (moved from main.py so the planner is self-contained)
# ---------------------------------------------------------------------------

def _runtime_settings(settings_path: Path) -> dict:
    defaults = {"tool_execution_mode": "full", "developer_mode": True, "allow_dynamic_tools": True}
    try:
        values = json.loads(settings_path.read_text(encoding="utf-8"))
        return {**defaults, **values} if isinstance(values, dict) else defaults
    except (OSError, json.JSONDecodeError):
        return defaults


def _tool_execution_enabled(settings: dict) -> bool:
    mode = str(settings.get("tool_execution_mode", "full")).strip().lower()
    return (bool(settings.get("developer_mode", True))
            and bool(settings.get("allow_dynamic_tools", True))
            and mode not in {"disabled", "off", "none"})


def _filter_tools(tools: list[dict], tools_enabled: dict[str, bool]) -> list[dict]:
    """Map UI tool toggles onto registry tool names."""
    if not tools_enabled:
        return tools
    return [tool for tool in tools if tools_enabled.get(
        _tool_catalog_id(tool.get("function", {}).get("name", "")), True
    )]


def _tool_catalog_id(function_name: str) -> str:
    return {
        # Brain tools
        "search": "web_search",
        "save_memory": "agent_memory",
        "retrieve_memory": "agent_memory",
        "run_termux_command": "terminal",
        "read_file": "file_manager",
        "write_file": "file_manager",
        "list_files": "file_manager",
        # Device tools
        "open_app": "app_control",
        "list_apps": "app_control",
        "click": "app_control",
        "set_text": "app_control",
        "scroll": "app_control",
        "press_back": "app_control",
        "press_home": "app_control",
        "read_screen": "screen_reader",
        "click_xy": "screen_reader",
        "click_node": "screen_reader",
        "get_notifications": "notification_manager",
        # Legacy
        "command_for_request": "app_control",
    }.get(function_name, function_name)


# ---------------------------------------------------------------------------
# Capability-aware LLM routing helpers
#
# These bridge the existing `complete()` transport with the new
# CapabilityRegistry so that provider health is tracked across requests
# and the orchestrator can route around degraded capabilities.
# ---------------------------------------------------------------------------

#: Meta-capability ID used to track the overall LLM reasoning call
#: (as opposed to individual provider capabilities).
_REASONING_META_CAP_ID = "reasoning-request"


def _get_registry(ctx: BrainContext) -> CapabilityRegistry | None:
    """Get the capability registry from context, or None if unavailable."""
    return getattr(ctx, "capability_registry", None)


def _knowledge_tool_is_selectable(ctx: BrainContext) -> bool:
    """Return whether lifecycle health permits the read-only local tool."""
    engine = getattr(ctx, "knowledge_engine", None)
    registry = _get_registry(ctx)
    descriptor = registry.get(KNOWLEDGE_CAPABILITY_ID) if registry is not None else None
    return bool(
        engine is not None
        and descriptor is not None
        and descriptor.health in {
            CapabilityHealth.AVAILABLE,
            CapabilityHealth.DEGRADED,
        }
    )


def _sort_providers_by_health(
    ctx: BrainContext, provider_payload: list[dict],
) -> list[dict]:
    """Present providers by current Registry health without health mutation.

    AVAILABLE precedes DEGRADED.  UNKNOWN is ordered after both because it is
    not evidence of health; it remains a distinguishable transport input for
    the compatibility validation path.  UNAVAILABLE is last and is removed
    once a validated reasoning candidate exists.
    """
    reg = _get_registry(ctx)
    if reg is None:
        return list(provider_payload)

    _PRIORITY = {
        CapabilityHealth.AVAILABLE: 0,
        CapabilityHealth.DEGRADED: 1,
        CapabilityHealth.UNKNOWN: 2,
        CapabilityHealth.UNAVAILABLE: 3,
    }

    def _key(provider: dict) -> int:
        name = str(provider.get("provider") or "").lower().strip()
        desc = reg.get(f"provider-{name}")
        if desc is None:
            return 1  # Unknown provider — same priority as UNKNOWN
        return _PRIORITY.get(desc.health, 1)

    return sorted(provider_payload, key=_key)


def _invoke_complete(
    reg: CapabilityRegistry | None,
    cap_id: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call ``complete()`` through the pre-existing capability health bridge.

    This legacy helper is retained for callers that still import it.  Active
    planner paths use :func:`_execute_llm_request` and interpret
    ``ExecutionResult`` directly; the registry-aware helper remains separate
    so boundary signatures stay registry-agnostic.
    """
    if reg is None:
        return complete(*args, **kwargs)

    exec_result = _execute_capability_fn(reg, cap_id, complete, *args, **kwargs)
    if exec_result.outcome == ExecutionOutcome.SUCCESS:
        return exec_result.data

    # Health was already updated by the existing executor bridge.  Preserve
    # the legacy exception contract for callers outside the active loop.
    failure_class = _failure_class_for_result(exec_result)
    raise LLMError(_llm_failure_observation(failure_class))


def _record_provider_failures_from_llm_error(
    ctx: BrainContext, exc: LLMError,
) -> None:
    """Parse ``LLMError`` and update per-provider health in the registry.

    ``complete()`` formats its error as:
    ``"All configured providers failed: name1: msg1; name2: msg2"``.
    We parse this to classify each provider's failure independently so
    that e.g. an AUTH_DENIED failure for one provider doesn't mark a
    TRANSIENT timeout for another.
    """
    reg = _get_registry(ctx)
    if reg is None:
        return

    msg = str(exc)
    prefix = "All configured providers failed: "
    parsed_any = False

    if msg.startswith(prefix):
        rest = msg[len(prefix):]
        for part in rest.split("; "):
            if ": " not in part:
                continue
            name, provider_msg = part.split(": ", 1)
            name = name.strip().lower()
            cap_id = f"provider-{name}"
            if reg.contains(cap_id):
                failure_class, _ = classify_exception(RuntimeError(provider_msg.strip()))
                try:
                    reg.update_health(cap_id, success=False, failure_class=failure_class)
                except Exception:
                    pass
                parsed_any = True

    if not parsed_any:
        # Couldn't parse per-provider details — classify the overall error
        # and apply it to all REASONING provider capabilities.
        failure_class, _ = classify_exception(exc)
        for desc in reg.query_by_semantic_type(SemanticCapabilityType.REASONING):
            if desc.id.startswith("provider-"):
                try:
                    reg.update_health(desc.id, success=False, failure_class=failure_class)
                except Exception:
                    pass


def _record_reasoning_result(ctx: BrainContext, result: ExecutionResult) -> None:
    """Apply one selected reasoning outcome to its Registry candidate."""
    capability_id = result.capability_id
    registry = _get_registry(ctx)
    if not capability_id or registry is None or not registry.contains(capability_id):
        return
    try:
        if result.outcome == ExecutionOutcome.SUCCESS:
            registry.update_health(capability_id, success=True)
        elif result.failure_class is not None:
            registry.update_health(
                capability_id,
                success=False,
                failure_class=result.failure_class,
            )
    except Exception:
        # A concurrent lifecycle teardown must not change the planner result.
        pass


def _record_provider_success(
    ctx: BrainContext, provider_calls: dict[str, bool],
) -> None:
    """Record success for providers that responded successfully."""
    reg = _get_registry(ctx)
    if reg is None:
        return
    for name, ok in provider_calls.items():
        if ok:
            cap_id = f"provider-{name}"
            if reg.contains(cap_id):
                try:
                    reg.update_health(cap_id, success=True)
                except Exception:
                    pass


def _count_healthy_reasoners(ctx: BrainContext) -> int:
    """Count reasoning capabilities still potentially usable.

    Returns ``-1`` when no registry is available (unknown).
    """
    reg = _get_registry(ctx)
    if reg is None:
        return -1
    reasoners = reg.query_by_semantic_type(SemanticCapabilityType.REASONING)
    return sum(
        1 for d in reasoners
        if d.health in (
            CapabilityHealth.AVAILABLE,
            CapabilityHealth.DEGRADED,
        )
    )


# ---------------------------------------------------------------------------
# Route executors (offline fallbacks + developer agent)
# ---------------------------------------------------------------------------

async def _run_android_command(request: AskRequest, ctx: BrainContext) -> AskResponse:
    # Device-side execution is enforced by the body (Accessibility service);
    # the brain only checks whether the user disabled the tool in the UI.
    if _tool_disabled(request, "app_control"):
        return AskResponse(
            response="App Control is disabled in the Tools module. Enable it to run device commands.",
            route="android_command",
        )
    command = command_for_request(request.message)
    # Keep the offline response on the JSON/Pydantic boundary.  Depending on
    # how the legacy tool module was loaded, its AndroidCommand instance may
    # not be recognized as the exact schema class expected by AskResponse.
    command_payload = command.model_dump(mode="json", exclude_none=True)
    ctx.state.set("executing", f"Executing: {command.action}...")
    ctx.log.log(f"Executing device command: {command.action}", "tool")
    # The offline/legacy path hands the command to the Body in the response, so
    # there is no observation to wait for — still surface the intent in the log.
    call_id = f"step-0:{command.action}"
    descriptor = _descriptor_for_tool(ctx, command.action)
    _tool_call(0, command.action, command_payload, device=True,
               label=f"Device command · {command.action}", call_id=call_id,
               descriptor=descriptor)
    _observation(0, command.action, True, "Queued on the Body for execution.", 0,
                 call_id=call_id, outcome=ExecutionOutcome.SUCCESS.value,
                 metadata=_capability_transport_metadata(descriptor))
    await ctx.stats.bump("apps_opened")
    await ctx.stats.record_tool_usage("app_control", command.action, True)
    return AskResponse(
        response=f"Approved Android command prepared: {command.action}.",
        route="android_command",
        command=command_payload,
    )


async def _run_web_search(request: AskRequest, ctx: BrainContext) -> AskResponse:
    if _tool_disabled(request, "web_search"):
        return AskResponse(
            response="Web Search is disabled in the Tools module. Enable it to search the web.",
            route="web_search",
        )
    query = request.message.split(" ", 2)[-1]
    ctx.state.set("executing", "Searching web...")
    ctx.log.log(f"Searching web for: \"{_clip(query, 48)}\"", "tool")
    call_id = "step-0:search"
    descriptor = _descriptor_for_tool(ctx, "search")
    _tool_call(0, "search", {"query": query}, device=False, label="Web Search",
               call_id=call_id, descriptor=descriptor)
    started = time.monotonic()
    exec_result = await asyncio.to_thread(execute_search_boundary, query)
    latency_ms = exec_result.elapsed_ms or int((time.monotonic() - started) * 1000)
    if exec_result.outcome not in {ExecutionOutcome.SUCCESS, ExecutionOutcome.PARTIAL_SUCCESS}:
        failure_label = _failure_class_label(exec_result.failure_class)
        sanitized = _tool_failure_observation("search", exec_result.failure_class)
        await ctx.stats.record_tool_usage("web_search", query, False)
        ctx.log.log(f"Web search failed: {failure_label}", "error")
        _observation(0, "search", False, sanitized, latency_ms,
                     call_id=call_id, outcome=exec_result.outcome.value,
                     metadata=_capability_transport_metadata(descriptor))
        _self_correction(
            0,
            "search",
            sanitized,
            _correction_for_failure_class(_failure_class_for_result(exec_result), "search"),
            call_id=call_id,
            semantic_capability=_capability_transport_metadata(descriptor).get(
                "semantic_capability"
            ),
            locality=_capability_transport_metadata(descriptor).get("locality"),
        )
        return AskResponse(response=sanitized, route="web_search", error=failure_label)
    data = (
        exec_result.partial_data
        if exec_result.outcome == ExecutionOutcome.PARTIAL_SUCCESS
        and exec_result.partial_data is not None else exec_result.data
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    if not isinstance(data, dict):
        sanitized = "Web search failed. Failure class: VALIDATION_FAILED. Reason: invalid request data."
        await ctx.stats.record_tool_usage("web_search", query, False)
        _observation(0, "search", False, sanitized, latency_ms,
                     call_id=call_id, outcome=ExecutionOutcome.FATAL_FAILURE.value,
                     metadata=_capability_transport_metadata(descriptor))
        return AskResponse(response=sanitized, route="web_search", error="validation_failed")
    results = data.get("results") or []
    ctx.log.log(f"Parsing {len(results)} results...", "info")
    _observation(0, "search", len(results) > 0, f"{len(results)} results parsed", latency_ms,
                 call_id=call_id, outcome=exec_result.outcome.value,
                 metadata=_capability_transport_metadata(descriptor))
    await ctx.stats.bump("web_searches")
    await ctx.stats.record_tool_usage("web_search", query, len(results) > 0)
    response = "\n".join(f"{x['title']}: {x['snippet']}" for x in results) or "No web results were returned."
    return AskResponse(response=response, route="web_search")


async def _run_local_tool(request: AskRequest, ctx: BrainContext) -> AskResponse:
    fact = request.message.split(" ", 1)[-1]
    ctx.state.set("learning", "Saving memory...")
    ctx.log.log("Saving memory...", "info")
    call_id = "step-0:save_memory"
    descriptor = _descriptor_for_tool(ctx, "save_memory")
    _tool_call(0, "save_memory", {"content": _clip(fact, 120)}, device=False,
               label="Agent Memory", call_id=call_id, descriptor=descriptor)
    await store_fact(fact[:120].lower(), fact)
    title = _clip(fact, 48)
    await ctx.memory_engine.add(category="knowledge", title=title, content=fact,
                                importance=3, source="chat")
    await ctx.stats.bump("learned")
    await ctx.stats.record_tool_usage("note_creator", title, True)
    ctx.log.log(f"Memory saved: \"{title}\"", "success")
    _observation(0, "save_memory", True, f"Stored as \"{title}\"", 0,
                 call_id=call_id, outcome=ExecutionOutcome.SUCCESS.value,
                 metadata=_capability_transport_metadata(descriptor))
    return AskResponse(response="Stored in persistent memory.", route="local_tool")


async def _run_tool_creation(request: AskRequest, ctx: BrainContext) -> AskResponse:
    """The Developer Agent: generate a new Python tool and propose it for approval."""
    tool_creation_system_prompt = (
        "You generate a single Python tool file for RONIN. "
        "Return only complete, valid raw Python source code for exactly one file. "
        "Do not use Markdown or code fences. Do not include explanations, tutorials, or prose. "
        "CRITICAL RULE: Write simple standalone Python functions (def). DO NOT create classes, BaseModels, or use fake AI tool frameworks. "
        "DO NOT import non-existent modules like 'rpn_tools'. Use only standard Python libraries and 'requests'. "
        "The entire response must compile as the requested Python tool."
    )
    ctx.state.set("thinking", "Generating code...")
    ctx.log.log("Generating new tool code...", "info")
    _thinking("Developer agent: writing a new Python tool", phase="call")
    provider_payload = [provider.model_dump() for provider in request.providers]
    started = time.monotonic()
    stream = _stream()
    exec_result = await asyncio.to_thread(
        _execute_llm_request, request.message, [], provider_payload,
        system_prompt=tool_creation_system_prompt,
        on_delta=stream.delta_forwarder(0) if stream is not None else None,
    )
    elapsed_ms = exec_result.elapsed_ms or int((time.monotonic() - started) * 1000)
    if stream is not None:
        stream.flush_delta(0)
    completion = _llm_completion(exec_result)
    if completion is None:
        failure_label = _failure_class_label(exec_result.failure_class)
        ctx.log.log(f"Code generation failed: {failure_label}", "error")
        return AskResponse(
            response=f"Code generation failed ({_failure_class_reason(exec_result.failure_class)}). Please try again.",
            route="tool_creation",
            error=failure_label,
        )
    _record_provider_latency(ctx, completion, elapsed_ms, provider_payload)
    generated_code = _response_text(_assistant_message(completion))
    cleaned_code = generated_code.replace("```python", "").replace("```", "").strip()

    prop_id = str(uuid.uuid4())[:8]
    module_name = f"tools.dynamic_{prop_id}"
    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    file_path = str(tools_dir / f"dynamic_{prop_id}.py")
    proposal = UpdateProposal(
        proposal_id=prop_id,
        file_path=file_path,
        module_name=module_name,
        new_code=cleaned_code,
        summary=f"Generated a new tool for: {request.message}",
    )
    ctx.log.log("Tool code ready — awaiting approval", "success")
    response_text = "Boss, maine is tool ka Python code likh liya hai. Please screen par review aur approve kijiye."
    return AskResponse(response=response_text, route="tool_creation", update_proposal=proposal)


# ---------------------------------------------------------------------------
# ReAct agent loop
# ---------------------------------------------------------------------------

async def _run_llm(request: AskRequest, ctx: BrainContext) -> AskResponse:
    """Contain normalized reasoning failures without leaking diagnostics.

    Active reasoning calls return ``ExecutionResult`` objects from the LLM
    boundary.  The ``LLMError`` handler remains only for legacy callers that
    bypass that boundary; it preserves the existing response contract while
    keeping raw provider messages out of ReAct and UI surfaces.
    """
    try:
        return await _run_llm_unchecked(request, ctx)
    except LLMError as exc:
        # Classify per-provider failures and update capability health.
        _record_provider_failures_from_llm_error(ctx, exc)
        remaining = _count_healthy_reasoners(ctx)
        remaining_ctx = (
            f" ({remaining} reasoning capability/ies still healthy)" if remaining >= 0 else ""
        )
        ctx.log.log(f"All configured AI providers failed{remaining_ctx}", "error")
        ctx.state.set("idle", "AI unavailable. Please retry.")
        _self_correction(0, None, "all configured AI providers failed",
                          "offline legacy route (keyword commands without a key)")
        return AskResponse(
            response="The AI service is temporarily unavailable. Please check your provider settings and try again.",
            route="llm",
            error="llm_unavailable",
        )


async def _run_llm_unchecked(request: AskRequest, ctx: BrainContext) -> AskResponse:
    log, stats, pm, state = ctx.log, ctx.stats, ctx.provider_manager, ctx.state
    stream = _stream()
    history = await recent_history(request.session_id)
    if stream is not None and history:
        stream.emit(EVENT_THINKING, step=0, phase="recall",
                    text=f"Recalling {len(history)} previous turn(s) for this session")
    system_prompt = build_system_prompt(request.personality, request.response_mode)
    memory_block = await _memory_context(ctx, request.message)
    if memory_block:
        injected = max(0, len([line for line in memory_block.splitlines() if line.startswith("- ")]))
        log.log(f"Injecting {injected} standing order(s) from memory", "info")
        if stream is not None:
            stream.emit(EVENT_THINKING, step=0, phase="recall",
                        text=f"{injected} standing order(s) recalled from long-term memory")
    system_prompt += memory_block
    provider_payload = [provider.model_dump() for provider in request.providers]

    # --- Capability-aware provider discovery ---
    # Registration is owned by the application lifecycle.  A request only
    # reads the active registry and applies its current health state.
    reg = _get_registry(ctx)
    provider_payload = _sort_providers_by_health(ctx, provider_payload)

    settings = _runtime_settings(Path(__file__).resolve().parent.parent / "config" / "settings.json")

    if _tool_execution_enabled(settings):
        server_tools = [
            tool for tool in get_available_tools()
            if tool.get("function", {}).get("name") not in HIDDEN_LEGACY_TOOLS
        ]
        server_tools = _filter_tools(server_tools, _enabled_map(request))
        if _knowledge_tool_is_selectable(ctx):
            server_tools.append(knowledge_tool_schema())
        device_tools = _filter_tools(device_tool_schemas(), _enabled_map(request))
        available_tools = server_tools + device_tools
    else:
        available_tools = []

    # Build a semantic plan from the lifecycle-owned Registry before the
    # prompt/tool surface is finalized.  No capability registration occurs in
    # this request path.
    capability_plan = build_capability_plan(request.message, reg) if reg is not None else None
    provider_payload = _filter_provider_payload_by_capability_plan(
        provider_payload,
        capability_plan,
    )
    available_tools = _filter_tools_by_capability_plan(available_tools, capability_plan)
    if capability_plan is not None:
        system_prompt += _capability_plan_prompt(capability_plan)
    selected_reasoning_id = _selected_reasoning_candidate(capability_plan)
    local_reasoning = getattr(ctx, "local_reasoning", None)

    if available_tools:
        log.log(f"{len(available_tools)} tools available for this request", "info")
        system_prompt += build_tool_instructions(available_tools)
        if stream is not None:
            names = ", ".join(tool.get("function", {}).get("name", "?") for tool in available_tools[:8])
            stream.emit(EVENT_THINKING, step=0, phase="prompt",
                        text=f"{len(available_tools)} tools armed: {names}"
                              + ("…" if len(available_tools) > 8 else ""))

    state.set("thinking", "Calling API...")
    active = pm.active_name(provider_payload)
    if active:
        log.log(f"Calling {active} model...", "info")
    if stream is not None:
        stream.emit(EVENT_THINKING, step=0, phase="call",
                    text=f"Calling {active or 'default'} provider"
                         + (f" ({pm.model_for(active)})" if active else "") + " — reasoning…")
    provider_calls: dict[str, bool] = {}

    def _on_provider(name: str, ok: bool) -> None:
        provider_calls[name] = ok
        if not ok:
            # Failover is self-healing the user should see happening.
            _self_correction(
                0, None, f"provider {name} failed",
                "failing over to the next configured provider", attempt=0,
                call_id=f"provider:{name}:attempt:0",
                transition_type="retry",
                semantic_capability=SemanticCapabilityType.REASONING.value,
            )

    started = time.monotonic()
    messages = _conversation_messages(request.message, history, system_prompt)
    llm_result = await asyncio.to_thread(
        _execute_llm_request,
        request.message, history, provider_payload,
        system_prompt=system_prompt, tools=available_tools, on_provider=_on_provider,
        on_delta=stream.delta_forwarder(0) if stream is not None else None,
        local_adapter=local_reasoning,
        selected_capability_id=selected_reasoning_id,
    )
    _record_reasoning_result(ctx, llm_result)
    if llm_result.outcome != ExecutionOutcome.SUCCESS:
        failed_reasoning_id = selected_reasoning_id or llm_result.capability_id
        llm_result, capability_plan, did_recover = _recover_reasoning_failure(
            ctx,
            capability_plan,
            llm_result,
            message=request.message,
            history=history,
            provider_payload=provider_payload,
            kwargs={
                "system_prompt": system_prompt,
                "tools": available_tools,
                "on_provider": _on_provider,
                "on_delta": stream.delta_forwarder(0) if stream is not None else None,
            },
        )
        if (
            did_recover
            and failed_reasoning_id
            and llm_result.capability_id
            and failed_reasoning_id != llm_result.capability_id
        ):
            replacement_descriptor = _get_registry(ctx).get(llm_result.capability_id) if _get_registry(ctx) else None
            replacement_metadata = _capability_transport_metadata(replacement_descriptor)
            _self_correction(
                0, None,
                "A verified reasoning capability replacement completed successfully",
                "capability replacement",
                attempt=0,
                transition_type="capability_replacement",
                semantic_capability=replacement_metadata.get("semantic_capability"),
                previous_candidate=failed_reasoning_id,
                selected_candidate=llm_result.capability_id,
                outcome=llm_result.outcome.value,
                locality=replacement_metadata.get("locality"),
            )
        _record_reasoning_result(ctx, llm_result)
    completion = _llm_completion(llm_result)
    if completion is None:
        # The normalized failure class is the only feedback allowed past the
        # boundary.  In particular, diagnostics.raw_message never enters the
        # ReAct context or the streamed thought terminal.
        return _llm_failure_response(llm_result)
    # Record per-provider success in the capability registry only for the
    # pre-existing health bridge.  The execution boundary itself remains
    # registry-agnostic.
    _record_provider_success(ctx, provider_calls)
    if stream is not None:
        stream.flush_delta(0, text=_stream_visible_thought(_assistant_message(completion)))
    assistant_message = _assistant_message(completion)
    return await _react_loop(
        ctx, provider_payload=provider_payload, available_tools=available_tools,
        messages=messages, assistant_message=assistant_message, completion=completion,
        provider_calls=provider_calls, on_provider=_on_provider,
        steps=0, brain_tools_called=[], session_id=request.session_id,
        original_message=request.message, started=started,
        input_mode=str(request.input_mode or "text"),
        capability_plan=capability_plan,
    )


def _stream_visible_thought(message: dict) -> str:
    """The model's own words for this step, safe to show in the terminal."""
    raw = message.get("reasoning_content") or message.get("content")
    if not isinstance(raw, str):
        return ""
    return strip_tool_tags(raw)[:1200]


def _arguments_preview(arguments: dict) -> str:
    try:
        return _clip(json.dumps(arguments, ensure_ascii=False, default=str), 120)
    except (TypeError, ValueError):
        return "{}"


async def _await_device_observation(
    session_id: str, tool_name: str, timeout: float | None = None,
) -> tuple[str, bool, str, bool]:
    """Park the live stream until the Body POSTs /agent/result for this session.

    Returns ``(observation_text, success, tool, timed_out)``. The legacy
    (non-streamed) contract never gets here: it ends the request and resumes
    from the saved agent session instead.
    """
    limit = timeout if timeout is not None else TOOL_RESULT_TIMEOUT_SECONDS
    future = TOOL_RESULT_BRIDGE.register(session_id)
    try:
        payload = await asyncio.wait_for(future, timeout=limit)
    except asyncio.TimeoutError:
        return ("", False, tool_name, True)
    finally:
        TOOL_RESULT_BRIDGE.release(session_id, future)
    result = str(payload.get("result") or "")
    success = bool(payload.get("success", True))
    tool = str(payload.get("tool") or tool_name)
    if not success:
        # Body callbacks have no normalized boundary object.  Preserve only a
        # safe class-level observation; never feed the callback's raw error
        # text (which may include device diagnostics) to the model.
        return (_device_failure_observation(tool), False, tool, False)
    return (f"[{tool} succeeded]: {result.strip() or '(empty result)'}", True, tool, False)



async def _react_loop(
    ctx: BrainContext,
    *,
    provider_payload: list[dict],
    available_tools: list[dict],
    messages: list[dict],
    assistant_message: dict,
    completion: dict,
    provider_calls: dict[str, bool],
    on_provider,
    steps: int,
    brain_tools_called: list[str],
    session_id: str,
    original_message: str,
    started: float,
    input_mode: str = "text",
    capability_plan: CapabilityPlan | None = None,
) -> AskResponse:
    """Thought -> Action -> Observation until final speech or device dispatch.

    With a live stream attached, dispatched device tools are awaited *inside*
    the loop (the Body answers on /agent/result, which resolves the bridge), so
    the whole ReAct run rides one SSE connection. Without a stream the dispatch
    returns a pending ``AgentAction`` exactly as before.
    """
    log, stats, state = ctx.log, ctx.stats, ctx.state
    stream = _stream()
    #: True once a dispatched device tool has been observed inside this stream.
    resume_after_dispatch = False

    while True:
        tool_calls = extract_tool_calls(assistant_message) if available_tools else []
        if not tool_calls:
            break
        if steps >= MAX_AGENT_STEPS:
            log.log("Agent step budget exhausted; summarizing", "warning")
            _self_correction(steps, None, f"step budget ({MAX_AGENT_STEPS}) exhausted",
                             "stop acting and summarize what is verified so far", attempt=steps)
            break

        messages.append(_assistant_replay_message(assistant_message, tool_calls))
        replay_index = len(messages) - 1
        brain_done = 0
        resume_after_dispatch = False

        for tool_call in tool_calls:
            function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
            tool_name = str(function.get("name", ""))
            arguments = function.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}
            call_id = str(tool_call.get("id") or f"step-{steps}:{tool_name}")
            selected_descriptor = _selected_candidate_descriptor(
                capability_plan, _get_registry(ctx), tool_name,
            )
            tool_attempt = 0

            raw_thought = assistant_message.get("content")
            thought = strip_tool_tags(raw_thought)[:2000] if isinstance(raw_thought, str) else None

            # --- Device tool: dispatch to the Kotlin Body, await callback ---
            if is_device_tool(tool_name):
                trimmed = tool_calls[: brain_done + 1]
                messages[replay_index] = _assistant_replay_message(assistant_message, trimmed)
                action_payload = {
                    "tool": tool_name, "args": arguments,
                    "tool_call_id": tool_call.get("id") if isinstance(tool_call, dict) else None,
                    "thought": thought or None,
                }
                _save_agent_session(session_id, {
                    "messages": messages,
                    "provider_payload": provider_payload,
                    "available_tools": available_tools,
                    "steps": steps,
                    "brain_tools_called": brain_tools_called,
                    "original_message": original_message,
                    "pending_tool": tool_name,
                    "pending_id": tool_call.get("id", "") if isinstance(tool_call, dict) else "",
                    "provider_calls": provider_calls,
                    "started": started,
                    "input_mode": input_mode,
                    "capability_plan": capability_plan,
                })
                catalog_id = _tool_catalog_id(tool_name)
                state.set("executing", f"Running {tool_label(catalog_id)} on device...")
                log.log(f"Dispatching device tool: {tool_name} { _clip(json.dumps(arguments, ensure_ascii=False), 80)}", "tool")
                _tool_call(steps, tool_name, arguments, device=True, thought=thought,
                           label=f"Executing {tool_name} on the Body", action=action_payload,
                           call_id=call_id, descriptor=selected_descriptor,
                           attempt=tool_attempt)
                if stream is None:
                    return AskResponse(
                        response="",
                        route="agent_action",
                        action=AgentAction(**action_payload),
                        needs_tool_result=True,
                        thought=thought or None,
                        steps=steps,
                    )
                # Live stream: keep the connection and wait for the observation.
                dispatch_started = time.monotonic()
                observation_text, device_ok, observed_tool, timed_out = await _await_device_observation(
                    session_id, tool_name)
                device_ms = int((time.monotonic() - dispatch_started) * 1000)
                if timed_out:
                    observation_text = _device_failure_observation(tool_name, timed_out=True)
                    log.log(f"Device tool {tool_name} timed out waiting for the Body", "error")
                    # Nobody is coming with an answer: drop the resume session so a
                    # late callback cannot run the loop a second time.
                    _clear_agent_session(session_id)
                if device_ok:
                    log.log(f"Device tool {tool_name} finished in {device_ms} ms", "success")
                else:
                    log.log(f"Device tool {tool_name} failed in {device_ms} ms — agent will self-correct",
                            "warning")
                _observation(
                    steps, tool_name, device_ok, observation_text, device_ms,
                    call_id=call_id,
                    outcome=(ExecutionOutcome.SUCCESS.value if device_ok else
                             ExecutionOutcome.RETRYABLE_FAILURE.value if timed_out else
                             ExecutionOutcome.FATAL_FAILURE.value),
                    metadata=_capability_transport_metadata(selected_descriptor),
                    attempt=tool_attempt,
                )
                if not device_ok:
                    _self_correction(
                        steps, tool_name,
                        "device action timed out; retry with another strategy" if timed_out
                        else _clip(observation_text, 200),
                        _correction_for_failure(observation_text if not timed_out else "timeout", tool_name),
                        attempt=steps, call_id=call_id,
                        semantic_capability=(
                            _capability_transport_metadata(selected_descriptor)
                            .get("semantic_capability")
                        ),
                        locality=_capability_transport_metadata(selected_descriptor).get("locality"))
                messages.append({
                    "role": "tool",
                    "tool_call_id": action_payload.get("tool_call_id") or "",
                    "name": observed_tool or tool_name,
                    "content": observation_text[:MAX_OBSERVATION_CHARS],
                })
                device_result = ExecutionResult(
                    outcome=(ExecutionOutcome.SUCCESS if device_ok
                             else ExecutionOutcome.RETRYABLE_FAILURE if timed_out
                             else ExecutionOutcome.FATAL_FAILURE),
                    capability_id=f"tool-device-{observed_tool or tool_name}",
                    failure_class=(None if device_ok else
                                   CanonicalFailureClass.TRANSIENT if timed_out
                                   else CanonicalFailureClass.UNKNOWN_FATAL),
                )
                if device_ok:
                    _record_capability_result(
                        capability_plan, device_result,
                        tool_name=observed_tool or tool_name,
                    )
                else:
                    capability_plan, affected_sub_goal_id = replan_affected_subgoal(
                        capability_plan,
                        _get_registry(ctx),
                        device_result,
                        tool_name=observed_tool or tool_name,
                    )
                    if affected_sub_goal_id:
                        messages.append({
                            "role": "system",
                            "content": _capability_plan_prompt(
                                capability_plan,
                                affected_sub_goal_id=affected_sub_goal_id,
                            ),
                        })
                await stats.record_tool_usage(_tool_catalog_id(observed_tool or tool_name),
                                              _clip(observation_text, 80), device_ok)
                # One dispatched action per step: reason over its observation now.
                resume_after_dispatch = True
                break

            # --- Brain tool: execute inline, feed observation back ---
            state.set("executing", f"Running {tool_label(_tool_catalog_id(tool_name))}...")
            log.log(f"Executing tool: {tool_name}", "tool")
            _tool_call(steps, tool_name, arguments, device=False, thought=thought,
                       label=f"Executing {tool_name} in the Brain", call_id=call_id,
                       descriptor=selected_descriptor, attempt=tool_attempt)
            tool_started = time.monotonic()
            exec_result = await asyncio.to_thread(
                _execute_tool_request, tool_name, arguments, ctx,
            )
            tool_ms = exec_result.elapsed_ms or int((time.monotonic() - tool_started) * 1000)
            result_text, tool_ok = _tool_execution_observation(tool_name, exec_result)
            effective_tool_name = tool_name
            affected_sub_goal_id: str | None = None

            # Backend recovery happens before the failed observation is handed
            # to the model. The original attempt is still transported to the
            # Body, and each verified alternative gets the same call ID with a
            # distinct attempt number.
            failed_candidate_id = selected_descriptor.id if selected_descriptor is not None else None
            initial_observation_emitted = False
            if not tool_ok:
                _observation(
                    steps, tool_name, False, result_text, tool_ms,
                    call_id=call_id,
                    outcome=exec_result.outcome.value,
                    metadata=_capability_transport_metadata(
                        selected_descriptor, exec_result.metadata,
                    ),
                    attempt=tool_attempt,
                )
                initial_observation_emitted = True

                def _execute_recovery_candidate(candidate: CapabilityDescriptor) -> ExecutionResult:
                    nonlocal tool_attempt
                    tool_attempt += 1
                    candidate_tool_name = (
                        _capability_tool_name(candidate, tool_name) or tool_name
                    )
                    _tool_call(
                        steps,
                        candidate_tool_name,
                        arguments,
                        device=False,
                        thought=thought,
                        label=f"Executing {candidate_tool_name} in the Brain",
                        call_id=call_id,
                        descriptor=candidate,
                        attempt=tool_attempt,
                    )
                    return _execute_registered_candidate(
                        candidate,
                        original_tool_name=tool_name,
                        arguments=arguments,
                        ctx=ctx,
                    )

                recovered_result, affected_sub_goal_id, recovered = recover_failed_subgoal(
                    capability_plan,
                    _get_registry(ctx),
                    exec_result,
                    tool_name=tool_name,
                    execute_candidate=_execute_recovery_candidate,
                )
                if recovered_result is not exec_result:
                    exec_result = recovered_result
                    registry = _get_registry(ctx)
                    descriptor = (
                        registry.get(exec_result.capability_id)
                        if registry is not None and exec_result.capability_id
                        else None
                    )
                    selected_descriptor = descriptor
                    effective_tool_name = (
                        _capability_tool_name(descriptor, tool_name)
                        if descriptor is not None
                        else tool_name
                    ) or tool_name
                    result_text, tool_ok = _tool_execution_observation(
                        effective_tool_name, exec_result,
                    )
                    tool_ms = exec_result.elapsed_ms or tool_ms
                    if recovered:
                        log.log(
                            f"Recovered {affected_sub_goal_id} via {exec_result.capability_id}",
                            "success",
                        )
                        if (
                            failed_candidate_id
                            and exec_result.capability_id
                            and failed_candidate_id != exec_result.capability_id
                        ):
                            replacement_metadata = _capability_transport_metadata(selected_descriptor)
                            _self_correction(
                                steps,
                                effective_tool_name,
                                "A verified capability replacement completed successfully",
                                "capability replacement",
                                attempt=tool_attempt,
                                call_id=call_id,
                                transition_type="capability_replacement",
                                semantic_capability=replacement_metadata.get("semantic_capability"),
                                previous_candidate=failed_candidate_id,
                                selected_candidate=exec_result.capability_id,
                                outcome=exec_result.outcome.value,
                                locality=replacement_metadata.get("locality"),
                            )
                    elif affected_sub_goal_id:
                        log.log(
                            f"Recovery exhausted for {affected_sub_goal_id}; returning sanitized observation",
                            "warning",
                        )

            catalog_id = _tool_catalog_id(effective_tool_name)
            await stats.record_tool_usage(
                catalog_id,
                _clip(json.dumps(arguments, ensure_ascii=False)[:80]),
                tool_ok,
            )
            if catalog_id == "web_search":
                await stats.bump("web_searches")
            if catalog_id == "app_control":
                await stats.bump("apps_opened")
            if catalog_id in {"agent_memory", "note_creator"} and tool_ok:
                await stats.bump("learned")
            brain_tools_called.append(effective_tool_name)
            transport_metadata = _capability_transport_metadata(
                selected_descriptor,
                exec_result.metadata,
            )
            if not initial_observation_emitted or tool_attempt > 0:
                _observation(
                    steps, effective_tool_name, tool_ok, result_text, tool_ms,
                    call_id=call_id,
                    outcome=exec_result.outcome.value,
                    metadata=transport_metadata,
                    attempt=tool_attempt,
                )
            if tool_ok:
                log.log(f"Tool {effective_tool_name} finished in {tool_ms} ms", "success")
                _record_capability_result(
                    capability_plan,
                    exec_result,
                    tool_name=effective_tool_name,
                )
            else:
                # The next Thought sees a canonical, sanitized observation;
                # raw boundary diagnostics never enter messages or the stream.
                log.log(
                    f"Tool {effective_tool_name} failed ({exec_result.outcome.value}) in {tool_ms} ms — agent will self-correct",
                    "warning",
                )
                _self_correction(
                    steps,
                    effective_tool_name,
                    result_text,
                    _correction_for_failure_class(
                        _failure_class_for_result(exec_result), effective_tool_name,
                    ),
                    attempt=tool_attempt,
                    call_id=call_id,
                    semantic_capability=_capability_transport_metadata(
                        selected_descriptor,
                    ).get("semantic_capability"),
                    locality=_capability_transport_metadata(selected_descriptor).get("locality"),
                )
                if affected_sub_goal_id is None:
                    capability_plan, affected_sub_goal_id = replan_affected_subgoal(
                        capability_plan,
                        _get_registry(ctx),
                        exec_result,
                        tool_name=effective_tool_name,
                    )
                if affected_sub_goal_id:
                    messages.append({
                        "role": "system",
                        "content": _capability_plan_prompt(
                            capability_plan,
                            affected_sub_goal_id=affected_sub_goal_id,
                        ),
                    })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.get("id", "") if isinstance(tool_call, dict) else "",
                # Keep the protocol name paired with the model's original
                # tool_call; the observation content carries the verified
                # alternative result.
                "name": tool_name,
                "content": result_text[:MAX_OBSERVATION_CHARS],
            })
            brain_done += 1

        # GROQ RATE LIMIT BYPASS: 2 second ka pause
        await _rate_limit_pause()
        state.set("thinking", "Reasoning over tool results...")
        _thinking("Reasoning over tool results…", step=steps + 1,
                  phase="reason" if not resume_after_dispatch else "verify")
        next_delta = stream.delta_forwarder(steps + 1) if stream is not None else None
        llm_result = await asyncio.to_thread(
            _execute_llm_request,
            "", [], provider_payload,
            tools=available_tools,
            messages=messages,
            on_provider=on_provider,
            on_delta=next_delta,
            local_adapter=getattr(ctx, "local_reasoning", None),
            selected_capability_id=_selected_reasoning_candidate(capability_plan),
        )
        _record_reasoning_result(ctx, llm_result)
        if llm_result.outcome != ExecutionOutcome.SUCCESS:
            llm_result, capability_plan, _ = _recover_reasoning_failure(
                ctx,
                capability_plan,
                llm_result,
                message="",
                history=[],
                provider_payload=provider_payload,
                kwargs={
                    "tools": available_tools,
                    "messages": messages,
                    "on_provider": on_provider,
                    "on_delta": next_delta,
                },
            )
            _record_reasoning_result(ctx, llm_result)
        completion = _llm_completion(llm_result)
        if completion is None:
            # A failed re-plan ends this bounded run cleanly.  It does not
            # consume another retry, enter offline mode, or expose diagnostics.
            return _llm_failure_response(llm_result, steps=steps + 1)
        if stream is not None:
            stream.flush_delta(steps + 1, text=_stream_visible_thought(_assistant_message(completion)))
        assistant_message = _assistant_message(completion)
        steps += 1

    elapsed_ms = int((time.monotonic() - started) * 1000)
    _record_provider_latency(ctx, completion, elapsed_ms, provider_payload, provider_calls)
    # Do not fall back to the raw content here.  An action-only fallback
    # response intentionally cleans to an empty string; restoring the raw
    # value would put its JSON/[ACTION] wire syntax into the chat bubble.
    final_text = strip_tool_tags(_response_text(assistant_message))

    # Self-learning safety net: an explicit "remember X" must persist even if
    # the model forgot to call save_memory in this task.
    await _ensure_remember_persisted(ctx, original_message, brain_tools_called)

    acted = bool(brain_tools_called) or steps > 0 or resume_after_dispatch
    log.log("Response ready", "success")
    if stream is not None:
        # The task completed on this connection: no resume session to keep.
        _clear_agent_session(session_id)
    return AskResponse(
        response=final_text, route="agent_final" if acted else "llm", steps=steps,
    )


async def _ensure_remember_persisted(ctx: BrainContext, message: str, brain_tools_called: list[str]) -> None:
    value = " ".join(message.split())
    lowered = value.casefold()
    if not lowered.startswith(("remember ", "save fact ")):
        return
    if "save_memory" in brain_tools_called:
        return
    fact = value.split(" ", 1)[-1].strip()
    if not fact:
        return
    try:
        engine = getattr(ctx, "memory_engine", None)
        await store_fact(fact[:120].lower(), fact)
        if engine is not None:
            title = _clip(fact, 48)
            await engine.add(category="knowledge", title=title, content=fact,
                             importance=3, source="chat")
        await ctx.stats.bump("learned")
        ctx.log.log("Auto-saved to memory (agent skipped save_memory)", "info")
    except Exception as exc:
        ctx.log.log(f"Memory safety-net failed: {exc}", "warning")


async def continue_with_tool_result(request: ToolResultRequest, ctx: BrainContext) -> AskResponse:
    """Resume the ReAct loop with a Body execution observation (``/agent/result``)."""
    log, state, stats = ctx.log, ctx.state, ctx.stats
    session = _load_agent_session(request.session_id)
    if session is None:
        log.log("Tool result arrived with no pending action", "warning")
        return AskResponse(
            response="I lost the thread of that action, Boss. Please ask again.",
            route="agent_final",
            error="no_pending_action",
        )

    messages = list(session.get("messages", []))
    provider_payload = session.get("provider_payload", [])
    available_tools = session.get("available_tools", [])
    steps = int(session.get("steps", 0))
    brain_tools_called = list(session.get("brain_tools_called", []))
    original_message = str(session.get("original_message", ""))
    provider_calls = dict(session.get("provider_calls", {}))
    started = float(session.get("started", time.monotonic()))
    pending_id = str(session.get("pending_id", "") or request.tool_call_id or "")
    input_mode = str(session.get("input_mode", "text") or "text")
    capability_plan = session.get("capability_plan")

    observation = (
        f"[{request.tool} succeeded]: {(request.result or '').strip() or '(empty result)'}"
        if request.success else _device_failure_observation(request.tool)
    )
    messages.append({
        "role": "tool",
        "tool_call_id": pending_id,
        "name": request.tool,
        "content": observation[:MAX_OBSERVATION_CHARS],
    })
    device_result = ExecutionResult(
        outcome=(ExecutionOutcome.SUCCESS if request.success else ExecutionOutcome.FATAL_FAILURE),
        capability_id=f"tool-device-{request.tool}",
        failure_class=None if request.success else CanonicalFailureClass.UNKNOWN_FATAL,
    )
    if request.success:
        _record_capability_result(capability_plan, device_result, tool_name=request.tool)
    else:
        capability_plan, affected_sub_goal_id = replan_affected_subgoal(
            capability_plan,
            _get_registry(ctx),
            device_result,
            tool_name=request.tool,
        )
        if affected_sub_goal_id:
            messages.append({
                "role": "system",
                "content": _capability_plan_prompt(
                    capability_plan,
                    affected_sub_goal_id=affected_sub_goal_id,
                ),
            })
    catalog_id = _tool_catalog_id(request.tool)
    await stats.record_tool_usage(catalog_id, _clip(request.result or "", 80), request.success)
    if request.tool == "open_app" and request.success:
        await stats.bump("apps_opened")
    if request.success:
        log.log(f"Device tool {request.tool} finished", "success")
    else:
        log.log(f"Device tool {request.tool} failed — agent will self-correct", "warning")

    def _on_provider(name: str, ok: bool) -> None:
        provider_calls[name] = ok

    state.set("thinking", "Reasoning over device result...")
    await _rate_limit_pause()  # GROQ RATE LIMIT BYPASS
    llm_result = await asyncio.to_thread(
        _execute_llm_request,
        "", [], provider_payload,
        tools=available_tools,
        messages=messages,
        on_provider=_on_provider,
        local_adapter=getattr(ctx, "local_reasoning", None),
        selected_capability_id=_selected_reasoning_candidate(capability_plan),
    )
    _record_reasoning_result(ctx, llm_result)
    if llm_result.outcome != ExecutionOutcome.SUCCESS:
        llm_result, capability_plan, _ = _recover_reasoning_failure(
            ctx,
            capability_plan,
            llm_result,
            message="",
            history=[],
            provider_payload=provider_payload,
            kwargs={
                "tools": available_tools,
                "messages": messages,
                "on_provider": _on_provider,
            },
        )
        _record_reasoning_result(ctx, llm_result)
    completion = _llm_completion(llm_result)
    if completion is None:
        log.log("Reasoning failed after device observation", "error")
        state.set("idle", "AI unavailable. Please retry.")
        return _llm_failure_response(llm_result, steps=steps + 1)
    assistant_message = _assistant_message(completion)
    result = await _react_loop(
        ctx, provider_payload=provider_payload, available_tools=available_tools,
        messages=messages, assistant_message=assistant_message, completion=completion,
        provider_calls=provider_calls, on_provider=_on_provider,
        steps=steps + 1, brain_tools_called=brain_tools_called,
        session_id=request.session_id, original_message=original_message, started=started,
        input_mode=input_mode,
        capability_plan=capability_plan,
    )
    if not result.needs_tool_result:
        _clear_agent_session(request.session_id)
        # The task completes here: persist the full turn + finalize stats.
        await save_conversation(request.session_id, original_message, result.response)
        if result.error is None:
            await stats.bump("tasks_completed")
        if input_mode.lower() == "voice":
            await stats.bump("voice_commands")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.log(f"Agent task complete in {elapsed_ms} ms", "warning" if result.error else "success")
        state.set("idle", "Ready. Waiting for your command.")
    return result


def _record_provider_latency(
    ctx: BrainContext,
    completion: dict,
    elapsed_ms: int,
    provider_payload: list[dict],
    provider_calls: dict[str, bool] | None = None,
) -> None:
    """Attribute latency/success to the provider that actually answered."""
    pm = ctx.provider_manager
    success_name: str | None = None
    if provider_calls:
        success_name = next((name for name, ok in provider_calls.items() if ok), None)
    if success_name is None:
        # No callback info: attribute to the active provider when the call succeeded.
        ok = isinstance(completion, dict) and bool(completion.get("choices"))
        if ok:
            success_name = pm.active_name(provider_payload)
    if success_name is None:
        return
    ok = isinstance(completion, dict) and bool(completion.get("choices"))
    pm.record(success_name, elapsed_ms if ok else None, ok)
    if ok:
        model = provider_payload and None
        for provider in provider_payload:
            if str(provider.get("provider") or "").lower() == success_name:
                model = provider.get("model")
                break
        ctx.state.set(ctx.state.get()["state"], provider=success_name,
                      model=model or pm.model_for(success_name))


def _enabled_map(request: AskRequest) -> dict[str, bool]:
    if not request.tools_enabled:
        return {}
    return {str(k).lower(): bool(v) for k, v in request.tools_enabled.items()}


def _tool_disabled(request: AskRequest, tool_id: str) -> bool:
    enabled = _enabled_map(request)
    return enabled.get(tool_id, True) is False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def plan_request(request: AskRequest, ctx: BrainContext,
                       settings_path: Path | None = None,
                       *, stream: AgentStreamer | None = None) -> AskResponse:
    """Full pipeline for one user request (state + logs + stats + answer).

    Pass ``stream`` to also publish the loop's internal monologue in real time
    and to stream the final answer chunk-by-chunk; ``None`` keeps the original
    single-shot JSON behaviour untouched.
    """
    log, state, stats = ctx.log, ctx.state, ctx.stats
    token = bind_stream(stream) if stream is not None else None
    if stream is not None:
        stream.bind_loop()
    try:
        return await _plan_request_inner(request, ctx, stream=stream)
    except Exception:  # a stream must always end with a terminal frame
        if stream is not None:
            # Unexpected failures are intentionally not copied into the stream;
            # normalized boundary failures have already been interpreted above.
            stream.emit(EVENT_ERROR, code="brain_error", message="Planner execution failed",
                        fatal=True, elapsed_ms=_elapsed(stream))
        raise
    finally:
        if token is not None:
            unbind_stream(token)


def _elapsed(stream: AgentStreamer | None) -> int:
    if stream is None:
        return 0
    return int((time.monotonic() - stream.started_at) * 1000)


async def _plan_request_inner(request: AskRequest, ctx: BrainContext, *,
                              stream: AgentStreamer | None) -> AskResponse:
    """The routing + execution half of :func:`plan_request` (stream-aware)."""
    log, state, stats = ctx.log, ctx.state, ctx.stats
    message = request.message
    started = time.monotonic()

    ctx.provider_manager.mark_keys([p.model_dump() for p in request.providers if p.api_key])
    log.log(f"Received request: \"{_clip(message, 64)}\"", "info")

    active = ctx.provider_manager.active_name([p.model_dump() for p in request.providers])
    state.set("thinking", "Analyzing request...", provider=active,
              model=ctx.provider_manager.model_for(active) if active else None)
    _thinking("Analyzing prompt…", phase="plan")

    decision = route_request(message)  # Planner -> Intent Router (agent-first)
    log.log(f"Intent routed to {decision.route} — {decision.reason}", "info")
    if stream is not None:
        stream.emit(EVENT_THINKING, step=0, phase="route",
                    text=f"Intent → {decision.route} ({decision.reason})")

    try:
        route = decision.route
        if route == "tool_creation":
            result = await _run_tool_creation(request, ctx)
        elif route == "android_command":
            result = await _run_android_command(request, ctx)
        elif route == "web_search":
            result = await _run_web_search(request, ctx)
        elif route == "local_tool":
            result = await _run_local_tool(request, ctx)
        else:
            result = await _run_llm(request, ctx)
            if result.error == "llm_unavailable":
                # Offline fallback: explicit commands still work with zero keys.
                fallback = legacy_route(message)
                if fallback.route not in {"llm", "tool_creation"}:
                    log.log(f"LLM unavailable — offline fallback: {fallback.route}", "warning")
                    _self_correction(
                        0, None, "no AI provider reachable",
                        f"offline legacy route → {fallback.route}", attempt=0,
                        transition_type="fallback_proposal",
                        semantic_capability=SemanticCapabilityType.REASONING.value,
                    )
                    if fallback.route == "android_command":
                        result = await _run_android_command(request, ctx)
                    elif fallback.route == "web_search":
                        result = await _run_web_search(request, ctx)
                    elif fallback.route == "local_tool":
                        result = await _run_local_tool(request, ctx)
    except LLMError:
        # Boundary failures are handled as ExecutionResults; this is only an
        # unexpected legacy escape hatch and must stay sanitized as well.
        log.log("AI engine execution failed", "error")
        print("\n[🔥 RONIN CRITICAL ERROR]: AI engine execution failed\n", flush=True)
        result = AskResponse(
            response="RONIN could not complete that request.",
            route=decision.route,
            error="llm_execution_failed",
        )
    except Exception:
        # Keep the endpoint alive without copying raw exception text into the
        # API response or streamed UI.
        log.log("Unexpected planner execution failure", "error")
        print("\n[🔥 RONIN CRITICAL ERROR]: planner execution failed\n", flush=True)
        result = AskResponse(
            response="RONIN could not complete that request.",
            route=decision.route,
            error="planner_execution_failed",
        )

    # Pending device action: the Body will call back on /agent/result, which
    # finalizes the turn. Stay in executing state so the orb shows acting.
    if result.needs_tool_result and result.action is not None:
        log.log(f"Waiting for device result: {result.action.tool}", "info")
        if stream is not None:
            # A legacy Body on a streaming connection still needs a terminal frame.
            stream.emit(EVENT_DONE, response=result.response, route=result.route,
                        steps=result.steps, thought=result.thought, error=result.error,
                        needs_tool_result=True, action=result.action.model_dump(mode="json"),
                        elapsed_ms=_elapsed(stream))
        return result

    await save_conversation(request.session_id, message, result.response)
    if result.error is None:
        await stats.bump("tasks_completed")
    if str(request.input_mode or "text").lower() == "voice":
        await stats.bump("voice_commands")

    # Typewriter: the finalized, tag-free answer is streamed to the Body before
    # the terminal `done` frame so the bubble can render it as it is "typed".
    if stream is not None and result.response:
        await stream.stream_answer(result.response, step=result.steps)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.log(f"Request complete in {elapsed_ms} ms", "warning" if result.error else "success")
    state.set("idle", "Ready. Waiting for your command.",
              provider=ctx.provider_manager.active_name([p.model_dump() for p in request.providers]),
              response_ms=elapsed_ms)
    if stream is not None:
        stream.emit(EVENT_DONE, response=result.response, route=result.route,
                    steps=result.steps, thought=result.thought, error=result.error,
                    needs_tool_result=False, streamed=True, elapsed_ms=elapsed_ms,
                    command=result.command.model_dump(mode="json") if result.command else None,
                    update_proposal=result.update_proposal.model_dump(mode="json")
                    if result.update_proposal else None)
    return result


async def plan_request_stream(request: AskRequest, ctx: BrainContext) -> AsyncIterator[str]:
    """SSE frame generator for one autonomous-agent turn.

    The agent runs as an independent task so a disconnected client never kills
    the loop (memory, stats and history still get written); the generator only
    forwards frames while the Body is listening.
    """
    streamer = AgentStreamer()
    streamer.bind_loop()

    async def _drive() -> None:
        try:
            # plan_request() owns the terminal `done` frame (it holds the result),
            # and the `error` frame for contained failures.
            await plan_request(request, ctx, stream=streamer)
        except asyncio.CancelledError:
            streamer.emit(EVENT_ERROR, code="cancelled", message="stream cancelled", fatal=True)
            raise
        except Exception:
            # plan_request re-raises only unexpected blow-ups after emitting `error`.
            pass
        finally:
            streamer.close()

    task = asyncio.create_task(_drive(), name=f"vyrx-stream-{streamer.request_id}")
    _DETACHED_TASKS.add(task)
    task.add_done_callback(_DETACHED_TASKS.discard)
    streamer.emit(
        EVENT_START,
        request_id=streamer.request_id,
        session_id=request.session_id,
        message=_clip(request.message, 200),
        input_mode=request.input_mode,
        state=ctx.state.get(),
    )
    try:
        async for frame in streamer.frames():
            yield frame
    finally:
        # Client gone (or stream exhausted): stop generating frames, let the
        # agent finish its work in the background.
        streamer.detach()


#: In-flight streamed turns that outlived their HTTP connection.
_DETACHED_TASKS: set[asyncio.Task] = set()
