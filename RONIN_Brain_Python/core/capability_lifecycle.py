"""Application-owned capability registration.

Capability registration is a lifecycle concern, not a request-planning concern.
This module adapts the existing provider/tool configuration surfaces to the
registry bootstrap function and is called by the Brain application's lifespan.
The planner only receives the resulting registry and performs side-effect-free
queries against it.
"""
from __future__ import annotations

from typing import Any, Iterable

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    SemanticCapabilityType,
)
from core.capability_bootstrap import bootstrap_registry
from core.knowledge.integration import establish_knowledge_health
from core.local_reasoning import (
    LOCAL_REASONING_CAPABILITY_ID,
    LocalReasoningAdapter,
    LocalReasoningReadiness,
)
from core.registry import CapabilityRegistry


_KNOWLEDGE_ENGINE_UNSET = object()
_LOCAL_REASONING_UNSET = object()


def _provider_registration_payload(provider_manager: Any) -> list[dict[str, Any]]:
    """Return provider descriptors known to the application at startup.

    API keys deliberately do not cross this boundary.  The Android request
    still supplies credentials to the existing LLM transport; registration
    only establishes stable capability identities and metadata in the active
    registry.
    """
    providers = provider_manager.list_providers()
    return [
        {
            "provider": str(provider.get("name") or "").strip(),
            "model": provider.get("model"),
            # ProviderManager's public snapshot intentionally does not expose
            # secrets or mutable transport state.  An endpoint is optional.
            "endpoint": provider.get("endpoint"),
        }
        for provider in providers
        if str(provider.get("name") or "").strip()
    ]


def _tool_registration_payload(
    available_tools: Iterable[dict[str, Any]],
    device_tools: Iterable[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Translate the complete application tool surface to bootstrap inputs."""
    brain_names: list[str] = []
    for tool in available_tools:
        name = str(tool.get("function", {}).get("name") or "").strip()
        if name:
            brain_names.append(name)

    device_specs: list[dict[str, Any]] = []
    for tool in device_tools:
        function = tool.get("function", {})
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        device_specs.append({
            "id": f"device-{name}",
            "name": name,
            "category": "device",
            "description": function.get("description") or "Device interaction",
            "brain_kind": "server_device",
        })
    return brain_names, device_specs


def local_reasoning_descriptor(
    adapter: LocalReasoningAdapter | None,
    readiness: LocalReasoningReadiness | None = None,
) -> CapabilityDescriptor:
    """Build the lifecycle-owned local REASONING descriptor."""
    readiness = readiness or (
        adapter.readiness if adapter is not None else LocalReasoningReadiness(
            health=CapabilityHealth.UNAVAILABLE,
            reason="Local reasoning adapter was not created",
            failure_class=CanonicalFailureClass.UNSUPPORTED_OPERATION,
        )
    )
    return CapabilityDescriptor(
        id=LOCAL_REASONING_CAPABILITY_ID,
        capability_type=SemanticCapabilityType.REASONING,
        description="Provider-agnostic local process reasoning runtime",
        requires_internet=False,
        requires_auth=False,
        is_local=True,
        estimated_latency_ms=1000,
        estimated_cost_tier="free",
        metadata={
            "adapter": "LocalReasoningAdapter",
            "runtime_protocol": "stdin_json_stdout_completion",
            "runtime_command": readiness.runtime_command,
            "readiness": readiness.health.value,
            "readiness_reason": readiness.reason,
            "max_context_chars": adapter.max_context_chars if adapter is not None else None,
            "max_output_chars": adapter.max_output_chars if adapter is not None else None,
            "timeout_seconds": adapter.timeout_seconds if adapter is not None else None,
            "concurrency": "serialized",
            "process_lifecycle": "per_request",
            "model_loading": "runtime-owned; no automatic download",
        },
    )


def establish_local_reasoning(
    registry: CapabilityRegistry,
    adapter: LocalReasoningAdapter | None,
) -> CapabilityHealth:
    """Register local reasoning and establish health from honest readiness."""
    readiness = adapter.initialize() if adapter is not None else None
    descriptor = local_reasoning_descriptor(adapter, readiness)
    registry.register_if_absent(descriptor)
    if readiness is not None and readiness.ready:
        registry.update_health(LOCAL_REASONING_CAPABILITY_ID, success=True)
    else:
        failure_class = (
            readiness.failure_class if readiness is not None and readiness.failure_class is not None
            else CanonicalFailureClass.UNKNOWN_FATAL
        )
        registry.update_health(
            LOCAL_REASONING_CAPABILITY_ID,
            success=False,
            failure_class=failure_class,
        )
    current = registry.get(LOCAL_REASONING_CAPABILITY_ID)
    return current.health if current is not None else CapabilityHealth.UNAVAILABLE


def bootstrap_application_capabilities(
    registry: CapabilityRegistry,
    provider_manager: Any,
    *,
    available_tools: Iterable[dict[str, Any]] = (),
    device_tools: Iterable[dict[str, Any]] = (),
    knowledge_engine: Any = _KNOWLEDGE_ENGINE_UNSET,
    local_reasoning: Any = _LOCAL_REASONING_UNSET,
) -> int:
    """Register the application's known capabilities during its lifecycle.

    The operation is safe to call more than once (for example, if an app
    lifespan is recreated): bootstrap uses the registry's atomic
    ``register_if_absent`` operation, so existing descriptors and health
    trackers are retained rather than reset.

    Returns the number of descriptor inputs processed, matching
    :func:`core.capability_bootstrap.bootstrap_registry`.
    """
    brain_names, device_specs = _tool_registration_payload(
        available_tools,
        device_tools,
    )
    count = bootstrap_registry(
        registry,
        providers_config=_provider_registration_payload(provider_manager),
        tools_list=device_specs,
        brain_tool_names=brain_names,
    )
    # The local knowledge descriptor is lifecycle-owned as well, but its
    # availability is established from the already-created engine.  The
    # planner only reads this result and never constructs the engine.  The
    # sentinel keeps this helper backward-compatible for callers that only
    # bootstrap the pre-Phase-10 capability set.
    if knowledge_engine is not _KNOWLEDGE_ENGINE_UNSET:
        establish_knowledge_health(registry, knowledge_engine)
        count += 1
    if local_reasoning is not _LOCAL_REASONING_UNSET:
        establish_local_reasoning(registry, local_reasoning)
        count += 1
    return count


__all__ = [
    "LOCAL_REASONING_CAPABILITY_ID",
    "bootstrap_application_capabilities",
    "establish_local_reasoning",
    "local_reasoning_descriptor",
]
