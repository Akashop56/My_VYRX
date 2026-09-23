"""Execution Boundary Normalization — standardized result capture at operation boundaries.

Provides adapters that wrap existing operations (LLM calls, tool execution,
web search, memory ops) and produce :class:`~core.capabilities.ExecutionResult`
objects with canonical failure classification and preserved diagnostics.

These adapters are **pure infrastructure** — they capture results but do **not**
make recovery decisions or update capability health.  That remains the
responsibility of the orchestrator and registry layers.

Design principles:

* Every adapter catches all ``Exception`` subclasses (except
  ``KeyboardInterrupt`` / ``SystemExit``, which are signals, not failures).
* Failure classification uses the canonical
  :func:`~core.capabilities.classify_exception` layer — no ad-hoc string
  matching.
* Raw diagnostics (exception type, message, traceback, HTTP status) are always
  preserved in :class:`~core.capabilities.DiagnosticContext`.
* The original function's return value is available as ``ExecutionResult.data``
  on success, and as ``ExecutionResult.data`` or ``ExecutionResult.partial_data``
  on failure when useful information was produced before the error.
"""
from __future__ import annotations

import json
import time
import traceback
from typing import Any, Callable

from core.capabilities import (
    CanonicalFailureClass,
    DiagnosticContext,
    ExecutionOutcome,
    ExecutionResult,
    classify_exception,
    outcome_for_failure,
)
from core.llm_handler import complete as _complete
from core.tool_registry import execute_tool as _execute_tool


# ---------------------------------------------------------------------------
# Generic boundary
# ---------------------------------------------------------------------------

def execute_boundary(
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> ExecutionResult:
    """Execute any callable and capture the result as a normalized ``ExecutionResult``.

    This is the **generic** boundary normalizer.  It wraps any synchronous
    callable, catches all exceptions (except ``KeyboardInterrupt`` and
    ``SystemExit``), classifies failures using the canonical classification
    layer, and returns a standardized ``ExecutionResult``.

    Parameters
    ----------
    func:
        The callable to invoke.
    *args, **kwargs:
        Positional and keyword arguments forwarded to *func*.

    Returns
    -------
    ExecutionResult
        On success: ``outcome=SUCCESS``, ``data=<return value>``.
        On failure: ``outcome`` mapped from the canonical failure class,
        ``diagnostics`` preserves the raw exception information.
    """
    started = time.monotonic()
    try:
        data = func(*args, **kwargs)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ExecutionResult(
            outcome=ExecutionOutcome.SUCCESS,
            data=data,
            elapsed_ms=elapsed_ms,
        )

    except KeyboardInterrupt:
        raise
    except SystemExit:
        raise

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        tb = traceback.format_exc()
        if tb and tb.strip() == "NoneType: None":
            tb = None

        failure_class, diagnostics = classify_exception(exc, stack_trace=tb)
        outcome = outcome_for_failure(failure_class)

        return ExecutionResult(
            outcome=outcome,
            failure_class=failure_class,
            diagnostics=diagnostics,
            elapsed_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# LLM completion boundary
# ---------------------------------------------------------------------------

def execute_llm_boundary(
    message: str,
    history: list,
    providers: list,
    **kwargs: Any,
) -> ExecutionResult:
    """Execute an LLM ``complete()`` call and normalize the result.

    Wraps :func:`core.llm_handler.complete` with standardized failure
    handling.  ``LLMError`` (and any other exception) is classified via the
    canonical layer — no ad-hoc string matching is performed.

    Parameters
    ----------
    message, history, providers:
        Forwarded verbatim to ``complete()``.
    **kwargs:
        Additional keyword arguments forwarded to ``complete()``
        (``system_prompt``, ``tools``, ``messages``, ``on_provider``,
        ``on_delta``, etc.).

    Returns
    -------
    ExecutionResult
        On success: ``data`` is the raw completion dict.
        On failure: ``diagnostics`` includes the exception type, message,
        and stack trace.
    """
    started = time.monotonic()
    try:
        # A lifecycle-owned local adapter can use this same boundary without
        # changing the normalized ExecutionResult contract.  The private kwarg
        # is consumed here and never reaches a vendor/runtime callable.
        completion_callable = kwargs.pop("_completion_callable", _complete)
        data = completion_callable(message, history, providers, **kwargs)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ExecutionResult(
            outcome=ExecutionOutcome.SUCCESS,
            data=data,
            elapsed_ms=elapsed_ms,
        )

    except KeyboardInterrupt:
        raise
    except SystemExit:
        raise

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        tb = traceback.format_exc()
        if tb and tb.strip() == "NoneType: None":
            tb = None

        failure_class, diagnostics = classify_exception(exc, stack_trace=tb)
        outcome = outcome_for_failure(failure_class)

        return ExecutionResult(
            outcome=outcome,
            failure_class=failure_class,
            diagnostics=diagnostics,
            elapsed_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# Tool execution boundary
# ---------------------------------------------------------------------------

def _classify_tool_error(error_message: str) -> tuple[CanonicalFailureClass, DiagnosticContext]:
    """Classify a tool error message returned as JSON (without an exception).

    ``tool_registry.execute_tool()`` swallows exceptions and returns
    ``{"error": "..."}`` instead.  This helper classifies those error strings
    using the canonical layer, with targeted overrides for tool-specific
    patterns that the generic message heuristics would mis-classify.
    """
    msg_lower = error_message.casefold()

    # Tool-not-found is an unsupported operation, not a generic "not found".
    if "tool not found" in msg_lower:
        return classify_exception(
            NotImplementedError(error_message),
            extra_details={"source": "tool_registry"},
        )

    # Argument validation errors.
    if "tool arguments must be" in msg_lower:
        return classify_exception(
            TypeError(error_message),
            extra_details={"source": "tool_registry"},
        )

    # Generic tool execution failure — delegate to the canonical layer.
    return classify_exception(
        RuntimeError(error_message),
        extra_details={"source": "tool_registry"},
    )


def execute_tool_boundary(
    tool_name: str,
    arguments: dict[str, Any] | str,
    *,
    context: Any | None = None,
    executor: Callable[[str, dict[str, Any] | str], Any] | None = None,
) -> ExecutionResult:
    """Execute a brain tool and normalize the result.

    Wraps :func:`core.tool_registry.execute_tool` with standardized handling.

    ``execute_tool()`` **never raises** — it swallows all exceptions and
    returns ``{"error": "..."}`` JSON on failure.  This boundary detects that
    pattern and classifies it using the canonical failure layer so the result
    carries the same structured semantics as all other boundaries.

    Parameters
    ----------
    tool_name:
        The registered tool function name (e.g. ``"search"``, ``"read_file"``).
    arguments:
        Tool arguments as a dict (or JSON string, handled by ``execute_tool``).
    context:
        Optional execution context. The local knowledge tool uses its
        lifecycle-owned ``knowledge_engine`` from this object.
    executor:
        Optional context-bound callable. When supplied it is invoked instead of
        the global tool registry while the same normalization path is used.

    Returns
    -------
    ExecutionResult
        On success (no ``"error"`` key in result): ``outcome=SUCCESS``,
        ``data=<JSON result string>``.
        On tool-level error (``"error"`` key present): ``outcome`` mapped from
        the classified failure, ``data`` preserves the raw JSON for the caller,
        ``partial_data`` is ``None`` (tools either succeed or fail — there is
        no partial-data path in the current tool registry).
        On unexpected exception (defensive): classified failure with stack trace.
    """
    if executor is None and context is not None and tool_name == "search_local_knowledge":
        # Keep the boundary generic for the existing registry while allowing
        # the one context-bound local tool to use the lifecycle-owned engine.
        # The import is local so execution_boundary remains independent of the
        # planner and avoids an integration import cycle at module load time.
        from core.knowledge.integration import execute_knowledge_search_with_metadata
        executor = lambda name, payload: execute_knowledge_search_with_metadata(
            getattr(context, "knowledge_engine", None), payload,
        )

    started = time.monotonic()
    try:
        raw_result = (
            executor(tool_name, arguments)
            if executor is not None
            else _execute_tool(tool_name, arguments)
        )
        # Context-bound tools may return a safe text/metadata envelope. Keep
        # metadata on the normalized result instead of serializing it into the
        # model-visible tool text.
        result_metadata: dict[str, Any] = {}
        if hasattr(raw_result, "text") and hasattr(raw_result, "metadata"):
            result_text = str(raw_result.text)
            value = raw_result.metadata
            result_metadata = dict(value) if isinstance(value, dict) else {}
        else:
            result_text = raw_result
        elapsed_ms = int((time.monotonic() - started) * 1000)

        # Tools may return {"error": "..."} without raising.
        if isinstance(result_text, str) and '"error"' in result_text[:160]:
            try:
                parsed = json.loads(result_text)
                error_msg = parsed.get("error", result_text)
            except (json.JSONDecodeError, AttributeError, TypeError):
                error_msg = result_text

            failure_class, diagnostics = _classify_tool_error(str(error_msg))
            outcome = outcome_for_failure(failure_class)

            return ExecutionResult(
                outcome=outcome,
                data=result_text,        # Preserve raw result for caller
                failure_class=failure_class,
                diagnostics=diagnostics,
                metadata=result_metadata,
                elapsed_ms=elapsed_ms,
            )

        return ExecutionResult(
            outcome=ExecutionOutcome.SUCCESS,
            data=result_text,
            metadata=result_metadata,
            elapsed_ms=elapsed_ms,
        )

    except KeyboardInterrupt:
        raise
    except SystemExit:
        raise

    except Exception as exc:
        # Defensive: execute_tool shouldn't raise, but protect against
        # future changes to the tool registry.
        elapsed_ms = int((time.monotonic() - started) * 1000)
        tb = traceback.format_exc()
        if tb and tb.strip() == "NoneType: None":
            tb = None

        failure_class, diagnostics = classify_exception(exc, stack_trace=tb)
        outcome = outcome_for_failure(failure_class)

        return ExecutionResult(
            outcome=outcome,
            failure_class=failure_class,
            diagnostics=diagnostics,
            elapsed_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# Web search boundary
# ---------------------------------------------------------------------------

def execute_search_boundary(query: str, **kwargs: Any) -> ExecutionResult:
    """Execute a web search and normalize the result.

    Wraps :func:`tools.web_search.search` with standardized failure handling.
    HTTP status codes from ``requests.HTTPError`` are passed to the classifier
    for authoritative classification (e.g. 429 → TRANSIENT, 403 → AUTH_DENIED).

    Parameters
    ----------
    query:
        The search query string.
    **kwargs:
        Additional keyword arguments forwarded to ``search()``
        (e.g. ``limit``).

    Returns
    -------
    ExecutionResult
        On success: ``data`` is the search results dict.
        On failure: classified failure with diagnostics.
    """
    from tools.web_search import search as _search

    started = time.monotonic()
    try:
        data = _search(query, **kwargs)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ExecutionResult(
            outcome=ExecutionOutcome.SUCCESS,
            data=data,
            elapsed_ms=elapsed_ms,
        )

    except KeyboardInterrupt:
        raise
    except SystemExit:
        raise

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        tb = traceback.format_exc()
        if tb and tb.strip() == "NoneType: None":
            tb = None

        # Extract HTTP status from requests.HTTPError for authoritative
        # classification (the generic layer falls back to message heuristics).
        http_status: int | None = None
        try:
            import requests as _requests
            if isinstance(exc, _requests.HTTPError) and exc.response is not None:
                http_status = exc.response.status_code
        except (ImportError, AttributeError):
            pass

        failure_class, diagnostics = classify_exception(
            exc,
            http_status=http_status,
            stack_trace=tb,
        )
        outcome = outcome_for_failure(failure_class)

        return ExecutionResult(
            outcome=outcome,
            failure_class=failure_class,
            diagnostics=diagnostics,
            elapsed_ms=elapsed_ms,
        )
