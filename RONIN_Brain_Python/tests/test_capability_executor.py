"""Unit tests for the Execution Boundary (core.capability_executor).

Covers: successful execution, exception classification, health tracker
integration, elapsed timing, and the critical invariant that raw exceptions
never escape the boundary.
"""
from __future__ import annotations

import unittest

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityHealth,
    ExecutionOutcome,
)
from core.capability_executor import (
    _FAILURE_TO_OUTCOME,
    _outcome_for_failure,
    execute_capability,
)
from core.registry import CapabilityNotFoundError, CapabilityRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reg_with_cap(cap_id: str = "test-cap") -> CapabilityRegistry:
    """Create a registry with one registered REASONING capability."""
    from core.capabilities import CapabilityDescriptor, SemanticCapabilityType

    reg = CapabilityRegistry()
    reg.register(CapabilityDescriptor(
        id=cap_id,
        capability_type=SemanticCapabilityType.REASONING,
        description="Test capability",
    ))
    return reg


# ==============================================================================
# 1. Successful execution
# ==============================================================================

class TestSuccessfulExecution(unittest.TestCase):
    def test_success_returns_data(self):
        reg = _reg_with_cap()
        result = execute_capability(reg, "test-cap", lambda: 42)
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(result.data, 42)
        self.assertIsNone(result.failure_class)
        self.assertIsNone(result.diagnostics)

    def test_success_with_args_and_kwargs(self):
        reg = _reg_with_cap()
        result = execute_capability(
            reg, "test-cap", lambda a, b, c=0: a + b + c, 10, 20, c=5
        )
        self.assertEqual(result.data, 35)

    def test_success_updates_health(self):
        reg = _reg_with_cap()
        self.assertEqual(reg.get("test-cap").health, CapabilityHealth.UNKNOWN)
        execute_capability(reg, "test-cap", lambda: "ok")
        self.assertEqual(reg.get("test-cap").health, CapabilityHealth.AVAILABLE)

    def test_success_sets_capability_id(self):
        reg = _reg_with_cap()
        result = execute_capability(reg, "test-cap", lambda: None)
        self.assertEqual(result.capability_id, "test-cap")

    def test_success_records_elapsed_ms(self):
        reg = _reg_with_cap()
        result = execute_capability(reg, "test-cap", lambda: None)
        self.assertIsNotNone(result.elapsed_ms)
        self.assertGreaterEqual(result.elapsed_ms, 0)

    def test_success_preserves_complex_data(self):
        reg = _reg_with_cap()
        data = {"choices": [{"message": {"content": "hello"}}]}
        result = execute_capability(reg, "test-cap", lambda: data)
        self.assertEqual(result.data, data)


# ==============================================================================
# 2. Failed execution — classification and diagnostics
# ==============================================================================

class TestFailedExecution(unittest.TestCase):
    def test_timeout_is_transient_retryable(self):
        reg = _reg_with_cap()
        result = execute_capability(
            reg, "test-cap", (_ for _ in ()).throw, TimeoutError("timed out")
        )
        # Actually, the above won't work. Let me use a proper callable.
        def boom():
            raise TimeoutError("upstream timed out")

        result = execute_capability(reg, "test-cap", boom)
        self.assertEqual(result.outcome, ExecutionOutcome.RETRYABLE_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.TRANSIENT)
        self.assertIsNotNone(result.diagnostics)
        self.assertEqual(result.diagnostics.raw_error_type, "TimeoutError")
        self.assertIn("timed out", result.diagnostics.raw_message)

    def test_connection_error_is_network_isolated(self):
        reg = _reg_with_cap()

        def boom():
            raise ConnectionError("DNS resolution failed")

        result = execute_capability(reg, "test-cap", boom)
        self.assertEqual(result.outcome, ExecutionOutcome.RETRYABLE_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.NETWORK_ISOLATED)

    def test_auth_error_is_denied(self):
        reg = _reg_with_cap()

        def boom():
            raise PermissionError("invalid API key")

        result = execute_capability(reg, "test-cap", boom)
        self.assertEqual(result.outcome, ExecutionOutcome.DENIED)
        self.assertEqual(result.failure_class, CanonicalFailureClass.AUTH_DENIED)

    def test_file_not_found_is_fatal(self):
        reg = _reg_with_cap()

        def boom():
            raise FileNotFoundError("/tmp/missing.txt")

        result = execute_capability(reg, "test-cap", boom)
        self.assertEqual(result.outcome, ExecutionOutcome.FATAL_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.DETERMINISTIC_ERROR)

    def test_not_implemented_is_unsupported(self):
        reg = _reg_with_cap()

        def boom():
            raise NotImplementedError("not yet")

        result = execute_capability(reg, "test-cap", boom)
        self.assertEqual(result.outcome, ExecutionOutcome.UNSUPPORTED)
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNSUPPORTED_OPERATION)

    def test_value_error_is_validation_fatal(self):
        reg = _reg_with_cap()

        def boom():
            raise ValueError("malformed JSON")

        result = execute_capability(reg, "test-cap", boom)
        self.assertEqual(result.outcome, ExecutionOutcome.FATAL_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.VALIDATION_FAILED)

    def test_generic_runtime_error_is_unknown_fatal(self):
        reg = _reg_with_cap()

        def boom():
            raise RuntimeError("something completely unexpected")

        result = execute_capability(reg, "test-cap", boom)
        self.assertEqual(result.outcome, ExecutionOutcome.FATAL_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNKNOWN_FATAL)

    def test_failure_updates_health(self):
        reg = _reg_with_cap()
        # First succeed to move to AVAILABLE
        execute_capability(reg, "test-cap", lambda: "ok")
        self.assertEqual(reg.get("test-cap").health, CapabilityHealth.AVAILABLE)

        def boom():
            raise TimeoutError("timed out")

        execute_capability(reg, "test-cap", boom)
        # Health should have been updated (AVAILABLE → AVAILABLE since 1
        # transient failure is below default threshold of 3)
        tracker = reg.get_tracker("test-cap")
        self.assertEqual(tracker.consecutive_failures, 1)

    def test_failure_records_elapsed_ms(self):
        reg = _reg_with_cap()

        def boom():
            raise RuntimeError("boom")

        result = execute_capability(reg, "test-cap", boom)
        self.assertIsNotNone(result.elapsed_ms)
        self.assertGreaterEqual(result.elapsed_ms, 0)

    def test_failure_captures_stack_trace(self):
        reg = _reg_with_cap()

        def boom():
            raise ValueError("test error")

        result = execute_capability(reg, "test-cap", boom)
        self.assertIsNotNone(result.diagnostics.stack_trace)
        self.assertIn("ValueError", result.diagnostics.stack_trace)

    def test_failure_has_capability_id(self):
        reg = _reg_with_cap()

        def boom():
            raise RuntimeError("boom")

        result = execute_capability(reg, "test-cap", boom)
        self.assertEqual(result.capability_id, "test-cap")


# ==============================================================================
# 3. Exception never escapes the boundary
# ==============================================================================

class TestExceptionContainment(unittest.TestCase):
    def test_no_exception_propagates(self):
        """The executor must NEVER let a raw exception escape."""
        reg = _reg_with_cap()

        exceptions = [
            RuntimeError("runtime"),
            ValueError("value"),
            TypeError("type"),
            KeyError("key"),
            IndexError("index"),
            FileNotFoundError("file"),
            PermissionError("perm"),
            TimeoutError("timeout"),
            ConnectionError("conn"),
            NotImplementedError("not impl"),
            OSError("os"),
            RecursionError("recursion"),
            ArithmeticError("math"),
            Exception("generic"),
        ]

        for exc in exceptions:
            with self.subTest(exc_type=type(exc).__name__):

                def boom(_exc=exc):
                    raise _exc

                result = execute_capability(reg, "test-cap", boom)
                # Must return an ExecutionResult, never raise
                self.assertIsNotNone(result)
                self.assertIsNotNone(result.outcome)
                self.assertIsNotNone(result.failure_class)

    def test_keyboard_interrupt_still_raises(self):
        """KeyboardInterrupt and SystemExit should NOT be caught — they are
        signals, not capability failures."""
        reg = _reg_with_cap()

        def boom():
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            execute_capability(reg, "test-cap", boom)

    def test_system_exit_still_raises(self):
        reg = _reg_with_cap()

        def boom():
            raise SystemExit(1)

        with self.assertRaises(SystemExit):
            execute_capability(reg, "test-cap", boom)


# ==============================================================================
# 4. Registry interaction edge cases
# ==============================================================================

class TestRegistryInteraction(unittest.TestCase):
    def test_unregistered_capability_raises(self):
        reg = CapabilityRegistry()
        with self.assertRaises(CapabilityNotFoundError):
            execute_capability(reg, "no-such-cap", lambda: None)

    def test_sequential_executions_track_health(self):
        reg = _reg_with_cap()

        # Success 1 → AVAILABLE
        execute_capability(reg, "test-cap", lambda: "ok")
        self.assertEqual(reg.get("test-cap").health, CapabilityHealth.AVAILABLE)

        # Success 2 → stays AVAILABLE
        execute_capability(reg, "test-cap", lambda: "ok")
        self.assertEqual(reg.get("test-cap").health, CapabilityHealth.AVAILABLE)

        # Failure → health changes
        def boom():
            raise ConnectionError("down")

        execute_capability(reg, "test-cap", boom)
        self.assertEqual(reg.get("test-cap").health, CapabilityHealth.UNAVAILABLE)

    def test_multiple_capabilities_independent(self):
        from core.capabilities import CapabilityDescriptor, SemanticCapabilityType

        reg = CapabilityRegistry()
        reg.register(CapabilityDescriptor(
            id="cap-a", capability_type=SemanticCapabilityType.REASONING,
            description="A",
        ))
        reg.register(CapabilityDescriptor(
            id="cap-b", capability_type=SemanticCapabilityType.WEB_RETRIEVAL,
            description="B",
        ))

        execute_capability(reg, "cap-a", lambda: "ok")

        def boom():
            raise TimeoutError("timed out")

        execute_capability(reg, "cap-b", boom)

        self.assertEqual(reg.get("cap-a").health, CapabilityHealth.AVAILABLE)
        # cap-b got a transient failure, still UNKNOWN (below threshold)
        self.assertEqual(reg.get("cap-b").health, CapabilityHealth.UNKNOWN)


# ==============================================================================
# 5. Failure-to-outcome mapping completeness
# ==============================================================================

class TestFailureToOutcomeMapping(unittest.TestCase):
    def test_all_failure_classes_mapped(self):
        """Every CanonicalFailureClass must have an outcome mapping."""
        from core.capabilities import CanonicalFailureClass

        for fc in CanonicalFailureClass:
            self.assertIn(
                fc, _FAILURE_TO_OUTCOME,
                f"{fc.name} has no outcome mapping",
            )

    def test_mapping_values(self):
        self.assertEqual(
            _outcome_for_failure(CanonicalFailureClass.TRANSIENT),
            ExecutionOutcome.RETRYABLE_FAILURE,
        )
        self.assertEqual(
            _outcome_for_failure(CanonicalFailureClass.NETWORK_ISOLATED),
            ExecutionOutcome.RETRYABLE_FAILURE,
        )
        self.assertEqual(
            _outcome_for_failure(CanonicalFailureClass.AUTH_DENIED),
            ExecutionOutcome.DENIED,
        )
        self.assertEqual(
            _outcome_for_failure(CanonicalFailureClass.POLICY_BLOCKED),
            ExecutionOutcome.BLOCKED,
        )
        self.assertEqual(
            _outcome_for_failure(CanonicalFailureClass.UNSUPPORTED_OPERATION),
            ExecutionOutcome.UNSUPPORTED,
        )
        self.assertEqual(
            _outcome_for_failure(CanonicalFailureClass.UNKNOWN_FATAL),
            ExecutionOutcome.FATAL_FAILURE,
        )


if __name__ == "__main__":
    unittest.main()
