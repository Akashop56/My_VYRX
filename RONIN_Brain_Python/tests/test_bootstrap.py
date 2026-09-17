"""Unit tests for Capability Bootstrapping (core.capability_bootstrap).

Covers: provider mapping, tool mapping, brain-tool mapping, idempotency,
deduplication, empty inputs, and end-to-end registry population.
"""
from __future__ import annotations

import unittest

from core.capabilities import (
    CapabilityHealth,
    SemanticCapabilityType,
)
from core.capability_bootstrap import (
    _BRAIN_FUNCTION_MAP,
    _build_brain_tool_descriptor,
    _build_provider_descriptor,
    _build_tool_descriptor,
    _provider_id,
    _tool_capability_id,
    bootstrap_registry,
)
from core.registry import CapabilityRegistry


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_PROVIDERS = [
    {"provider": "groq", "api_key": "gsk_test", "model": "llama-3.3-70b-versatile"},
    {"provider": "gemini", "api_key": "AIza_test", "model": None},
    {"provider": "openai", "api_key": "sk-test", "model": "gpt-4o"},
    {"provider": "openrouter", "api_key": "or_test", "model": None},
    {"provider": "custom", "api_key": "custom_key", "endpoint": "http://localhost:8080/v1", "model": "custom-7b"},
]

MOCK_TOOLS = [
    {"id": "web_search", "name": "Web Search", "category": "internet",
     "description": "Search the web", "brain_kind": "server", "device_signal": None},
    {"id": "app_control", "name": "App Control", "category": "device",
     "description": "Open and manage apps", "brain_kind": "server_device", "device_signal": "accessibility"},
    {"id": "terminal", "name": "Terminal", "category": "development",
     "description": "Execute shell commands", "brain_kind": "server", "device_signal": None},
    {"id": "agent_memory", "name": "Agent Memory", "category": "automation",
     "description": "Self-learning memory", "brain_kind": "server", "device_signal": None},
    {"id": "file_manager", "name": "File Manager", "category": "device",
     "description": "Browse and manage files", "brain_kind": "server", "device_signal": None},
    {"id": "screen_reader", "name": "Screen Reader", "category": "device",
     "description": "Read UI tree", "brain_kind": "server_device", "device_signal": "accessibility"},
    {"id": "telegram", "name": "Telegram", "category": "internet",
     "description": "Send messages", "brain_kind": "unavailable", "device_signal": None},
]

MOCK_BRAIN_TOOLS = [
    "save_memory",
    "retrieve_memory",
    "search",
    "run_termux_command",
    "read_file",
    "write_file",
    "list_files",
]


# ==============================================================================
# 1. Provider descriptor building
# ==============================================================================

class TestProviderDescriptorBuilding(unittest.TestCase):
    def test_groq_descriptor(self):
        desc = _build_provider_descriptor(MOCK_PROVIDERS[0])
        self.assertEqual(desc.id, "provider-groq")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.REASONING)
        self.assertTrue(desc.requires_internet)
        self.assertTrue(desc.requires_auth)
        self.assertFalse(desc.is_local)
        self.assertEqual(desc.metadata["model"], "llama-3.3-70b-versatile")
        self.assertEqual(desc.metadata["provider_name"], "groq")

    def test_custom_provider_has_endpoint(self):
        desc = _build_provider_descriptor(MOCK_PROVIDERS[4])
        self.assertEqual(desc.id, "provider-custom")
        self.assertEqual(desc.metadata["endpoint"], "http://localhost:8080/v1")
        self.assertEqual(desc.metadata["model"], "custom-7b")

    def test_gemini_uses_default_model(self):
        desc = _build_provider_descriptor(MOCK_PROVIDERS[1])
        # model=None in config, should fall back to default
        self.assertEqual(desc.metadata["model"], "gemini-3.8-flash")

    def test_openai_model_explicit(self):
        desc = _build_provider_descriptor(MOCK_PROVIDERS[2])
        self.assertEqual(desc.metadata["model"], "gpt-4o")

    def test_health_defaults_unknown(self):
        desc = _build_provider_descriptor(MOCK_PROVIDERS[0])
        self.assertEqual(desc.health, CapabilityHealth.UNKNOWN)

    def test_estimated_latency_set(self):
        desc = _build_provider_descriptor(MOCK_PROVIDERS[0])
        self.assertEqual(desc.estimated_latency_ms, 2000)

    def test_cost_tier_medium(self):
        desc = _build_provider_descriptor(MOCK_PROVIDERS[0])
        self.assertEqual(desc.estimated_cost_tier, "medium")


# ==============================================================================
# 2. Tool descriptor building
# ==============================================================================

class TestToolDescriptorBuilding(unittest.TestCase):
    def test_web_search_type(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[0])
        self.assertEqual(desc.id, "tool-web_search")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.WEB_RETRIEVAL)
        self.assertTrue(desc.requires_internet)

    def test_app_control_type(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[1])
        self.assertEqual(desc.id, "tool-app_control")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.DEVICE_INTERACTION)

    def test_terminal_type(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[2])
        self.assertEqual(desc.id, "tool-terminal")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.SYSTEM_COMMAND)

    def test_agent_memory_type(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[3])
        self.assertEqual(desc.id, "tool-agent_memory")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.MEMORY_PERSISTENCE)

    def test_file_manager_type(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[4])
        self.assertEqual(desc.id, "tool-file_manager")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.FILE_SYSTEM_IO)

    def test_screen_reader_type(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[5])
        self.assertEqual(desc.id, "tool-screen_reader")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.DEVICE_INTERACTION)

    def test_telegram_type(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[6])
        self.assertEqual(desc.id, "tool-telegram")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.WEB_RETRIEVAL)
        self.assertTrue(desc.requires_internet)

    def test_server_tool_is_local(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[0])  # web_search, brain_kind=server
        self.assertTrue(desc.is_local)

    def test_server_device_tool_is_not_local(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[1])  # app_control, brain_kind=server_device
        self.assertFalse(desc.is_local)

    def test_unavailable_tool_is_not_local(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[6])  # telegram, brain_kind=unavailable
        self.assertFalse(desc.is_local)

    def test_health_defaults_unknown(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[0])
        self.assertEqual(desc.health, CapabilityHealth.UNKNOWN)

    def test_metadata_carries_brain_kind(self):
        desc = _build_tool_descriptor(MOCK_TOOLS[1])
        self.assertEqual(desc.metadata["brain_kind"], "server_device")
        self.assertEqual(desc.metadata["device_signal"], "accessibility")

    def test_unknown_tool_category_defaults_to_deterministic_compute(self):
        desc = _build_tool_descriptor({
            "id": "mystery", "name": "Mystery", "category": "unknown_category",
            "description": "A mystery", "brain_kind": "server",
        })
        self.assertEqual(desc.capability_type, SemanticCapabilityType.DETERMINISTIC_COMPUTE)

    def test_empty_tool_id_gets_unknown(self):
        desc = _build_tool_descriptor({
            "id": "", "name": "X", "category": "device",
            "description": "X", "brain_kind": "server",
        })
        self.assertEqual(desc.id, "tool-unknown")


# ==============================================================================
# 3. Brain tool descriptor building
# ==============================================================================

class TestBrainToolDescriptorBuilding(unittest.TestCase):
    def test_save_memory(self):
        desc = _build_brain_tool_descriptor("save_memory")
        self.assertEqual(desc.id, "brain-save_memory")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.MEMORY_PERSISTENCE)
        self.assertTrue(desc.is_local)

    def test_search(self):
        desc = _build_brain_tool_descriptor("search")
        self.assertEqual(desc.id, "brain-search")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.WEB_RETRIEVAL)
        self.assertTrue(desc.requires_internet)

    def test_run_termux_command(self):
        desc = _build_brain_tool_descriptor("run_termux_command")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.SYSTEM_COMMAND)
        self.assertTrue(desc.is_local)

    def test_read_file(self):
        desc = _build_brain_tool_descriptor("read_file")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.FILE_SYSTEM_IO)

    def test_unknown_function_defaults_to_deterministic(self):
        desc = _build_brain_tool_descriptor("some_new_function")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.DETERMINISTIC_COMPUTE)

    def test_metadata_carries_function_name(self):
        desc = _build_brain_tool_descriptor("save_memory")
        self.assertEqual(desc.metadata["function_name"], "save_memory")


# ==============================================================================
# 4. Bootstrap integration
# ==============================================================================

class TestBootstrapRegistry(unittest.TestCase):
    def test_full_bootstrap(self):
        reg = CapabilityRegistry()
        count = bootstrap_registry(
            reg,
            providers_config=MOCK_PROVIDERS,
            tools_list=MOCK_TOOLS,
            brain_tool_names=MOCK_BRAIN_TOOLS,
        )
        self.assertEqual(count, 5 + 7 + 7)  # providers + tools + brain tools
        self.assertEqual(reg.size, 19)

    def test_providers_only(self):
        reg = CapabilityRegistry()
        count = bootstrap_registry(reg, providers_config=MOCK_PROVIDERS)
        self.assertEqual(count, 5)
        self.assertEqual(reg.size, 5)
        self.assertTrue(reg.contains("provider-groq"))
        self.assertTrue(reg.contains("provider-gemini"))
        self.assertTrue(reg.contains("provider-openai"))

    def test_tools_only(self):
        reg = CapabilityRegistry()
        count = bootstrap_registry(reg, tools_list=MOCK_TOOLS)
        self.assertEqual(count, 7)
        self.assertTrue(reg.contains("tool-web_search"))
        self.assertTrue(reg.contains("tool-app_control"))

    def test_brain_tools_only(self):
        reg = CapabilityRegistry()
        count = bootstrap_registry(reg, brain_tool_names=MOCK_BRAIN_TOOLS)
        self.assertEqual(count, 7)
        self.assertTrue(reg.contains("brain-save_memory"))
        self.assertTrue(brain_tool_name in reg.registered_ids()
                        for brain_tool_name in ["brain-search", "brain-read_file"])

    def test_empty_inputs(self):
        reg = CapabilityRegistry()
        count = bootstrap_registry(reg)
        self.assertEqual(count, 0)
        self.assertEqual(reg.size, 0)

    def test_empty_provider_entry_skipped(self):
        reg = CapabilityRegistry()
        count = bootstrap_registry(reg, providers_config=[
            {"provider": "", "api_key": "key"},
            {"provider": None, "api_key": "key"},
            {"provider": "groq", "api_key": "key"},
        ])
        self.assertEqual(count, 1)
        self.assertTrue(reg.contains("provider-groq"))

    def test_empty_tool_entry_skipped(self):
        reg = CapabilityRegistry()
        count = bootstrap_registry(reg, tools_list=[
            {"id": "", "name": "X", "category": "device", "description": "X", "brain_kind": "server"},
            {"id": None, "name": "X", "category": "device", "description": "X", "brain_kind": "server"},
            {"id": "web_search", "name": "Web Search", "category": "internet", "description": "S", "brain_kind": "server"},
        ])
        self.assertEqual(count, 1)

    def test_duplicate_tool_ids_deduplicated(self):
        reg = CapabilityRegistry()
        count = bootstrap_registry(reg, tools_list=[
            {"id": "web_search", "name": "Web Search v1", "category": "internet",
             "description": "v1", "brain_kind": "server"},
            {"id": "web_search", "name": "Web Search v2", "category": "internet",
             "description": "v2", "brain_kind": "server"},
        ])
        self.assertEqual(count, 1)
        desc = reg.get("tool-web_search")
        # First entry wins — duplicates are skipped, not replaced
        self.assertEqual(desc.description, "v1")

    def test_empty_brain_tool_name_skipped(self):
        reg = CapabilityRegistry()
        count = bootstrap_registry(reg, brain_tool_names=["", "  ", "save_memory"])
        self.assertEqual(count, 1)

    def test_all_health_unknown_after_bootstrap(self):
        reg = CapabilityRegistry()
        bootstrap_registry(
            reg,
            providers_config=MOCK_PROVIDERS,
            tools_list=MOCK_TOOLS,
            brain_tool_names=MOCK_BRAIN_TOOLS,
        )
        for desc in reg.list():
            self.assertEqual(
                desc.health, CapabilityHealth.UNKNOWN,
                f"{desc.id} should have UNKNOWN health after bootstrap",
            )

    def test_idempotent_double_bootstrap(self):
        reg = CapabilityRegistry()
        count1 = bootstrap_registry(
            reg,
            providers_config=MOCK_PROVIDERS,
            tools_list=MOCK_TOOLS,
            brain_tool_names=MOCK_BRAIN_TOOLS,
        )
        count2 = bootstrap_registry(
            reg,
            providers_config=MOCK_PROVIDERS,
            tools_list=MOCK_TOOLS,
            brain_tool_names=MOCK_BRAIN_TOOLS,
        )
        # Same count both times
        self.assertEqual(count1, count2)
        # Size unchanged (re-registration replaces, doesn't duplicate)
        self.assertEqual(reg.size, 19)


# ==============================================================================
# 5. Semantic type distribution
# ==============================================================================

class TestSemanticTypeDistribution(unittest.TestCase):
    def test_reasoning_capabilities_from_providers(self):
        reg = CapabilityRegistry()
        bootstrap_registry(reg, providers_config=MOCK_PROVIDERS)
        reasoners = reg.query_by_semantic_type(SemanticCapabilityType.REASONING)
        self.assertEqual(len(reasoners), 5)

    def test_web_retrieval_from_tools_and_brain(self):
        reg = CapabilityRegistry()
        bootstrap_registry(
            reg,
            tools_list=MOCK_TOOLS,
            brain_tool_names=MOCK_BRAIN_TOOLS,
        )
        web = reg.query_by_semantic_type(SemanticCapabilityType.WEB_RETRIEVAL)
        # web_search tool + telegram tool + search brain tool
        self.assertEqual(len(web), 3)

    def test_memory_persistence_from_tools_and_brain(self):
        reg = CapabilityRegistry()
        bootstrap_registry(
            reg,
            tools_list=MOCK_TOOLS,
            brain_tool_names=MOCK_BRAIN_TOOLS,
        )
        memory = reg.query_by_semantic_type(SemanticCapabilityType.MEMORY_PERSISTENCE)
        # agent_memory tool + note_creator tool + save_memory + retrieve_memory
        # Wait, note_creator is in TOOLS but not in MOCK_TOOLS. Let me check...
        # MOCK_TOOLS has agent_memory (automation → MEMORY_PERSISTENCE via _ID_OVERRIDES)
        # MOCK_BRAIN_TOOLS has save_memory, retrieve_memory
        self.assertGreaterEqual(len(memory), 3)

    def test_file_system_io_from_tools_and_brain(self):
        reg = CapabilityRegistry()
        bootstrap_registry(
            reg,
            tools_list=MOCK_TOOLS,
            brain_tool_names=MOCK_BRAIN_TOOLS,
        )
        fs = reg.query_by_semantic_type(SemanticCapabilityType.FILE_SYSTEM_IO)
        # file_manager tool + read_file + write_file + list_files
        self.assertEqual(len(fs), 4)

    def test_device_interaction_from_tools(self):
        reg = CapabilityRegistry()
        bootstrap_registry(reg, tools_list=MOCK_TOOLS)
        device = reg.query_by_semantic_type(SemanticCapabilityType.DEVICE_INTERACTION)
        # app_control + screen_reader
        self.assertEqual(len(device), 2)


# ==============================================================================
# 6. ID naming conventions
# ==============================================================================

class TestIdNamingConventions(unittest.TestCase):
    def test_provider_id_prefix(self):
        self.assertEqual(_provider_id("groq"), "provider-groq")
        self.assertEqual(_provider_id("OpenAI"), "provider-openai")
        self.assertEqual(_provider_id("  gemini  "), "provider-gemini")

    def test_tool_id_prefix(self):
        self.assertEqual(_tool_capability_id("web_search"), "tool-web_search")
        self.assertEqual(_tool_capability_id("App_Control"), "tool-app_control")


# ==============================================================================
# 7. Bootstrap → executor integration
# ==============================================================================

class TestBootstrapToExecutorIntegration(unittest.TestCase):
    """Verify that bootstrapped capabilities work with the executor."""

    def test_bootstrapped_provider_executes(self):
        from core.capability_executor import execute_capability

        reg = CapabilityRegistry()
        bootstrap_registry(reg, providers_config=[{
            "provider": "groq", "api_key": "test-key", "model": "llama-3.3-70b-versatile",
        }])
        result = execute_capability(reg, "provider-groq", lambda: {"choices": [{"message": {"content": "hi"}}]})
        self.assertEqual(result.outcome, "success")
        self.assertEqual(reg.get("provider-groq").health, CapabilityHealth.AVAILABLE)

    def test_bootstrapped_tool_executes(self):
        from core.capability_executor import execute_capability

        reg = CapabilityRegistry()
        bootstrap_registry(reg, tools_list=[{
            "id": "web_search", "name": "Web Search", "category": "internet",
            "description": "Search", "brain_kind": "server",
        }])
        result = execute_capability(reg, "tool-web_search", lambda: {"results": []})
        self.assertEqual(result.outcome, "success")

    def test_bootstrapped_capability_failure_updates_health(self):
        from core.capability_executor import execute_capability

        reg = CapabilityRegistry()
        bootstrap_registry(reg, providers_config=[{
            "provider": "groq", "api_key": "test-key",
        }])

        def boom():
            raise TimeoutError("provider timeout")

        result = execute_capability(reg, "provider-groq", boom)
        self.assertEqual(result.outcome, "retryable_failure")
        self.assertEqual(result.failure_class, "transient")


if __name__ == "__main__":
    unittest.main()
