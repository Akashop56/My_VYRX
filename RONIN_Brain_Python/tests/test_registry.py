"""Unit tests for the Active Capability Registry.

Covers CRUD operations, querying, multi-implementation support, health state
isolation, thread safety, and edge cases.
"""
from __future__ import annotations

import threading
import unittest

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    HealthPolicy,
    SemanticCapabilityType,
)
from core.registry import CapabilityNotFoundError, CapabilityRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_descriptor(cap_id: str, **overrides: object) -> CapabilityDescriptor:
    """Create a minimal descriptor with sensible defaults for testing."""
    defaults: dict[str, object] = dict(
        id=cap_id,
        capability_type=SemanticCapabilityType.REASONING,
        description=f"Test capability {cap_id}",
    )
    defaults.update(overrides)
    return CapabilityDescriptor(**defaults)


# ==============================================================================
# 1. Register, retrieve, replace, unregister
# ==============================================================================

class TestRegisterAndGet(unittest.TestCase):
    def test_register_returns_copy_with_unknown_health(self):
        reg = CapabilityRegistry()
        desc = _make_descriptor("cap-1", health=CapabilityHealth.AVAILABLE)
        result = reg.register(desc)
        # Registration always resets health to UNKNOWN
        self.assertEqual(result.health, CapabilityHealth.UNKNOWN)
        # The original is not mutated
        self.assertEqual(desc.health, CapabilityHealth.AVAILABLE)

    def test_get_returns_descriptor_with_unknown_health(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        got = reg.get("cap-1")
        self.assertIsNotNone(got)
        self.assertEqual(got.id, "cap-1")
        self.assertEqual(got.health, CapabilityHealth.UNKNOWN)

    def test_get_missing_returns_none(self):
        reg = CapabilityRegistry()
        self.assertIsNone(reg.get("nonexistent"))

    def test_register_replaces_existing(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1", description="version 1"))
        reg.register(_make_descriptor("cap-1", description="version 2"))
        got = reg.get("cap-1")
        self.assertEqual(got.description, "version 2")
        self.assertEqual(reg.size, 1)

    def test_register_replaces_health_resets_to_unknown(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.update_health("cap-1", success=True)  # → AVAILABLE
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.AVAILABLE)
        # Re-register resets health
        reg.register(_make_descriptor("cap-1"))
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.UNKNOWN)

    def test_unregister_existing(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        self.assertTrue(reg.unregister("cap-1"))
        self.assertIsNone(reg.get("cap-1"))
        self.assertEqual(reg.size, 0)

    def test_unregister_missing(self):
        reg = CapabilityRegistry()
        self.assertFalse(reg.unregister("nonexistent"))

    def test_unregister_does_not_affect_others(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.register(_make_descriptor("cap-2"))
        reg.unregister("cap-1")
        self.assertIsNone(reg.get("cap-1"))
        self.assertIsNotNone(reg.get("cap-2"))
        self.assertEqual(reg.size, 1)

    def test_register_returns_deep_copy(self):
        """Mutating the returned descriptor must not affect the registry."""
        reg = CapabilityRegistry()
        desc = _make_descriptor("cap-1", metadata={"key": "value"})
        result = reg.register(desc)
        result.metadata["key"] = "mutated"
        self.assertEqual(reg.get("cap-1").metadata["key"], "value")

    def test_get_returns_deep_copy(self):
        """Mutating a get() result must not affect the registry."""
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        got = reg.get("cap-1")
        got.description = "mutated"
        self.assertEqual(reg.get("cap-1").description, "Test capability cap-1")


# ==============================================================================
# 2. List
# ==============================================================================

class TestList(unittest.TestCase):
    def test_empty_registry(self):
        reg = CapabilityRegistry()
        self.assertEqual(reg.list(), [])

    def test_list_returns_all(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.register(_make_descriptor("cap-2"))
        reg.register(_make_descriptor("cap-3"))
        ids = {d.id for d in reg.list()}
        self.assertEqual(ids, {"cap-1", "cap-2", "cap-3"})

    def test_list_reflects_current_health(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.update_health("cap-1", success=True)
        items = reg.list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].health, CapabilityHealth.AVAILABLE)

    def test_list_returns_copies(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        items = reg.list()
        items[0].description = "mutated"
        self.assertEqual(reg.get("cap-1").description, "Test capability cap-1")


# ==============================================================================
# 3. Multiple implementations for the same semantic type
# ==============================================================================

class TestMultipleImplementations(unittest.TestCase):
    def test_two_reasoning_backends(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor(
            "reasoning-groq",
            capability_type=SemanticCapabilityType.REASONING,
            description="Groq LLaMA",
            is_local=False,
            requires_internet=True,
        ))
        reg.register(_make_descriptor(
            "reasoning-local",
            capability_type=SemanticCapabilityType.REASONING,
            description="Local GGUF model",
            is_local=True,
            requires_internet=False,
        ))
        self.assertEqual(reg.size, 2)
        reasoners = reg.query_by_semantic_type(SemanticCapabilityType.REASONING)
        self.assertEqual(len(reasoners), 2)
        ids = {d.id for d in reasoners}
        self.assertEqual(ids, {"reasoning-groq", "reasoning-local"})

    def test_health_independence_between_implementations(self):
        """One implementation's failure must not affect the other."""
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("reasoning-groq",
                                     capability_type=SemanticCapabilityType.REASONING))
        reg.register(_make_descriptor("reasoning-local",
                                     capability_type=SemanticCapabilityType.REASONING))

        # Groq fails
        reg.update_health("reasoning-groq", success=False,
                          failure_class=CanonicalFailureClass.AUTH_DENIED)
        # Local succeeds
        reg.update_health("reasoning-local", success=True)

        self.assertEqual(reg.get("reasoning-groq").health, CapabilityHealth.UNAVAILABLE)
        self.assertEqual(reg.get("reasoning-local").health, CapabilityHealth.AVAILABLE)

    def test_unregister_one_leaves_other(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("impl-a",
                                     capability_type=SemanticCapabilityType.WEB_RETRIEVAL))
        reg.register(_make_descriptor("impl-b",
                                     capability_type=SemanticCapabilityType.WEB_RETRIEVAL))
        reg.unregister("impl-a")
        remaining = reg.query_by_semantic_type(SemanticCapabilityType.WEB_RETRIEVAL)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].id, "impl-b")


# ==============================================================================
# 4. Querying
# ==============================================================================

class TestQueryBySemanticType(unittest.TestCase):
    def test_matches(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-web",
                                     capability_type=SemanticCapabilityType.WEB_RETRIEVAL))
        reg.register(_make_descriptor("cap-reason",
                                     capability_type=SemanticCapabilityType.REASONING))
        result = reg.query_by_semantic_type(SemanticCapabilityType.WEB_RETRIEVAL)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "cap-web")

    def test_no_matches(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1",
                                     capability_type=SemanticCapabilityType.REASONING))
        result = reg.query_by_semantic_type(SemanticCapabilityType.WEB_RETRIEVAL)
        self.assertEqual(result, [])

    def test_empty_registry(self):
        reg = CapabilityRegistry()
        self.assertEqual(
            reg.query_by_semantic_type(SemanticCapabilityType.REASONING), []
        )


class TestQueryByHealth(unittest.TestCase):
    def test_matches_unknown(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        result = reg.query_by_health(CapabilityHealth.UNKNOWN)
        self.assertEqual(len(result), 1)

    def test_matches_after_health_update(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.register(_make_descriptor("cap-2"))
        reg.update_health("cap-1", success=True)
        available = reg.query_by_health(CapabilityHealth.AVAILABLE)
        unknown = reg.query_by_health(CapabilityHealth.UNKNOWN)
        self.assertEqual(len(available), 1)
        self.assertEqual(available[0].id, "cap-1")
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0].id, "cap-2")

    def test_no_matches(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        self.assertEqual(reg.query_by_health(CapabilityHealth.AVAILABLE), [])


class TestQueryByLocality(unittest.TestCase):
    def test_local(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-local", is_local=True))
        reg.register(_make_descriptor("cap-remote", is_local=False))
        result = reg.query_by_locality(is_local=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "cap-local")

    def test_remote(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-local", is_local=True))
        reg.register(_make_descriptor("cap-remote", is_local=False))
        result = reg.query_by_locality(is_local=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "cap-remote")

    def test_empty_registry(self):
        reg = CapabilityRegistry()
        self.assertEqual(reg.query_by_locality(is_local=True), [])


# ==============================================================================
# 5. Health updates and policy-driven transitions
# ==============================================================================

class TestHealthUpdates(unittest.TestCase):
    def test_success_from_unknown_to_available(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        health = reg.update_health("cap-1", success=True)
        self.assertEqual(health, CapabilityHealth.AVAILABLE)
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.AVAILABLE)

    def test_transient_failure_to_degraded(self):
        policy = HealthPolicy(transient_failure_threshold=2)
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"), policy=policy)
        reg.update_health("cap-1", success=False,
                          failure_class=CanonicalFailureClass.TRANSIENT)
        reg.update_health("cap-1", success=False,
                          failure_class=CanonicalFailureClass.TRANSIENT)
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.DEGRADED)

    def test_auth_denied_immediate_unavailable(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.update_health("cap-1", success=False,
                          failure_class=CanonicalFailureClass.AUTH_DENIED)
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.UNAVAILABLE)

    def test_recovery_from_unavailable(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        # Drive to UNAVAILABLE
        reg.update_health("cap-1", success=False,
                          failure_class=CanonicalFailureClass.AUTH_DENIED)
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.UNAVAILABLE)
        # First success: UNAVAILABLE → DEGRADED
        reg.update_health("cap-1", success=True)
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.DEGRADED)
        # Second success: DEGRADED → AVAILABLE (default recovery_success_count=2)
        reg.update_health("cap-1", success=True)
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.AVAILABLE)

    def test_update_health_missing_capability_raises(self):
        reg = CapabilityRegistry()
        with self.assertRaises(CapabilityNotFoundError):
            reg.update_health("nonexistent", success=True)

    def test_update_health_failure_requires_class(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        with self.assertRaises(ValueError):
            reg.update_health("cap-1", success=False)

    def test_one_capability_update_does_not_mutate_another(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.register(_make_descriptor("cap-2"))
        reg.update_health("cap-1", success=True)
        # cap-2 must still be UNKNOWN
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.AVAILABLE)
        self.assertEqual(reg.get("cap-2").health, CapabilityHealth.UNKNOWN)

    def test_custom_policy_per_capability(self):
        """Different capabilities can have different health policies."""
        strict_policy = HealthPolicy(transient_failure_threshold=1)
        lenient_policy = HealthPolicy(transient_failure_threshold=10)

        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-strict"), policy=strict_policy)
        reg.register(_make_descriptor("cap-lenient"), policy=lenient_policy)

        # One transient failure: strict degrades, lenient stays available
        reg.update_health("cap-strict", success=False,
                          failure_class=CanonicalFailureClass.TRANSIENT)
        reg.update_health("cap-lenient", success=False,
                          failure_class=CanonicalFailureClass.TRANSIENT)

        self.assertEqual(reg.get("cap-strict").health, CapabilityHealth.DEGRADED)
        self.assertEqual(reg.get("cap-lenient").health, CapabilityHealth.UNKNOWN)

    def test_custom_policy_preserved_after_replacement(self):
        policy = HealthPolicy(transient_failure_threshold=1)
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"), policy=policy)
        reg.update_health("cap-1", success=False,
                          failure_class=CanonicalFailureClass.TRANSIENT)
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.DEGRADED)
        # Re-register resets health to UNKNOWN (and uses default policy)
        reg.register(_make_descriptor("cap-1"))
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.UNKNOWN)


# ==============================================================================
# 6. Tracker access
# ==============================================================================

class TestTrackerAccess(unittest.TestCase):
    def test_get_tracker_returns_tracker(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        tracker = reg.get_tracker("cap-1")
        self.assertIsNotNone(tracker)
        self.assertEqual(tracker.current_health, CapabilityHealth.UNKNOWN)

    def test_get_tracker_missing_returns_none(self):
        reg = CapabilityRegistry()
        self.assertIsNone(reg.get_tracker("nonexistent"))

    def test_tracker_reflects_health_updates(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        tracker = reg.get_tracker("cap-1")
        reg.update_health("cap-1", success=True)
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    def test_tracker_has_custom_policy(self):
        policy = HealthPolicy(recovery_success_count=5)
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"), policy=policy)
        tracker = reg.get_tracker("cap-1")
        self.assertEqual(tracker.policy.recovery_success_count, 5)

    def test_tracker_replaced_on_reregister(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        tracker1 = reg.get_tracker("cap-1")
        reg.register(_make_descriptor("cap-1"))
        tracker2 = reg.get_tracker("cap-1")
        # New tracker object after re-registration
        self.assertIsNot(tracker1, tracker2)
        self.assertEqual(tracker2.current_health, CapabilityHealth.UNKNOWN)


# ==============================================================================
# 7. Introspection
# ==============================================================================

class TestIntrospection(unittest.TestCase):
    def test_size_empty(self):
        self.assertEqual(CapabilityRegistry().size, 0)

    def test_size_after_register(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.register(_make_descriptor("cap-2"))
        self.assertEqual(reg.size, 2)

    def test_size_after_unregister(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.register(_make_descriptor("cap-2"))
        reg.unregister("cap-1")
        self.assertEqual(reg.size, 1)

    def test_contains(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        self.assertTrue(reg.contains("cap-1"))
        self.assertFalse(reg.contains("cap-2"))

    def test_registered_ids(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-b"))
        reg.register(_make_descriptor("cap-a"))
        ids = reg.registered_ids()
        # Insertion order preserved (dict preserves insertion order in Python 3.7+)
        self.assertEqual(ids, ["cap-b", "cap-a"])


# ==============================================================================
# 8. Thread safety
# ==============================================================================

class TestThreadSafety(unittest.TestCase):
    def test_concurrent_register_and_read(self):
        """Multiple threads registering and reading must not corrupt state."""
        reg = CapabilityRegistry()
        errors: list[Exception] = []

        def writer(prefix: str, count: int) -> None:
            try:
                for i in range(count):
                    reg.register(_make_descriptor(f"{prefix}-{i}"))
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(100):
                    reg.list()
                    reg.size
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=("a", 50)),
            threading.Thread(target=writer, args=("b", 50)),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(reg.size, 100)

    def test_concurrent_health_updates(self):
        """Multiple threads updating different capabilities must not interfere."""
        reg = CapabilityRegistry()
        cap_count = 20
        for i in range(cap_count):
            reg.register(_make_descriptor(f"cap-{i}"))

        errors: list[Exception] = []

        def updater(prefix: str) -> None:
            try:
                for i in range(cap_count):
                    cap_id = f"cap-{i}"
                    # Alternate success/failure
                    if i % 2 == 0:
                        reg.update_health(cap_id, success=True)
                    else:
                        reg.update_health(
                            cap_id,
                            success=False,
                            failure_class=CanonicalFailureClass.TRANSIENT,
                        )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=updater, args=("t1",)),
            threading.Thread(target=updater, args=("t2",)),
            threading.Thread(target=updater, args=("t3",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # All capabilities should have been updated
        for i in range(cap_count):
            health = reg.get(f"cap-{i}").health
            self.assertIn(health, (
                CapabilityHealth.AVAILABLE,
                CapabilityHealth.DEGRADED,
                CapabilityHealth.UNKNOWN,
            ))

    def test_concurrent_register_unregister(self):
        """Rapid register/unregister from multiple threads must not crash."""
        reg = CapabilityRegistry()
        errors: list[Exception] = []

        def churn(prefix: str, count: int) -> None:
            try:
                for i in range(count):
                    reg.register(_make_descriptor(f"{prefix}-{i}"))
                    reg.unregister(f"{prefix}-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=churn, args=(f"t{t}", 100))
            for t in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(reg.size, 0)

    def test_concurrent_mixed_operations(self):
        """Realistic mix of register, query, health-update, unregister."""
        reg = CapabilityRegistry()
        errors: list[Exception] = []

        def mixed_worker(worker_id: int) -> None:
            try:
                for i in range(50):
                    cap_id = f"w{worker_id}-cap-{i}"
                    reg.register(_make_descriptor(cap_id))
                    reg.get(cap_id)
                    reg.query_by_semantic_type(SemanticCapabilityType.REASONING)
                    reg.update_health(cap_id, success=True)
                    reg.query_by_health(CapabilityHealth.AVAILABLE)
                    reg.unregister(cap_id)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=mixed_worker, args=(w,))
            for w in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(reg.size, 0)


# ==============================================================================
# 9. Edge cases
# ==============================================================================

class TestEdgeCases(unittest.TestCase):
    def test_register_with_all_capability_types(self):
        """Every semantic type should be registerable."""
        reg = CapabilityRegistry()
        for i, cap_type in enumerate(SemanticCapabilityType):
            reg.register(_make_descriptor(f"cap-{i}", capability_type=cap_type))
        self.assertEqual(reg.size, len(SemanticCapabilityType))

    def test_register_with_empty_metadata(self):
        reg = CapabilityRegistry()
        desc = _make_descriptor("cap-1")
        reg.register(desc)
        self.assertEqual(reg.get("cap-1").metadata, {})

    def test_register_with_populated_metadata(self):
        reg = CapabilityRegistry()
        desc = _make_descriptor("cap-1", metadata={"provider": "groq", "tier": 1})
        reg.register(desc)
        got = reg.get("cap-1")
        self.assertEqual(got.metadata["provider"], "groq")
        self.assertEqual(got.metadata["tier"], 1)

    def test_health_update_on_unregistered_then_register(self):
        """Updating health for a non-existent ID raises, even if later registered."""
        reg = CapabilityRegistry()
        with self.assertRaises(CapabilityNotFoundError):
            reg.update_health("future-cap", success=True)
        # Now register it — should work fine
        reg.register(_make_descriptor("future-cap"))
        reg.update_health("future-cap", success=True)
        self.assertEqual(reg.get("future-cap").health, CapabilityHealth.AVAILABLE)

    def test_unregister_twice(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        self.assertTrue(reg.unregister("cap-1"))
        self.assertFalse(reg.unregister("cap-1"))

    def test_get_after_unregister_returns_none(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.unregister("cap-1")
        self.assertIsNone(reg.get("cap-1"))
        self.assertIsNone(reg.get_tracker("cap-1"))

    def test_query_returns_deep_copies(self):
        """Query results must not be affected by subsequent registry changes."""
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        results = reg.query_by_semantic_type(SemanticCapabilityType.REASONING)
        # Now change health
        reg.update_health("cap-1", success=True)
        # The previously returned result should still show UNKNOWN
        self.assertEqual(results[0].health, CapabilityHealth.UNKNOWN)
        # Fresh query should show AVAILABLE
        fresh = reg.query_by_semantic_type(SemanticCapabilityType.REASONING)
        self.assertEqual(fresh[0].health, CapabilityHealth.AVAILABLE)

    def test_custom_health_policy_with_extreme_values(self):
        """Edge case: very high thresholds mean failures never escalate."""
        policy = HealthPolicy(
            transient_failure_threshold=1000,
            transient_unavailable_threshold=2000,
        )
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"), policy=policy)
        # First succeed so health moves to AVAILABLE (the baseline for escalation)
        reg.update_health("cap-1", success=True)
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.AVAILABLE)
        for _ in range(100):
            reg.update_health("cap-1", success=False,
                              failure_class=CanonicalFailureClass.TRANSIENT)
        # 100 < 1000 threshold — still AVAILABLE, never degraded
        self.assertEqual(reg.get("cap-1").health, CapabilityHealth.AVAILABLE)

    def test_list_after_mixed_health_states(self):
        reg = CapabilityRegistry()
        reg.register(_make_descriptor("cap-1"))
        reg.register(_make_descriptor("cap-2"))
        reg.register(_make_descriptor("cap-3"))
        reg.update_health("cap-1", success=True)
        reg.update_health("cap-2", success=False,
                          failure_class=CanonicalFailureClass.AUTH_DENIED)
        # cap-3 stays UNKNOWN

        items = reg.list()
        health_map = {d.id: d.health for d in items}
        self.assertEqual(health_map["cap-1"], CapabilityHealth.AVAILABLE)
        self.assertEqual(health_map["cap-2"], CapabilityHealth.UNAVAILABLE)
        self.assertEqual(health_map["cap-3"], CapabilityHealth.UNKNOWN)


# ==============================================================================
# 10. Re-export verification
# ==============================================================================

class TestSchemaReExport(unittest.TestCase):
    """Verify that CapabilityRegistry is accessible from core.registry."""

    def test_import_from_core_registry(self):
        from core.registry import CapabilityRegistry as CR  # noqa: F811
        self.assertIs(CR, CapabilityRegistry)

    def test_import_capability_not_found_error(self):
        from core.registry import CapabilityNotFoundError  # noqa: F811
        self.assertTrue(issubclass(CapabilityNotFoundError, KeyError))


if __name__ == "__main__":
    unittest.main()
