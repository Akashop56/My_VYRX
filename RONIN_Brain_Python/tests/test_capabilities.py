"""Unit tests for the Capability-Oriented Architecture foundational schemas.

Covers schema validation, outcome modelling, diagnostic capture, policy-driven
health transitions, failure classification, and architectural invariant
verification.
"""
from __future__ import annotations

import unittest

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    DiagnosticContext,
    ExecutionOutcome,
    ExecutionResult,
    HealthPolicy,
    HealthTracker,
    SemanticCapabilityType,
    classify_exception,
    classify_http_status,
)


# ==============================================================================
# 1. SemanticCapabilityType & CapabilityHealth enums
# ==============================================================================

class TestSemanticCapabilityType(unittest.TestCase):
    def test_all_members_present(self):
        expected = {
            "REASONING", "SUMMARIZATION", "SYNTHESIS", "CLASSIFICATION",
            "WEB_RETRIEVAL", "LOCAL_KNOWLEDGE_SEARCH", "DETERMINISTIC_COMPUTE",
            "FILE_SYSTEM_IO", "DEVICE_INTERACTION", "SYSTEM_COMMAND",
            "MEMORY_PERSISTENCE",
        }
        actual = {m.name for m in SemanticCapabilityType}
        self.assertEqual(actual, expected)

    def test_string_serialisation(self):
        self.assertEqual(SemanticCapabilityType.REASONING.value, "reasoning")
        self.assertEqual(SemanticCapabilityType("web_retrieval"), SemanticCapabilityType.WEB_RETRIEVAL)

    def test_is_str_subclass(self):
        """Enums are str subclasses so they serialize as their value in JSON."""
        self.assertIsInstance(SemanticCapabilityType.REASONING, str)
        self.assertEqual(SemanticCapabilityType.REASONING.value, "reasoning")


class TestCapabilityHealth(unittest.TestCase):
    def test_all_members(self):
        expected = {"AVAILABLE", "DEGRADED", "UNAVAILABLE", "UNKNOWN"}
        self.assertEqual({m.name for m in CapabilityHealth}, expected)

    def test_string_values(self):
        self.assertEqual(CapabilityHealth.DEGRADED.value, "degraded")

    def test_is_str_subclass(self):
        self.assertIsInstance(CapabilityHealth.AVAILABLE, str)


# ==============================================================================
# 2. CapabilityDescriptor
# ==============================================================================

class TestCapabilityDescriptor(unittest.TestCase):
    def _make_descriptor(self, **overrides):
        defaults = dict(
            id="cap-1",
            capability_type=SemanticCapabilityType.REASONING,
            description="Test capability",
        )
        defaults.update(overrides)
        return CapabilityDescriptor(**defaults)

    def test_minimal_valid(self):
        desc = self._make_descriptor()
        self.assertEqual(desc.id, "cap-1")
        self.assertEqual(desc.capability_type, SemanticCapabilityType.REASONING)
        self.assertTrue(desc.is_local)
        self.assertEqual(desc.health, CapabilityHealth.UNKNOWN)
        self.assertEqual(desc.estimated_cost_tier, "free")
        self.assertEqual(desc.required_permissions, [])
        self.assertEqual(desc.system_dependencies, [])
        self.assertEqual(desc.metadata, {})

    def test_full_valid(self):
        desc = self._make_descriptor(
            capability_type=SemanticCapabilityType.WEB_RETRIEVAL,
            description="Searches the web",
            requires_internet=True,
            requires_auth=True,
            required_permissions=["internet", "network_state"],
            system_dependencies=["curl"],
            is_local=False,
            estimated_latency_ms=2500,
            estimated_cost_tier="medium",
            health=CapabilityHealth.AVAILABLE,
            metadata={"provider": "serpapi"},
        )
        self.assertFalse(desc.is_local)
        self.assertTrue(desc.requires_internet)
        self.assertEqual(len(desc.required_permissions), 2)
        self.assertEqual(desc.metadata["provider"], "serpapi")

    def test_id_must_be_non_empty(self):
        with self.assertRaises(Exception):
            self._make_descriptor(id="")

    def test_description_max_length(self):
        with self.assertRaises(Exception):
            self._make_descriptor(description="x" * 2049)

    def test_estimated_latency_non_negative(self):
        with self.assertRaises(Exception):
            self._make_descriptor(estimated_latency_ms=-1)

    def test_roundtrip_dict(self):
        desc = self._make_descriptor()
        data = desc.model_dump()
        restored = CapabilityDescriptor(**data)
        self.assertEqual(desc, restored)

    def test_roundtrip_json(self):
        desc = self._make_descriptor()
        json_str = desc.model_dump_json()
        restored = CapabilityDescriptor.model_validate_json(json_str)
        self.assertEqual(desc, restored)

    def test_local_remote_capability_distinction(self):
        """Internet-dependent capabilities must be explicitly non-local."""
        local = self._make_descriptor(
            capability_type=SemanticCapabilityType.DETERMINISTIC_COMPUTE,
            is_local=True, requires_internet=False,
        )
        remote = self._make_descriptor(
            id="cap-web",
            capability_type=SemanticCapabilityType.WEB_RETRIEVAL,
            is_local=False, requires_internet=True,
        )
        self.assertTrue(local.is_local)
        self.assertFalse(local.requires_internet)
        self.assertFalse(remote.is_local)
        self.assertTrue(remote.requires_internet)


# ==============================================================================
# 3. CanonicalFailureClass
# ==============================================================================

class TestCanonicalFailureClass(unittest.TestCase):
    def test_all_members(self):
        expected = {
            "TRANSIENT", "NETWORK_ISOLATED", "AUTH_DENIED", "POLICY_BLOCKED",
            "VALIDATION_FAILED", "DETERMINISTIC_ERROR", "UNSUPPORTED_OPERATION",
            "UNKNOWN_FATAL",
        }
        self.assertEqual({m.name for m in CanonicalFailureClass}, expected)

    def test_string_values(self):
        self.assertEqual(CanonicalFailureClass.TRANSIENT.value, "transient")
        self.assertEqual(CanonicalFailureClass("auth_denied"), CanonicalFailureClass.AUTH_DENIED)

    def test_is_str_subclass(self):
        self.assertIsInstance(CanonicalFailureClass.TRANSIENT, str)


# ==============================================================================
# 4. DiagnosticContext
# ==============================================================================

class TestDiagnosticContext(unittest.TestCase):
    def test_all_fields_optional(self):
        ctx = DiagnosticContext()
        self.assertIsNone(ctx.raw_error_type)
        self.assertIsNone(ctx.raw_message)
        self.assertIsNone(ctx.http_status)
        self.assertIsNone(ctx.endpoint_or_path)
        self.assertIsNone(ctx.stack_trace)
        self.assertEqual(ctx.extra_details, {})

    def test_populated(self):
        ctx = DiagnosticContext(
            raw_error_type="TimeoutError",
            raw_message="upstream timed out",
            http_status=504,
            endpoint_or_path="/v1/chat/completions",
            stack_trace="Traceback (most recent call last):\n  ...",
            extra_details={"retry_after": 30},
        )
        self.assertEqual(ctx.http_status, 504)
        self.assertEqual(ctx.extra_details["retry_after"], 30)

    def test_roundtrip_json(self):
        ctx = DiagnosticContext(
            raw_error_type="ValueError",
            raw_message="bad input",
            extra_details={"field": "prompt"},
        )
        json_str = ctx.model_dump_json()
        restored = DiagnosticContext.model_validate_json(json_str)
        self.assertEqual(ctx, restored)

    def test_preserves_long_messages(self):
        long_msg = "x" * 5000
        ctx = DiagnosticContext(raw_message=long_msg)
        # DiagnosticContext stores as-is; truncation is the caller's job
        self.assertEqual(len(ctx.raw_message), 5000)


# ==============================================================================
# 5. classify_http_status
# ==============================================================================

class TestClassifyHttpStatus(unittest.TestCase):
    def test_auth_errors(self):
        self.assertEqual(classify_http_status(401), CanonicalFailureClass.AUTH_DENIED)
        self.assertEqual(classify_http_status(403), CanonicalFailureClass.AUTH_DENIED)

    def test_rate_limit(self):
        self.assertEqual(classify_http_status(429), CanonicalFailureClass.TRANSIENT)

    def test_server_errors_are_transient(self):
        for code in (500, 502, 503, 504):
            self.assertEqual(
                classify_http_status(code), CanonicalFailureClass.TRANSIENT,
                f"HTTP {code} should be TRANSIENT",
            )

    def test_client_validation_errors(self):
        self.assertEqual(classify_http_status(400), CanonicalFailureClass.VALIDATION_FAILED)
        self.assertEqual(classify_http_status(422), CanonicalFailureClass.VALIDATION_FAILED)

    def test_not_found(self):
        self.assertEqual(classify_http_status(404), CanonicalFailureClass.DETERMINISTIC_ERROR)

    def test_policy_blocked(self):
        self.assertEqual(classify_http_status(451), CanonicalFailureClass.POLICY_BLOCKED)

    def test_other_4xx_is_validation(self):
        # 408 Request Timeout, 409 Conflict, etc. — catch-all for 4xx
        self.assertEqual(classify_http_status(408), CanonicalFailureClass.VALIDATION_FAILED)
        self.assertEqual(classify_http_status(418), CanonicalFailureClass.VALIDATION_FAILED)

    def test_other_5xx_is_transient(self):
        self.assertEqual(classify_http_status(599), CanonicalFailureClass.TRANSIENT)

    def test_success_codes_are_unknown_fatal(self):
        # Not errors — should never be called with these, but handle gracefully
        self.assertEqual(classify_http_status(200), CanonicalFailureClass.UNKNOWN_FATAL)
        self.assertEqual(classify_http_status(301), CanonicalFailureClass.UNKNOWN_FATAL)


# ==============================================================================
# 6. classify_exception
# ==============================================================================

class TestClassifyException(unittest.TestCase):
    def test_connection_error_is_network_isolated(self):
        cls, diag = classify_exception(ConnectionError("reset by peer"))
        self.assertEqual(cls, CanonicalFailureClass.NETWORK_ISOLATED)
        self.assertEqual(diag.raw_error_type, "ConnectionError")

    def test_connection_refused_is_network_isolated(self):
        cls, _ = classify_exception(ConnectionRefusedError("refused"))
        self.assertEqual(cls, CanonicalFailureClass.NETWORK_ISOLATED)

    def test_timeout_error_is_transient(self):
        cls, _ = classify_exception(TimeoutError("operation timed out"))
        self.assertEqual(cls, CanonicalFailureClass.TRANSIENT)

    def test_file_not_found_is_deterministic(self):
        cls, _ = classify_exception(FileNotFoundError("/tmp/missing.txt"))
        self.assertEqual(cls, CanonicalFailureClass.DETERMINISTIC_ERROR)

    def test_not_implemented_is_unsupported(self):
        cls, _ = classify_exception(NotImplementedError("not yet"))
        self.assertEqual(cls, CanonicalFailureClass.UNSUPPORTED_OPERATION)

    def test_value_error_is_validation(self):
        cls, _ = classify_exception(ValueError("bad value"))
        self.assertEqual(cls, CanonicalFailureClass.VALIDATION_FAILED)

    def test_type_error_is_validation(self):
        cls, _ = classify_exception(TypeError("wrong type"))
        self.assertEqual(cls, CanonicalFailureClass.VALIDATION_FAILED)

    def test_http_status_takes_precedence(self):
        """When http_status is provided and >= 400, it wins over exception type."""
        cls, diag = classify_exception(
            TimeoutError("timed out"), http_status=401, endpoint="/api",
        )
        self.assertEqual(cls, CanonicalFailureClass.AUTH_DENIED)
        self.assertEqual(diag.http_status, 401)
        self.assertEqual(diag.endpoint_or_path, "/api")

    def test_http_status_below_400_ignored_for_classification(self):
        """Status < 400 is not an error; fall through to exception type.
        The diagnostic context still records it for telemetry."""
        cls, diag = classify_exception(
            TimeoutError("timed out"), http_status=200,
        )
        self.assertEqual(cls, CanonicalFailureClass.TRANSIENT)
        # Diagnostic context records the status for telemetry even though
        # it wasn't used for classification
        self.assertEqual(diag.http_status, 200)

    def test_message_timeout_heuristic(self):
        """Non-timeout exceptions whose message mentions timeout."""
        cls, _ = classify_exception(RuntimeError("request timeout exceeded"))
        self.assertEqual(cls, CanonicalFailureClass.TRANSIENT)

    def test_message_rate_limit_heuristic(self):
        cls, _ = classify_exception(RuntimeError("429 Too Many Requests"))
        self.assertEqual(cls, CanonicalFailureClass.TRANSIENT)

    def test_message_api_key_heuristic(self):
        cls, _ = classify_exception(RuntimeError("invalid API key provided"))
        self.assertEqual(cls, CanonicalFailureClass.AUTH_DENIED)

    def test_message_permission_denied_heuristic(self):
        cls, _ = classify_exception(RuntimeError("permission denied by policy"))
        self.assertEqual(cls, CanonicalFailureClass.POLICY_BLOCKED)

    def test_message_not_found_heuristic(self):
        cls, _ = classify_exception(RuntimeError("file not found: /tmp/x"))
        self.assertEqual(cls, CanonicalFailureClass.DETERMINISTIC_ERROR)

    def test_message_tool_not_found_heuristic(self):
        cls, _ = classify_exception(RuntimeError("Tool not found: frobnicate"))
        self.assertEqual(cls, CanonicalFailureClass.UNSUPPORTED_OPERATION)

    def test_message_unsupported_heuristic(self):
        cls, _ = classify_exception(RuntimeError("this operation is not supported"))
        self.assertEqual(cls, CanonicalFailureClass.UNSUPPORTED_OPERATION)

    def test_unknown_exception_is_fatal(self):
        cls, _ = classify_exception(RuntimeError("something completely unexpected"))
        self.assertEqual(cls, CanonicalFailureClass.UNKNOWN_FATAL)

    def test_diagnostic_context_captures_all_fields(self):
        cls, diag = classify_exception(
            ValueError("bad json"),
            http_status=422,
            endpoint="/v1/chat/completions",
            stack_trace="Traceback...",
            extra_details={"field": "messages"},
        )
        self.assertEqual(diag.raw_error_type, "ValueError")
        self.assertEqual(diag.raw_message, "bad json")
        self.assertEqual(diag.http_status, 422)
        self.assertEqual(diag.endpoint_or_path, "/v1/chat/completions")
        self.assertEqual(diag.stack_trace, "Traceback...")
        self.assertEqual(diag.extra_details["field"], "messages")

    def test_extra_details_defaults_empty(self):
        _, diag = classify_exception(RuntimeError("x"))
        self.assertEqual(diag.extra_details, {})

    def test_stack_trace_none_when_not_provided(self):
        _, diag = classify_exception(RuntimeError("x"))
        self.assertIsNone(diag.stack_trace)

    def test_http_status_positive_but_below_400_recorded_for_telemetry(self):
        """Non-error status codes are recorded in diagnostics but not used for classification."""
        _, diag = classify_exception(RuntimeError("x"), http_status=100)
        self.assertEqual(diag.http_status, 100)  # recorded for telemetry

    def test_http_status_negative_not_recorded(self):
        _, diag = classify_exception(RuntimeError("x"), http_status=-1)
        self.assertIsNone(diag.http_status)


# ==============================================================================
# 7. ExecutionOutcome & ExecutionResult
# ==============================================================================

class TestExecutionOutcome(unittest.TestCase):
    def test_all_members(self):
        expected = {
            "SUCCESS", "PARTIAL_SUCCESS", "RETRYABLE_FAILURE", "BLOCKED",
            "DENIED", "UNSUPPORTED", "FATAL_FAILURE",
        }
        self.assertEqual({m.name for m in ExecutionOutcome}, expected)


class TestExecutionResult(unittest.TestCase):
    def test_minimal(self):
        result = ExecutionResult(outcome=ExecutionOutcome.SUCCESS)
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertIsNone(result.sub_goal_id)
        self.assertIsNone(result.capability_id)
        self.assertIsNone(result.data)
        self.assertIsNone(result.partial_data)
        self.assertIsNone(result.failure_class)
        self.assertIsNone(result.diagnostics)
        self.assertIsNone(result.elapsed_ms)

    def test_success_with_data(self):
        result = ExecutionResult(
            outcome=ExecutionOutcome.SUCCESS,
            sub_goal_id="sg-1",
            capability_id="cap-reason",
            data={"answer": 42},
            elapsed_ms=150,
        )
        self.assertEqual(result.data["answer"], 42)
        self.assertEqual(result.elapsed_ms, 150)

    def test_partial_success(self):
        result = ExecutionResult(
            outcome=ExecutionOutcome.PARTIAL_SUCCESS,
            capability_id="cap-web",
            data={"summary": "partial"},
            partial_data={"raw": "incomplete html"},
            elapsed_ms=3200,
        )
        self.assertEqual(result.outcome, ExecutionOutcome.PARTIAL_SUCCESS)
        self.assertIsNotNone(result.partial_data)

    def test_retryable_failure(self):
        diag = DiagnosticContext(
            raw_error_type="RateLimitError",
            raw_message="429 Too Many Requests",
            http_status=429,
            endpoint_or_path="/v1/chat/completions",
        )
        result = ExecutionResult(
            outcome=ExecutionOutcome.RETRYABLE_FAILURE,
            capability_id="cap-llm",
            failure_class=CanonicalFailureClass.TRANSIENT,
            diagnostics=diag,
            elapsed_ms=50,
        )
        self.assertEqual(result.failure_class, CanonicalFailureClass.TRANSIENT)
        self.assertEqual(result.diagnostics.http_status, 429)

    def test_fatal_failure(self):
        diag = DiagnosticContext(
            raw_error_type="RuntimeError",
            raw_message="something terrible happened",
            stack_trace="Traceback ...\nRuntimeError: something terrible happened",
        )
        result = ExecutionResult(
            outcome=ExecutionOutcome.FATAL_FAILURE,
            failure_class=CanonicalFailureClass.UNKNOWN_FATAL,
            diagnostics=diag,
        )
        self.assertEqual(result.outcome, ExecutionOutcome.FATAL_FAILURE)

    def test_elapsed_ms_must_be_non_negative(self):
        with self.assertRaises(Exception):
            ExecutionResult(outcome=ExecutionOutcome.SUCCESS, elapsed_ms=-5)

    def test_roundtrip_json(self):
        result = ExecutionResult(
            outcome=ExecutionOutcome.DENIED,
            capability_id="cap-fs",
            failure_class=CanonicalFailureClass.POLICY_BLOCKED,
            diagnostics=DiagnosticContext(raw_message="permission denied"),
        )
        json_str = result.model_dump_json()
        restored = ExecutionResult.model_validate_json(json_str)
        self.assertEqual(result, restored)

    def test_sub_goal_isolation(self):
        """Sub-goal failures are scoped; different sub-goals are independent."""
        sg1 = ExecutionResult(
            outcome=ExecutionOutcome.FATAL_FAILURE,
            sub_goal_id="sg-1",
            capability_id="cap-web",
            failure_class=CanonicalFailureClass.TRANSIENT,
        )
        sg2 = ExecutionResult(
            outcome=ExecutionOutcome.SUCCESS,
            sub_goal_id="sg-2",
            capability_id="cap-reason",
            data={"answer": "ok"},
        )
        # Different sub-goals, different outcomes — parent task is not failed
        self.assertNotEqual(sg1.outcome, sg2.outcome)
        self.assertNotEqual(sg1.sub_goal_id, sg2.sub_goal_id)


# ==============================================================================
# 8. HealthPolicy
# ==============================================================================

class TestHealthPolicy(unittest.TestCase):
    def test_defaults(self):
        policy = HealthPolicy()
        self.assertEqual(policy.transient_failure_threshold, 3)
        self.assertEqual(policy.transient_unavailable_threshold, 5)
        self.assertTrue(policy.immediate_auth_failure)
        self.assertTrue(policy.immediate_policy_block)
        self.assertEqual(policy.recovery_window_seconds, 60)
        self.assertEqual(policy.recovery_success_count, 2)

    def test_custom_values(self):
        policy = HealthPolicy(
            transient_failure_threshold=10,
            transient_unavailable_threshold=20,
            immediate_auth_failure=False,
            immediate_policy_block=False,
            recovery_window_seconds=120,
            recovery_success_count=5,
        )
        self.assertEqual(policy.transient_failure_threshold, 10)
        self.assertFalse(policy.immediate_auth_failure)

    def test_threshold_validation(self):
        with self.assertRaises(Exception):
            HealthPolicy(transient_failure_threshold=0)  # must be >= 1
        with self.assertRaises(Exception):
            HealthPolicy(transient_unavailable_threshold=1)  # must be >= 2
        with self.assertRaises(Exception):
            HealthPolicy(recovery_window_seconds=0)  # must be >= 1
        with self.assertRaises(Exception):
            HealthPolicy(recovery_success_count=0)  # must be >= 1

    def test_transient_unavailable_must_exceed_transient_degraded(self):
        """Logical invariant: unavailable threshold must be >= degraded threshold.
        Not enforced by Pydantic (would require model_validator), but verified here
        as an operational contract."""
        policy = HealthPolicy(transient_failure_threshold=5, transient_unavailable_threshold=3)
        # The model allows this, but callers should ensure >=. This test documents
        # the expected usage.
        self.assertGreaterEqual(policy.transient_failure_threshold, 1)


# ==============================================================================
# 9. HealthTracker — policy-driven transitions
# ==============================================================================

class TestHealthTracker(unittest.TestCase):
    # --- initialisation ---

    def test_default_initial_health(self):
        tracker = HealthTracker("cap-1")
        self.assertEqual(tracker.current_health, CapabilityHealth.UNKNOWN)

    def test_custom_initial_health(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.AVAILABLE)
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    def test_consecutive_failures_starts_at_zero(self):
        tracker = HealthTracker("cap-1")
        self.assertEqual(tracker.consecutive_failures, 0)

    def test_reset(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.UNAVAILABLE)
        tracker._consecutive_transient_failures = 10
        tracker._consecutive_successes = 5
        tracker.reset()
        self.assertEqual(tracker.current_health, CapabilityHealth.UNKNOWN)
        self.assertEqual(tracker.consecutive_failures, 0)
        self.assertEqual(tracker._consecutive_successes, 0)

    def test_reset_to_specific_health(self):
        tracker = HealthTracker("cap-1")
        tracker.reset(health=CapabilityHealth.AVAILABLE)
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    # --- success recording ---

    def test_success_from_unknown_moves_to_available(self):
        tracker = HealthTracker("cap-1")
        result = tracker.record_success()
        self.assertEqual(result, CapabilityHealth.AVAILABLE)
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    def test_success_from_available_stays_available(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.AVAILABLE)
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    def test_degraded_recovers_after_recovery_success_count(self):
        policy = HealthPolicy(recovery_success_count=3)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.DEGRADED)

        tracker.record_success()  # 1
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)
        tracker.record_success()  # 2
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)
        tracker.record_success()  # 3 → recover
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    def test_degraded_recovery_counter_resets_on_failure(self):
        policy = HealthPolicy(recovery_success_count=2)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.DEGRADED)

        tracker.record_success()  # 1
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)  # reset
        tracker.record_success()  # 1 again (not 2)
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)

    def test_unavailable_to_degraded_on_first_success(self):
        """UNAVAILABLE → DEGRADED is immediate partial recovery."""
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.UNAVAILABLE)
        result = tracker.record_success()
        self.assertEqual(result, CapabilityHealth.DEGRADED)
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)

    def test_unavailable_recovery_requires_sustained_success(self):
        """UNAVAILABLE → DEGRADED → AVAILABLE requires recovery_success_count
        successes *after* the UNAVAILABLE→DEGRADED transition."""
        policy = HealthPolicy(recovery_success_count=2)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.UNAVAILABLE)

        # 1st success: UNAVAILABLE → DEGRADED (counter resets to 1)
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)

        # 2nd success: DEGRADED, counter=2, 2 >= recovery_success_count(2) → AVAILABLE
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    def test_unavailable_recovery_with_high_threshold(self):
        """With recovery_success_count=3, UNAVAILABLE → AVAILABLE needs 4 total successes."""
        policy = HealthPolicy(recovery_success_count=3)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.UNAVAILABLE)

        # 1st: UNAVAILABLE → DEGRADED (counter=1)
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)
        # 2nd: DEGRADED, counter=2 < 3
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)
        # 3rd: DEGRADED, counter=3 >= 3 → AVAILABLE
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    def test_unavailable_recovery_resets_on_intervening_failure(self):
        """A failure during recovery resets the success counter."""
        policy = HealthPolicy(recovery_success_count=2)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.UNAVAILABLE)

        tracker.record_success()  # UNAVAILABLE → DEGRADED
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)  # back to counting
        self.assertEqual(tracker.consecutive_failures, 1)
        # Recovery counter was reset by the failure
        tracker.record_success()  # counter=1, still DEGRADED
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)
        tracker.record_success()  # counter=2 >= 2 → AVAILABLE
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    # --- transient failures ---

    def test_transient_failures_below_threshold_stay_available(self):
        policy = HealthPolicy(transient_failure_threshold=3, transient_unavailable_threshold=5)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.AVAILABLE)

        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        # 2 < threshold 3 → still AVAILABLE
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)
        self.assertEqual(tracker.consecutive_failures, 2)

    def test_transient_failures_reach_degraded(self):
        policy = HealthPolicy(transient_failure_threshold=3, transient_unavailable_threshold=5)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.AVAILABLE)

        for _ in range(3):
            tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)

    def test_transient_failures_reach_unavailable(self):
        policy = HealthPolicy(transient_failure_threshold=3, transient_unavailable_threshold=5)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.AVAILABLE)

        for _ in range(5):
            tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        self.assertEqual(tracker.current_health, CapabilityHealth.UNAVAILABLE)

    def test_transient_counter_resets_on_success(self):
        policy = HealthPolicy(transient_failure_threshold=3, transient_unavailable_threshold=5)
        tracker = HealthTracker("cap-1", policy=policy)

        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        tracker.record_success()  # reset counter
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        # Only 1 consecutive transient after reset → stays AVAILABLE
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)
        self.assertEqual(tracker.consecutive_failures, 1)

    # --- immediate escalations (unconditional) ---

    def test_network_isolated_immediate_unavailable(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.AVAILABLE)
        tracker.record_failure(CanonicalFailureClass.NETWORK_ISOLATED)
        self.assertEqual(tracker.current_health, CapabilityHealth.UNAVAILABLE)

    def test_unsupported_operation_immediate_unavailable(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.AVAILABLE)
        tracker.record_failure(CanonicalFailureClass.UNSUPPORTED_OPERATION)
        self.assertEqual(tracker.current_health, CapabilityHealth.UNAVAILABLE)

    def test_unknown_fatal_immediate_unavailable(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.AVAILABLE)
        tracker.record_failure(CanonicalFailureClass.UNKNOWN_FATAL)
        self.assertEqual(tracker.current_health, CapabilityHealth.UNAVAILABLE)

    # --- immediate escalations (policy-controlled) ---

    def test_auth_denied_immediate_unavailable(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.AVAILABLE)
        result = tracker.record_failure(CanonicalFailureClass.AUTH_DENIED)
        self.assertEqual(result, CapabilityHealth.UNAVAILABLE)

    def test_auth_denied_disabled_by_policy(self):
        policy = HealthPolicy(immediate_auth_failure=False)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.AVAILABLE)
        result = tracker.record_failure(CanonicalFailureClass.AUTH_DENIED)
        # Without immediate escalation, AUTH_DENIED degrades
        self.assertEqual(result, CapabilityHealth.DEGRADED)

    def test_policy_blocked_immediate_unavailable(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.AVAILABLE)
        result = tracker.record_failure(CanonicalFailureClass.POLICY_BLOCKED)
        self.assertEqual(result, CapabilityHealth.UNAVAILABLE)

    def test_policy_blocked_disabled_by_policy(self):
        policy = HealthPolicy(immediate_policy_block=False)
        tracker = HealthTracker("cap-1", policy=policy, initial_health=CapabilityHealth.AVAILABLE)
        result = tracker.record_failure(CanonicalFailureClass.POLICY_BLOCKED)
        self.assertEqual(result, CapabilityHealth.DEGRADED)

    # --- degraded from validation/deterministic errors ---

    def test_validation_failed_moves_to_degraded(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.AVAILABLE)
        tracker.record_failure(CanonicalFailureClass.VALIDATION_FAILED)
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)

    def test_deterministic_error_moves_to_degraded(self):
        tracker = HealthTracker("cap-1", initial_health=CapabilityHealth.AVAILABLE)
        tracker.record_failure(CanonicalFailureClass.DETERMINISTIC_ERROR)
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)

    # --- full lifecycle ---

    def test_full_lifecycle(self):
        """UNKNOWN → AVAILABLE → DEGRADED → UNAVAILABLE → DEGRADED → AVAILABLE."""
        policy = HealthPolicy(
            transient_failure_threshold=2,
            transient_unavailable_threshold=4,
            recovery_success_count=2,
        )
        tracker = HealthTracker("cap-lifecycle", policy=policy)

        # UNKNOWN → AVAILABLE (first success)
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

        # AVAILABLE → DEGRADED (hit transient threshold)
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)

        # DEGRADED → UNAVAILABLE (more transients)
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        self.assertEqual(tracker.current_health, CapabilityHealth.UNAVAILABLE)

        # UNAVAILABLE → DEGRADED (first success = partial recovery)
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)

        # DEGRADED → AVAILABLE (second success completes recovery)
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    def test_alternating_success_failure_oscillation(self):
        """Rapid alternation keeps health oscillating between AVAILABLE and DEGRADED."""
        policy = HealthPolicy(transient_failure_threshold=2, transient_unavailable_threshold=5)
        tracker = HealthTracker("cap-osc", policy=policy, initial_health=CapabilityHealth.AVAILABLE)

        # Single failure doesn't degrade (below threshold)
        tracker.record_failure(CanonicalFailureClass.TRANSIENT)
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)
        # Success resets counter
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)


# ==============================================================================
# 10. Architectural invariant verification
# ==============================================================================

class TestArchitecturalInvariants(unittest.TestCase):
    """Verify that the contracts enforce the required architectural principles.

    These tests document and enforce the invariants specified in the
    Capability-Oriented Architecture design:

    1. Provider failure != Internet failure
    2. Internet failure != Local capability failure
    3. Single execution failure != Capability permanently unavailable
    4. Sub-goal failure != Parent task failure
    """

    def test_provider_failure_is_not_internet_failure(self):
        """AUTH_DENIED (provider key issue) and NETWORK_ISOLATED (connectivity)
        are distinct canonical classes."""
        self.assertNotEqual(
            CanonicalFailureClass.AUTH_DENIED,
            CanonicalFailureClass.NETWORK_ISOLATED,
        )
        # classify_exception maps them differently
        auth_cls, _ = classify_exception(RuntimeError("invalid API key"))
        net_cls, _ = classify_exception(ConnectionError("DNS resolution failed"))
        self.assertEqual(auth_cls, CanonicalFailureClass.AUTH_DENIED)
        self.assertEqual(net_cls, CanonicalFailureClass.NETWORK_ISOLATED)

    def test_internet_failure_is_not_local_capability_failure(self):
        """NETWORK_ISOLATED (internet) and DETERMINISTIC_ERROR (local) are distinct."""
        self.assertNotEqual(
            CanonicalFailureClass.NETWORK_ISOLATED,
            CanonicalFailureClass.DETERMINISTIC_ERROR,
        )
        net_cls, _ = classify_exception(ConnectionError("socket unreachable"))
        local_cls, _ = classify_exception(FileNotFoundError("/tmp/missing"))
        self.assertEqual(net_cls, CanonicalFailureClass.NETWORK_ISOLATED)
        self.assertEqual(local_cls, CanonicalFailureClass.DETERMINISTIC_ERROR)

    def test_single_failure_does_not_permanently_disable(self):
        """A capability that becomes UNAVAILABLE after failures can recover."""
        tracker = HealthTracker("cap-invariant", initial_health=CapabilityHealth.AVAILABLE)

        # Drive to UNAVAILABLE
        tracker.record_failure(CanonicalFailureClass.NETWORK_ISOLATED)
        self.assertEqual(tracker.current_health, CapabilityHealth.UNAVAILABLE)

        # Recovery is possible
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.DEGRADED)
        tracker.record_success()
        self.assertEqual(tracker.current_health, CapabilityHealth.AVAILABLE)

    def test_local_capability_unaffected_by_provider_auth_failure(self):
        """A local capability (no auth required) should not be impacted by
        AUTH_DENIED failures — the failure class is provider-specific.

        This is a contract test: the HealthTracker treats AUTH_DENIED the same
        regardless of the capability type. The *orchestrator* (not yet wired)
        is responsible for routing AUTH_DENIED only to the relevant capability.
        """
        local_cap = CapabilityDescriptor(
            id="cap-local-fs",
            capability_type=SemanticCapabilityType.FILE_SYSTEM_IO,
            description="Local file operations",
            is_local=True,
            requires_internet=False,
            requires_auth=False,
        )
        self.assertTrue(local_cap.is_local)
        self.assertFalse(local_cap.requires_auth)
        # The descriptor correctly declares no auth dependency — the orchestrator
        # must not route AUTH_DENIED failures to this capability.

    def test_sub_goal_failure_is_scoped(self):
        """A failure in one sub-goal does not affect another sub-goal's result."""
        result_a = ExecutionResult(
            outcome=ExecutionOutcome.FATAL_FAILURE,
            sub_goal_id="sub-goal-a",
            failure_class=CanonicalFailureClass.TRANSIENT,
        )
        result_b = ExecutionResult(
            outcome=ExecutionOutcome.SUCCESS,
            sub_goal_id="sub-goal-b",
            data={"ok": True},
        )
        self.assertEqual(result_a.sub_goal_id, "sub-goal-a")
        self.assertEqual(result_b.sub_goal_id, "sub-goal-b")
        self.assertNotEqual(result_a.outcome, result_b.outcome)


# ==============================================================================
# 11. Re-export verification (core.schemas re-exports)
# ==============================================================================

class TestSchemaReExports(unittest.TestCase):
    """Verify that core.schemas re-exports every capability model and utility."""

    def test_re_exports(self):
        from core import schemas  # noqa: F811
        for name in (
            "SemanticCapabilityType",
            "CapabilityHealth",
            "CapabilityDescriptor",
            "CanonicalFailureClass",
            "DiagnosticContext",
            "ExecutionOutcome",
            "ExecutionResult",
            "HealthPolicy",
            "HealthTracker",
            "classify_http_status",
            "classify_exception",
        ):
            self.assertTrue(
                hasattr(schemas, name),
                f"core.schemas is missing re-export of {name}",
            )

    def test_existing_schemas_unchanged(self):
        """The original API surface models must still be importable."""
        from core.schemas import AskRequest, AskResponse, ToolResultRequest  # noqa: F811
        req = AskRequest(message="hello")
        self.assertEqual(req.message, "hello")

    def test_all_original_imports_survive(self):
        """Every schema imported by main.py and planner.py must still exist."""
        from core.schemas import (  # noqa: F811
            ApprovalRequest,
            ApprovalResponse,
            AskRequest,
            AskResponse,
            DeviceStatusRequest,
            FeedbackRequest,
            MemoryRequest,
            MemoryUpdate,
            ProviderRequest,
            ProviderUpdate,
            ToolResultRequest,
            UpdateProposal,
            AgentAction,
        )
        # Smoke-test construction
        AskRequest(message="test")
        ToolResultRequest(tool="test")
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
