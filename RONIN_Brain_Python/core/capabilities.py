"""Foundational schemas for VYRX's Capability-Oriented Architecture.

Provides vendor-agnostic capability semantics, two-level failure classification,
execution outcome modelling, and policy-driven health evaluation.  All types are
pure data structures with no runtime side-effects — they are designed to be
consumed by the planner, provider manager, and future orchestration layers.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ==============================================================================
# 1. Abstract Capability Semantics & Health
# ==============================================================================

class SemanticCapabilityType(str, Enum):
    """Vendor-agnostic capability definitions."""

    REASONING = "reasoning"
    SUMMARIZATION = "summarization"
    SYNTHESIS = "synthesis"
    CLASSIFICATION = "classification"
    WEB_RETRIEVAL = "web_retrieval"
    LOCAL_KNOWLEDGE_SEARCH = "local_knowledge_search"
    DETERMINISTIC_COMPUTE = "deterministic_compute"
    FILE_SYSTEM_IO = "file_system_io"
    DEVICE_INTERACTION = "device_interaction"
    SYSTEM_COMMAND = "system_command"
    MEMORY_PERSISTENCE = "memory_persistence"


class CapabilityHealth(str, Enum):
    """Runtime health state of a capability."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class CapabilityDescriptor(BaseModel):
    """Describes a single capability's identity, dependencies, and state.

    This is a static declaration paired with a dynamic ``health`` field.
    The health value is updated by :class:`HealthTracker` as execution
    outcomes arrive; the descriptor itself is otherwise immutable.
    """

    # Identity & purpose
    id: str = Field(min_length=1, max_length=128)
    capability_type: SemanticCapabilityType
    description: str = Field(max_length=2048)

    # Dependencies
    requires_internet: bool = False
    requires_auth: bool = False
    required_permissions: list[str] = Field(default_factory=list)
    system_dependencies: list[str] = Field(default_factory=list)

    # Operational characteristics
    is_local: bool = True
    estimated_latency_ms: int = Field(default=0, ge=0)
    estimated_cost_tier: str = Field(default="free", max_length=64)

    # Dynamic state
    health: CapabilityHealth = CapabilityHealth.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)


# ==============================================================================
# 2. Two-Level Failure Classification & Diagnostics
# ==============================================================================

class CanonicalFailureClass(str, Enum):
    """Normalised failure classes for orchestration-level routing decisions.

    Level 1: the *canonical* class (this enum) tells the orchestrator *what kind*
    of failure occurred so it can decide whether to retry, escalate, or block.
    Level 2: the *raw* details live in :class:`DiagnosticContext` so no telemetry
    is lost.
    """

    TRANSIENT = "transient"              # rate-limit, timeout, temporary 503
    NETWORK_ISOLATED = "network_isolated"  # DNS drop, socket unreachable
    AUTH_DENIED = "auth_denied"          # invalid/expired key, 401/403
    POLICY_BLOCKED = "policy_blocked"    # permission denied, sandbox boundary
    VALIDATION_FAILED = "validation_failed"  # malformed JSON/output, Pydantic mismatch
    DETERMINISTIC_ERROR = "deterministic_error"  # file-not-found, syntax/regex error
    UNSUPPORTED_OPERATION = "unsupported_operation"  # missing capability/tool
    UNKNOWN_FATAL = "unknown_fatal"      # unhandled exceptions


class DiagnosticContext(BaseModel):
    """Captures raw exception details without losing telemetry.

    Every field is optional so callers can populate only what they have.
    """

    raw_error_type: str | None = None
    raw_message: str | None = None
    http_status: int | None = None
    endpoint_or_path: str | None = None
    stack_trace: str | None = None
    extra_details: dict[str, Any] = Field(default_factory=dict)


# --- Classification utilities ---

def classify_http_status(status_code: int) -> CanonicalFailureClass:
    """Map an HTTP status code to its canonical failure class.

    This is the Level-1 classification for HTTP-based provider/tool errors.
    Callers that have both a status code and an exception should prefer this
    over message-based heuristics when the status code is available.
    """
    if status_code in (401, 403):
        return CanonicalFailureClass.AUTH_DENIED
    if status_code == 429:
        return CanonicalFailureClass.TRANSIENT
    if status_code in (502, 503, 504):
        return CanonicalFailureClass.TRANSIENT
    if status_code in (400, 422):
        return CanonicalFailureClass.VALIDATION_FAILED
    if status_code == 404:
        return CanonicalFailureClass.DETERMINISTIC_ERROR
    if status_code == 451:
        return CanonicalFailureClass.POLICY_BLOCKED
    # Catch-all ranges
    if 400 <= status_code < 500:
        return CanonicalFailureClass.VALIDATION_FAILED
    if status_code >= 500:
        return CanonicalFailureClass.TRANSIENT
    # Below 400 is not an error
    return CanonicalFailureClass.UNKNOWN_FATAL


def classify_exception(
    exc: BaseException,
    *,
    http_status: int | None = None,
    endpoint: str | None = None,
    stack_trace: str | None = None,
    extra_details: dict[str, Any] | None = None,
) -> tuple[CanonicalFailureClass, DiagnosticContext]:
    """Classify a caught exception into a canonical failure class.

    Returns ``(failure_class, diagnostic_context)`` so callers get both the
    orchestration-level signal and the raw telemetry in one call.

    Priority order:
    1. Explicit ``http_status`` (if >= 400) — always wins.
    2. Exception type matching (ConnectionError → NETWORK_ISOLATED, etc.).
    3. Message-content heuristics for stringly-typed provider errors.
    4. Fallback to UNKNOWN_FATAL.
    """
    diag = DiagnosticContext(
        raw_error_type=type(exc).__qualname__,
        raw_message=str(exc)[:2000] if str(exc) else None,
        http_status=http_status if http_status is not None and http_status > 0 else None,
        endpoint_or_path=endpoint,
        stack_trace=stack_trace or None,
        extra_details=extra_details or {},
    )

    # 1. HTTP status — authoritative when present
    if http_status is not None and http_status >= 400:
        return classify_http_status(http_status), diag

    # 2. Exception type hierarchy
    if isinstance(exc, (ConnectionError, ConnectionRefusedError, ConnectionResetError)):
        return CanonicalFailureClass.NETWORK_ISOLATED, diag
    if isinstance(exc, TimeoutError):
        return CanonicalFailureClass.TRANSIENT, diag
    if isinstance(exc, FileNotFoundError):
        return CanonicalFailureClass.DETERMINISTIC_ERROR, diag
    if isinstance(exc, NotImplementedError):
        return CanonicalFailureClass.UNSUPPORTED_OPERATION, diag
    if isinstance(exc, (ValueError, TypeError)):
        # Covers Pydantic ValidationError (subclass of ValueError) and
        # JSONDecodeError (subclass of ValueError).
        return CanonicalFailureClass.VALIDATION_FAILED, diag

    # 3. Message heuristics — for stringly-typed provider exceptions
    msg = str(exc).casefold()

    if any(kw in msg for kw in ("timeout", "timed out", "deadline exceeded")):
        return CanonicalFailureClass.TRANSIENT, diag
    if any(kw in msg for kw in ("rate limit", "too many requests", "429")):
        return CanonicalFailureClass.TRANSIENT, diag
    if any(kw in msg for kw in ("502", "503", "504", "bad gateway",
                                 "service unavailable", "gateway timeout")):
        return CanonicalFailureClass.TRANSIENT, diag

    if any(kw in msg for kw in ("api key", "unauthorized", "forbidden",
                                 "invalid key", "expired key", "401", "403")):
        return CanonicalFailureClass.AUTH_DENIED, diag

    if any(kw in msg for kw in ("permission denied", "refused", "sandbox",
                                 "not allowed", "access denied")):
        return CanonicalFailureClass.POLICY_BLOCKED, diag

    if any(kw in msg for kw in ("malformed", "parse error", "invalid response",
                                 "invalid json", "no choices", "no candidates")):
        return CanonicalFailureClass.VALIDATION_FAILED, diag

    if any(kw in msg for kw in ("unsupported", "not supported", "not support")):
        return CanonicalFailureClass.UNSUPPORTED_OPERATION, diag
    # "tool not found" must be checked before generic "not found"
    if "tool not found" in msg:
        return CanonicalFailureClass.UNSUPPORTED_OPERATION, diag

    if any(kw in msg for kw in ("not found", "no such file", "no such file or directory")):
        return CanonicalFailureClass.DETERMINISTIC_ERROR, diag
    if any(kw in msg for kw in ("syntax error", "regex error", "compilation failed")):
        return CanonicalFailureClass.DETERMINISTIC_ERROR, diag

    # 4. Fallback
    return CanonicalFailureClass.UNKNOWN_FATAL, diag


# ==============================================================================
# 3. Execution Outcomes & Partial Success
# ==============================================================================

class ExecutionOutcome(str, Enum):
    """High-level outcome of an execution step."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    RETRYABLE_FAILURE = "retryable_failure"
    BLOCKED = "blocked"
    DENIED = "denied"
    UNSUPPORTED = "unsupported"
    FATAL_FAILURE = "fatal_failure"


class ExecutionResult(BaseModel):
    """Standardised result contract for any capability execution.

    Separates execution outcome (what happened) from failure classification
    (why it happened) from capability health (is the capability still usable).
    A single sub-goal failure is scoped by ``sub_goal_id`` and does not imply
    the parent task has failed.
    """

    outcome: ExecutionOutcome
    sub_goal_id: str | None = None
    capability_id: str | None = None
    data: Any = None
    partial_data: Any = None
    failure_class: CanonicalFailureClass | None = None
    diagnostics: DiagnosticContext | None = None
    # Safe producer metadata for transport adapters. This is deliberately
    # separate from outcome, health, and diagnostics; callers must not derive
    # one of those concepts from the other.
    metadata: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int | None = Field(default=None, ge=0)


# ==============================================================================
# 4. Policy-Driven Health Evaluation
# ==============================================================================

def outcome_for_failure(failure_class: CanonicalFailureClass) -> ExecutionOutcome:
    """Map a canonical failure class to the corresponding execution outcome.

    This is the authoritative mapping shared by all execution boundary
    normalizers.  It translates *why* a failure occurred into *what the
    orchestrator should do next*.
    """
    _MAP: dict[CanonicalFailureClass, ExecutionOutcome] = {
        CanonicalFailureClass.TRANSIENT: ExecutionOutcome.RETRYABLE_FAILURE,
        CanonicalFailureClass.NETWORK_ISOLATED: ExecutionOutcome.RETRYABLE_FAILURE,
        CanonicalFailureClass.AUTH_DENIED: ExecutionOutcome.DENIED,
        CanonicalFailureClass.POLICY_BLOCKED: ExecutionOutcome.BLOCKED,
        CanonicalFailureClass.VALIDATION_FAILED: ExecutionOutcome.FATAL_FAILURE,
        CanonicalFailureClass.DETERMINISTIC_ERROR: ExecutionOutcome.FATAL_FAILURE,
        CanonicalFailureClass.UNSUPPORTED_OPERATION: ExecutionOutcome.UNSUPPORTED,
        CanonicalFailureClass.UNKNOWN_FATAL: ExecutionOutcome.FATAL_FAILURE,
    }
    return _MAP.get(failure_class, ExecutionOutcome.FATAL_FAILURE)


class HealthPolicy(BaseModel):
    """Configurable rules for capability health-state transitions.

    Keeps the thresholds and behaviours outside the state machine so operators
    can tune sensitivity without code changes.  Every threshold here is
    user-configurable — no hardcoded global counters.
    """

    #: Number of consecutive transient failures before moving to DEGRADED.
    transient_failure_threshold: int = Field(default=3, ge=1)
    #: Number of consecutive transient failures before moving to UNAVAILABLE.
    transient_unavailable_threshold: int = Field(default=5, ge=2)
    #: Any AUTH_DENIED failure immediately transitions to UNAVAILABLE.
    immediate_auth_failure: bool = True
    #: Any POLICY_BLOCKED failure immediately transitions to UNAVAILABLE.
    immediate_policy_block: bool = True
    #: Seconds of successful execution required to recover from DEGRADED → AVAILABLE.
    recovery_window_seconds: int = Field(default=60, ge=1)
    #: Number of consecutive successes required to recover to AVAILABLE
    #: (from either DEGRADED or UNAVAILABLE).
    recovery_success_count: int = Field(default=2, ge=1)


class HealthTracker:
    """Stateful helper that applies a :class:`HealthPolicy` to a capability.

    Not a Pydantic model — it is intentionally a plain object that wraps
    mutable counters.  Callers can persist the resulting
    :class:`CapabilityHealth` value into a :class:`CapabilityDescriptor` or
    any other store.

    Health state machine (simplified)::

        UNKNOWN ──success──▶ AVAILABLE
        AVAILABLE ──N transient failures──▶ DEGRADED
        DEGRADED ──M transient failures──▶ UNAVAILABLE
        DEGRADED ──recovery_success_count successes──▶ AVAILABLE
        UNAVAILABLE ──first success──▶ DEGRADED (partial recovery)
        UNAVAILABLE ──AUTH_DENIED / POLICY_BLOCKED / NETWORK_ISOLATED / ...──▶ UNAVAILABLE

    Key invariant: UNAVAILABLE is *never* permanent.  Sustained successful
    execution always recovers health, but it requires going through DEGRADED
    first so the orchestrator can observe a stable window before trusting
    the capability fully.
    """

    def __init__(
        self,
        capability_id: str,
        policy: HealthPolicy | None = None,
        initial_health: CapabilityHealth = CapabilityHealth.UNKNOWN,
    ) -> None:
        self.capability_id = capability_id
        self.policy = policy or HealthPolicy()
        self.health: CapabilityHealth = initial_health

        # Internal counters
        self._consecutive_transient_failures: int = 0
        self._consecutive_successes: int = 0
        self._last_success_timestamp: float | None = None

    # -- public API ----------------------------------------------------------

    @property
    def current_health(self) -> CapabilityHealth:
        return self.health

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive transient failures since last success."""
        return self._consecutive_transient_failures

    def reset(self, health: CapabilityHealth = CapabilityHealth.UNKNOWN) -> None:
        """Reset the tracker to a clean state (e.g. on capability re-registration)."""
        self.health = health
        self._consecutive_transient_failures = 0
        self._consecutive_successes = 0
        self._last_success_timestamp = None

    def record_success(self) -> CapabilityHealth:
        """Record a successful execution and potentially recover health.

        Recovery is *gradual*: UNAVAILABLE → DEGRADED on the first success,
        then DEGRADED → AVAILABLE after ``recovery_success_count`` additional
        consecutive successes.  This prevents a single lucky response from
        immediately declaring a previously-unavailable capability fully healthy.
        """
        self._consecutive_transient_failures = 0
        self._last_success_timestamp = time.monotonic()

        if self.health == CapabilityHealth.UNAVAILABLE:
            # Partial recovery: acknowledge the capability is responding again.
            # Transition to DEGRADED and start a fresh recovery window so
            # DEGRADED → AVAILABLE independently requires the full
            # recovery_success_count.
            self.health = CapabilityHealth.DEGRADED
            self._consecutive_successes = 1
            return self.health

        self._consecutive_successes += 1

        if self.health == CapabilityHealth.DEGRADED:
            if self._consecutive_successes >= self.policy.recovery_success_count:
                self.health = CapabilityHealth.AVAILABLE
        elif self.health == CapabilityHealth.UNKNOWN:
            # First success from unknown → available (no prior evidence of failure)
            self.health = CapabilityHealth.AVAILABLE
        # AVAILABLE stays AVAILABLE

        return self.health

    def record_failure(self, failure_class: CanonicalFailureClass) -> CapabilityHealth:
        """Record a failure and apply the health policy.

        The policy controls whether AUTH_DENIED and POLICY_BLOCKED cause an
        immediate jump to UNAVAILABLE or degrade through the normal threshold
        path.  NETWORK_ISOLATED, UNSUPPORTED_OPERATION, and UNKNOWN_FATAL
        always escalate immediately — these indicate a fundamental inability
        to execute the capability.
        """
        self._consecutive_successes = 0

        # -- Immediate escalation paths (unconditional) --
        if failure_class in (
            CanonicalFailureClass.NETWORK_ISOLATED,
            CanonicalFailureClass.UNSUPPORTED_OPERATION,
            CanonicalFailureClass.UNKNOWN_FATAL,
        ):
            self.health = CapabilityHealth.UNAVAILABLE
            return self.health

        # -- Immediate escalation paths (policy-controlled) --
        if failure_class == CanonicalFailureClass.AUTH_DENIED and self.policy.immediate_auth_failure:
            self.health = CapabilityHealth.UNAVAILABLE
            return self.health

        if failure_class == CanonicalFailureClass.POLICY_BLOCKED and self.policy.immediate_policy_block:
            self.health = CapabilityHealth.UNAVAILABLE
            return self.health

        # -- Count-based escalation for transient failures --
        if failure_class == CanonicalFailureClass.TRANSIENT:
            self._consecutive_transient_failures += 1
            count = self._consecutive_transient_failures
            if count >= self.policy.transient_unavailable_threshold:
                self.health = CapabilityHealth.UNAVAILABLE
            elif count >= self.policy.transient_failure_threshold:
                self.health = CapabilityHealth.DEGRADED
            return self.health

        # -- Degradation for non-transient, non-fatal failures --
        # VALIDATION_FAILED, DETERMINISTIC_ERROR, and policy-controlled
        # AUTH_DENIED / POLICY_BLOCKED land here.
        self.health = CapabilityHealth.DEGRADED
        return self.health
