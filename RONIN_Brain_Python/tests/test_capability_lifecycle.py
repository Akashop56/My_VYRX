"""Phase 8 tests for lifecycle-owned registration and health-aware selection."""
from __future__ import annotations

import inspect
import threading
import unittest
from types import SimpleNamespace

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    SemanticCapabilityType,
)
from core.capability_lifecycle import bootstrap_application_capabilities
from core.planner import (
    build_capability_plan,
    identify_required_capabilities,
    query_capability_candidates,
)
from core.registry import CapabilityRegistry


def descriptor(
    capability_id: str,
    capability_type: SemanticCapabilityType,
    *,
    requires_internet: bool = False,
    is_local: bool = True,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=capability_id,
        capability_type=capability_type,
        description=f"Implementation {capability_id}",
        requires_internet=requires_internet,
        is_local=is_local,
    )


class CapabilityLifecycleTests(unittest.TestCase):
    def test_registration_is_owned_by_application_lifecycle_not_planner(self):
        import core.planner as planner
        import main

        planner_source = inspect.getsource(planner)
        self.assertNotIn("bootstrap_registry", planner_source)
        self.assertNotIn("register_if_absent", planner_source)
        self.assertIn("bootstrap_application_capabilities(", inspect.getsource(main.lifespan))

    def test_lifecycle_registration_is_idempotent_and_preserves_health(self):
        registry = CapabilityRegistry()
        provider_manager = SimpleNamespace(list_providers=lambda: [
            {"name": "groq", "model": "test-model"},
        ])
        tools = [{
            "function": {
                "name": "read_file",
                "description": "Read a local file",
            },
        }]

        first_count = bootstrap_application_capabilities(
            registry,
            provider_manager,
            available_tools=tools,
        )
        registry.update_health("provider-groq", success=True)
        registry.update_health("brain-read_file", success=False,
                              failure_class=CanonicalFailureClass.DETERMINISTIC_ERROR)
        before_ids = set(registry.registered_ids())

        second_count = bootstrap_application_capabilities(
            registry,
            provider_manager,
            available_tools=tools,
        )

        self.assertEqual(first_count, second_count)
        self.assertEqual(set(registry.registered_ids()), before_ids)
        self.assertEqual(registry.get("provider-groq").health, CapabilityHealth.AVAILABLE)
        self.assertEqual(registry.get("brain-read_file").health, CapabilityHealth.DEGRADED)

    def test_concurrent_lifecycle_bootstrap_is_atomic_and_idempotent(self):
        registry = CapabilityRegistry()
        provider_manager = SimpleNamespace(list_providers=lambda: [
            {"name": "groq", "model": "test-model"},
        ])

        threads = [threading.Thread(
            target=bootstrap_application_capabilities,
            args=(registry, provider_manager),
        ) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(registry.registered_ids(), ["provider-groq"])
        self.assertEqual(registry.get("provider-groq").health, CapabilityHealth.UNKNOWN)

    def test_repeated_planning_only_reads_registry_and_preserves_health(self):
        registry = CapabilityRegistry()
        registry.register(descriptor("reasoning-a", SemanticCapabilityType.REASONING))
        registry.update_health("reasoning-a", success=True)
        size_before = registry.size

        first = build_capability_plan("hello", registry)
        second = build_capability_plan("hello", registry)

        self.assertEqual(registry.size, size_before)
        self.assertEqual(first.sub_goal("subgoal-reasoning").candidate_ids, ("reasoning-a",))
        self.assertEqual(second.sub_goal("subgoal-reasoning").candidate_ids, ("reasoning-a",))
        self.assertEqual(registry.get("reasoning-a").health, CapabilityHealth.AVAILABLE)

    def test_unknown_is_uncertain_not_selectable_or_healthy(self):
        registry = CapabilityRegistry()
        registry.register(descriptor("reasoning-unknown", SemanticCapabilityType.REASONING))
        requirement = next(iter(identify_required_capabilities("hello")))

        selected = query_capability_candidates(registry, requirement)
        inspected = query_capability_candidates(registry, requirement, include_unknown=True)
        plan = build_capability_plan("hello", registry)
        sub_goal = plan.sub_goal("subgoal-reasoning")

        self.assertEqual(selected, ())
        self.assertEqual([item.id for item in inspected], ["reasoning-unknown"])
        self.assertEqual(sub_goal.health_state, CapabilityHealth.UNKNOWN)
        self.assertEqual(sub_goal.unknown_candidate_ids, ("reasoning-unknown",))
        self.assertIsNone(sub_goal.selected_capability_id)

    def test_available_selectable_unavailable_excluded_degraded_distinguishable(self):
        registry = CapabilityRegistry()
        registry.register(descriptor("available", SemanticCapabilityType.REASONING))
        registry.register(descriptor("degraded", SemanticCapabilityType.REASONING))
        registry.register(descriptor("unavailable", SemanticCapabilityType.REASONING))
        registry.update_health("available", success=True)
        registry.update_health("degraded", success=False,
                              failure_class=CanonicalFailureClass.DETERMINISTIC_ERROR)
        registry.update_health("unavailable", success=False,
                              failure_class=CanonicalFailureClass.AUTH_DENIED)
        requirement = next(iter(identify_required_capabilities("hello")))

        candidates = query_capability_candidates(registry, requirement)
        plan = build_capability_plan("hello", registry)
        sub_goal = plan.sub_goal("subgoal-reasoning")

        self.assertEqual([item.id for item in candidates], ["available", "degraded"])
        self.assertEqual(sub_goal.health_state, CapabilityHealth.AVAILABLE)
        self.assertEqual(registry.get("degraded").health, CapabilityHealth.DEGRADED)
        self.assertNotIn("unavailable", sub_goal.candidate_ids)

    def test_health_change_is_isolated_per_capability_identity(self):
        registry = CapabilityRegistry()
        registry.register(descriptor("implementation-a", SemanticCapabilityType.REASONING))
        registry.register(descriptor("implementation-b", SemanticCapabilityType.REASONING))
        registry.update_health("implementation-a", success=False,
                              failure_class=CanonicalFailureClass.AUTH_DENIED)

        self.assertEqual(registry.get("implementation-a").health, CapabilityHealth.UNAVAILABLE)
        self.assertEqual(registry.get("implementation-b").health, CapabilityHealth.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
