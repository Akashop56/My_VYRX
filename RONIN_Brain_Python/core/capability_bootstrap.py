"""Capability Bootstrapping — populate a registry from existing system config.

Provides :func:`bootstrap_registry`, which translates the existing VYRX
provider and tool configurations into :class:`~core.capabilities.CapabilityDescriptor`
instances and registers them into a :class:`~core.registry.CapabilityRegistry`.

This bridges the gap between the legacy configuration surfaces
(``core.provider_manager``, ``core.tools_catalog``) and the new
Capability-Oriented Architecture without modifying either.

Design decisions
----------------
* **Providers → REASONING capabilities.**  Each configured AI provider
  (Groq, Gemini, OpenAI, OpenRouter, custom) is mapped to a
  ``REASONING`` capability with ``requires_internet=True`` and
  ``requires_auth=True``.
* **Tools → semantic capabilities.**  The existing ``ToolSpec`` catalog
  entries are mapped to the closest :class:`SemanticCapabilityType` based
  on their ``category`` and ``id``.
* **Idempotent.**  Calling ``bootstrap_registry`` twice with the same
  config is safe — re-registration replaces the previous entry.
* **No side effects beyond the registry.**  The function does not modify
  provider state, tool files, or any other module.
"""
from __future__ import annotations

from typing import Any

from core.capabilities import (
    CapabilityDescriptor,
    SemanticCapabilityType,
)
from core.registry import CapabilityRegistry


# ---------------------------------------------------------------------------
# Provider → capability mapping
# ---------------------------------------------------------------------------

# All known VYRX providers are LLM reasoning backends.
_PROVIDER_CAPABILITY_TYPE = SemanticCapabilityType.REASONING

# Default models per provider (mirrors core.provider_manager.DEFAULT_MODELS
# so the descriptor metadata can carry the model name without importing
# the provider manager).
_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "openrouter": "openai/gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-3.8-flash",
}


def _provider_id(name: str) -> str:
    """Canonical capability ID for a provider."""
    return f"provider-{name.lower().strip()}"


def _build_provider_descriptor(provider: dict[str, Any]) -> CapabilityDescriptor:
    """Build a CapabilityDescriptor from a provider config dict.

    The dict shape matches ``ProviderRequest.model_dump()`` — keys:
    ``provider``, ``api_key``, ``endpoint``, ``model``.
    """
    name = str(provider.get("provider") or "unknown").lower().strip()
    model = provider.get("model") or _DEFAULT_MODELS.get(name)
    endpoint = provider.get("endpoint")

    metadata: dict[str, Any] = {"provider_name": name}
    if model:
        metadata["model"] = model
    if endpoint:
        metadata["endpoint"] = endpoint

    return CapabilityDescriptor(
        id=_provider_id(name),
        capability_type=_PROVIDER_CAPABILITY_TYPE,
        description=f"{name.capitalize()} LLM provider"
                    + (f" ({model})" if model else ""),
        requires_internet=True,
        requires_auth=True,
        is_local=False,
        estimated_latency_ms=2000,
        estimated_cost_tier="medium",
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Tool → capability mapping
# ---------------------------------------------------------------------------

# Mapping from tool catalog category to the most appropriate
# SemanticCapabilityType.  Where the category is ambiguous, the tool id
# provides additional disambiguation.
_CATEGORY_TO_CAPABILITY: dict[str, SemanticCapabilityType] = {
    "device": SemanticCapabilityType.DEVICE_INTERACTION,
    "internet": SemanticCapabilityType.WEB_RETRIEVAL,
    "automation": SemanticCapabilityType.SYSTEM_COMMAND,
    "development": SemanticCapabilityType.DETERMINISTIC_COMPUTE,
}

# Tools whose ids give a more specific semantic type than their category.
_ID_OVERRIDES: dict[str, SemanticCapabilityType] = {
    "web_search": SemanticCapabilityType.WEB_RETRIEVAL,
    "agent_memory": SemanticCapabilityType.MEMORY_PERSISTENCE,
    "note_creator": SemanticCapabilityType.MEMORY_PERSISTENCE,
    "file_manager": SemanticCapabilityType.FILE_SYSTEM_IO,
    "terminal": SemanticCapabilityType.SYSTEM_COMMAND,
    "system_monitor": SemanticCapabilityType.DETERMINISTIC_COMPUTE,
    "code_executor": SemanticCapabilityType.DETERMINISTIC_COMPUTE,
    "app_control": SemanticCapabilityType.DEVICE_INTERACTION,
    "screen_reader": SemanticCapabilityType.DEVICE_INTERACTION,
    "notification_manager": SemanticCapabilityType.DEVICE_INTERACTION,
    "telegram": SemanticCapabilityType.WEB_RETRIEVAL,
    "email": SemanticCapabilityType.WEB_RETRIEVAL,
    "custom_api": SemanticCapabilityType.WEB_RETRIEVAL,
    "image_analyzer": SemanticCapabilityType.DETERMINISTIC_COMPUTE,
    "task_automation": SemanticCapabilityType.SYSTEM_COMMAND,
}


def _tool_capability_id(tool_id: str) -> str:
    """Canonical capability ID for a tool."""
    return f"tool-{tool_id.lower().strip()}"


def _build_tool_descriptor(tool: dict[str, Any]) -> CapabilityDescriptor:
    """Build a CapabilityDescriptor from a tool spec dict.

    The dict shape matches what ``core.tools_catalog.ToolSpec`` fields look
    like when exported: ``id``, ``name``, ``category``, ``description``,
    ``brain_kind``, ``device_signal``.
    """
    tool_id = str(tool.get("id") or "unknown").lower().strip()
    name = str(tool.get("name") or tool_id)
    category = str(tool.get("category") or "").lower().strip()
    description = str(tool.get("description") or f"{name} tool")
    brain_kind = str(tool.get("brain_kind") or "unavailable").lower().strip()

    # Determine semantic type: id-specific override → category fallback →
    # DETERMINISTIC_COMPUTE as safe default.
    capability_type = (
        _ID_OVERRIDES.get(tool_id)
        or _CATEGORY_TO_CAPABILITY.get(category)
        or SemanticCapabilityType.DETERMINISTIC_COMPUTE
    )

    # Locality: server-side tools run locally; server_device tools need
    # the Android body; everything else is conservatively local.
    is_local = brain_kind in ("server",)
    requires_internet = capability_type == SemanticCapabilityType.WEB_RETRIEVAL

    metadata: dict[str, Any] = {
        "tool_id": tool_id,
        "brain_kind": brain_kind,
    }
    device_signal = tool.get("device_signal")
    if device_signal:
        metadata["device_signal"] = device_signal

    return CapabilityDescriptor(
        id=_tool_capability_id(tool_id),
        capability_type=capability_type,
        description=description,
        requires_internet=requires_internet,
        requires_auth=False,
        is_local=is_local,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Brain-side tool function schemas → capability mapping
# ---------------------------------------------------------------------------

# Mapping for tool function names discovered by core.tool_registry from the
# tools/ directory.  These don't have ToolSpec entries but are real
# capabilities the agent can invoke.
_BRAIN_FUNCTION_MAP: dict[str, SemanticCapabilityType] = {
    "save_memory": SemanticCapabilityType.MEMORY_PERSISTENCE,
    "retrieve_memory": SemanticCapabilityType.MEMORY_PERSISTENCE,
    "search": SemanticCapabilityType.WEB_RETRIEVAL,
    "run_termux_command": SemanticCapabilityType.SYSTEM_COMMAND,
    "read_file": SemanticCapabilityType.FILE_SYSTEM_IO,
    "write_file": SemanticCapabilityType.FILE_SYSTEM_IO,
    "list_files": SemanticCapabilityType.FILE_SYSTEM_IO,
}


def _build_brain_tool_descriptor(func_name: str) -> CapabilityDescriptor:
    """Build a descriptor for a brain-side tool function."""
    capability_type = (
        _BRAIN_FUNCTION_MAP.get(func_name)
        or SemanticCapabilityType.DETERMINISTIC_COMPUTE
    )
    return CapabilityDescriptor(
        id=f"brain-{func_name}",
        capability_type=capability_type,
        description=f"Brain-side tool: {func_name}",
        requires_internet=(capability_type == SemanticCapabilityType.WEB_RETRIEVAL),
        requires_auth=False,
        is_local=True,
        metadata={"function_name": func_name},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def bootstrap_registry(
    registry: CapabilityRegistry,
    providers_config: list[dict[str, Any]] | None = None,
    tools_list: list[dict[str, Any]] | None = None,
    brain_tool_names: list[str] | None = None,
) -> int:
    """Populate *registry* with capabilities derived from existing config.

    Parameters
    ----------
    registry:
        The :class:`~core.registry.CapabilityRegistry` to populate.
    providers_config:
        List of provider config dicts (shape: ``{"provider": "groq",
        "api_key": "...", "model": "...", "endpoint": "..."}``).
        Each entry becomes a ``REASONING`` capability.
    tools_list:
        List of tool spec dicts (shape: ``{"id": "web_search", "name":
        "Web Search", "category": "internet", "description": "...",
        "brain_kind": "server", "device_signal": null}``).
        Each entry becomes a typed capability.
    brain_tool_names:
        List of brain-side function names (e.g. ``["save_memory",
        "search", "run_termux_command"]``).  These are the functions
        discovered by :mod:`core.tool_registry` from the ``tools/``
        directory.

    Returns
    -------
    int
        The total number of capabilities registered.
    """
    count = 0

    for provider in providers_config or []:
        name = str(provider.get("provider") or "").strip()
        if not name:
            continue
        descriptor = _build_provider_descriptor(provider)
        registry.register(descriptor)
        count += 1

    seen_tool_ids: set[str] = set()
    for tool in tools_list or []:
        tool_id = str(tool.get("id") or "").strip()
        if not tool_id or tool_id in seen_tool_ids:
            continue
        seen_tool_ids.add(tool_id)
        descriptor = _build_tool_descriptor(tool)
        registry.register(descriptor)
        count += 1

    for func_name in brain_tool_names or []:
        func_name = func_name.strip()
        if not func_name:
            continue
        descriptor = _build_brain_tool_descriptor(func_name)
        registry.register(descriptor)
        count += 1

    return count
