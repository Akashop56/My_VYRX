"""Execution Boundary — safe wrapper for capability invocation.

Provides :func:`execute_capability`, a generic execution wrapper that:

1. Invokes a caller-supplied callable inside the normalized execution boundary
   (:func:`~core.execution_boundary.execute_boundary`).
2. On success: updates the registry health tracker and returns an
   :class:`~core.capabilities.ExecutionResult` with ``SUCCESS``.
3. On exception: classifies the error via
   :func:`~core.capabilities.classify_exception`, updates health, and
   returns an ``ExecutionResult`` with the appropriate failure outcome.

**Critical invariant:** this boundary *never* propagates raw exceptions to
the caller (except ``KeyboardInterrupt``, ``SystemExit``, and
``CapabilityNotFoundError``).  Every runtime exception is caught, classified,
and translated into a structured ``ExecutionResult``.  The orchestrator can
therefore call any capability through this wrapper without risk of an
unhandled blow-up.

The mapping from :class:`~core.capabilities.CanonicalFailureClass` to
:class:`~core.capabilities.ExecutionOutcome` is defined once in
:func:`~core.capabilities.outcome_for_failure` and shared with
:mod:`core.execution_boundary`.
"""
from __future__ import annotations

from typing import Any, Callable

from core.capabilities import (
    ExecutionOutcome,
    ExecutionResult,
)
from core.execution_boundary import execute_boundary
from core.registry import CapabilityNotFoundError, CapabilityRegistry


# ---------------------------------------------------------------------------
# Re-export the mapping for backward compatibility with existing tests
# that import _FAILURE_TO_OUTCOME and _outcome_for_failure from this module.
# ---------------------------------------------------------------------------

from core.capabilities import (  # noqa: E402, F401
    CanonicalFailureClass,
    outcome_for_failure as _outcome_for_failure,
)

_FAILURE_TO_OUTCOME: dict[CanonicalFailureClass, ExecutionOutcome] = {
    CanonicalFailureClass.TRANSIENT: ExecutionOutcome.RETRYABLE_FAILURE,
    CanonicalFailureClass.NETWORK_ISOLATED: ExecutionOutcome.RETRYABLE_FAILURE,
    CanonicalFailureClass.AUTH_DENIED: ExecutionOutcome.DENIED,
    CanonicalFailureClass.POLICY_BLOCKED: ExecutionOutcome.BLOCKED,
    CanonicalFailureClass.VALIDATION_FAILED: ExecutionOutcome.FATAL_FAILURE,
    CanonicalFailureClass.DETERMINISTIC_ERROR: ExecutionOutcome.FATAL_FAILURE,
    CanonicalFailureClass.UNSUPPORTED_OPERATION: ExecutionOutcome.UNSUPPORTED,
    CanonicalFailureClass.UNKNOWN_FATAL: ExecutionOutcome.FATAL_FAILURE,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_capability(
    registry: CapabilityRegistry,
    capability_id: str,
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> ExecutionResult:
    """Execute *func* inside the capability execution boundary.

    Parameters
    ----------
    registry:
        The active :class:`~core.registry.CapabilityRegistry`.  Health
        updates are recorded against *capability_id*.
    capability_id:
        The registered capability whose health tracker will be updated.
    func, *args, **kwargs:
        The callable and its arguments, forwarded verbatim.

    Returns
    -------
    ExecutionResult
        Always.  Exceptions are caught, classified, and returned as failure
        results — they never escape this function.

    Raises
    ------
    CapabilityNotFoundError
        Only if *capability_id* is not registered.  This is a programming
        error (the caller asked to execute an unknown capability), not a
        runtime failure, so it is *not* caught.
    """
    # Validate the capability exists before executing.  This is a
    # programming-error check, not a runtime failure — let it propagate.
    if not registry.contains(capability_id):
        raise CapabilityNotFoundError(
            f"Cannot execute unregistered capability: {capability_id!r}"
        )

    # Delegate execution to the normalized boundary.
    result = execute_boundary(func, *args, **kwargs)

    # Update the health tracker based on the outcome.
    if result.outcome == ExecutionOutcome.SUCCESS:
        registry.update_health(capability_id, success=True)
    elif result.failure_class is not None:
        try:
            registry.update_health(
                capability_id,
                success=False,
                failure_class=result.failure_class,
            )
        except Exception:
            # If health update itself fails (e.g. capability was unregistered
            # between our check and now), swallow it — we still want to
            # return the classified result, not crash.
            pass

    # Attach the capability_id to the result for traceability.
    result = ExecutionResult(
        outcome=result.outcome,
        capability_id=capability_id,
        data=result.data,
        partial_data=result.partial_data,
        failure_class=result.failure_class,
        diagnostics=result.diagnostics,
        elapsed_ms=result.elapsed_ms,
    )

    return result
