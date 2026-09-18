"""Application-owned capability registration.

Capability registration is a lifecycle concern, not a request-planning concern.
This module adapts the existing provider/tool configuration surfaces to the
registry bootstrap function and is called by the Brain application's lifespan.
The planner only receives the resulting registry and performs side-effect-free
queries against it.
"""
from __future__ import annotations

from typing import Any, Iterable

from core.capability_bootstrap import bootstrap_registry
from core.registry import CapabilityRegistry


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


def bootstrap_application_capabilities(
    registry: CapabilityRegistry,
    provider_manager: Any,
    *,
    available_tools: Iterable[dict[str, Any]] = (),
    device_tools: Iterable[dict[str, Any]] = (),
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
    return bootstrap_registry(
        registry,
        providers_config=_provider_registration_payload(provider_manager),
        tools_list=device_specs,
        brain_tool_names=brain_names,
    )


__all__ = ["bootstrap_application_capabilities"]
