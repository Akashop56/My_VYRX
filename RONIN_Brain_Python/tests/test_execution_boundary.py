"""Unit tests for the Execution Boundary Normalizers (core.execution_boundary).

Covers every boundary adapter:
- ``execute_boundary`` (generic)
- ``execute_llm_boundary`` (LLM completion)
- ``execute_tool_boundary`` (brain tool execution)
- ``execute_search_boundary`` (web search)

Each test group verifies:
- Successful execution normalization
- Transient, network, authentication, deterministic, and validation failure normalization
- Preservation of diagnostic information
- Partial-data preservation where applicable
- Compatibility with existing callers (no behavioral change)
- No unintended capability-wide state mutation (boundaries are registry-free)
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from core.capabilities import (
    CanonicalFailureClass,
    DiagnosticContext,
    ExecutionOutcome,
    ExecutionResult,
    outcome_for_failure,
)
from core.execution_boundary import (
    execute_boundary,
    execute_llm_boundary,
    execute_search_boundary,
    execute_tool_boundary,
)


# ==========================================================================
# Helpers
# ==========================================================================

def _ok():
    return "all good"

def _returns(data):
    return data

def _booms(exc):
    def _inner():
        raise exc
    return _inner


# ==========================================================================
# 1. execute_boundary — generic
# ==========================================================================

class TestGenericBoundarySuccess(unittest.TestCase):
    def test_success_returns_data(self):
        result = execute_boundary(lambda: 42)
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(result.data, 42)
        self.assertIsNone(result.failure_class)
        self.assertIsNone(result.diagnostics)

    def test_success_with_args_and_kwargs(self):
        result = execute_boundary(lambda a, b, c=0: a + b + c, 10, 20, c=5)
        self.assertEqual(result.data, 35)

    def test_success_preserves_complex_data(self):
        data = {"choices": [{"message": {"content": "hello"}}]}
        result = execute_boundary(lambda: data)
        self.assertEqual(result.data, data)

    def test_success_returns_none_data(self):
        result = execute_boundary(lambda: None)
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertIsNone(result.data)

    def test_success_records_elapsed_ms(self):
        result = execute_boundary(lambda: None)
        self.assertIsNotNone(result.elapsed_ms)
        self.assertGreaterEqual(result.elapsed_ms, 0)

    def test_success_preserves_string_data(self):
        result = execute_boundary(lambda: '{"result": "ok"}')
        self.assertEqual(result.data, '{"result": "ok"}')


class TestGenericBoundaryFailure(unittest.TestCase):
    def test_timeout_is_transient_retryable(self):
        result = execute_boundary(_booms(TimeoutError("timed out")))
        self.assertEqual(result.outcome, ExecutionOutcome.RETRYABLE_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.TRANSIENT)
        self.assertIsNotNone(result.diagnostics)
        self.assertEqual(result.diagnostics.raw_error_type, "TimeoutError")
        self.assertIn("timed out", result.diagnostics.raw_message)

    def test_connection_error_is_network_isolated(self):
        result = execute_boundary(_booms(ConnectionError("DNS resolution failed")))
        self.assertEqual(result.outcome, ExecutionOutcome.RETRYABLE_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.NETWORK_ISOLATED)

    def test_auth_error_is_denied(self):
        result = execute_boundary(_booms(PermissionError("invalid API key")))
        self.assertEqual(result.outcome, ExecutionOutcome.DENIED)
        self.assertEqual(result.failure_class, CanonicalFailureClass.AUTH_DENIED)

    def test_file_not_found_is_deterministic(self):
        result = execute_boundary(_booms(FileNotFoundError("/tmp/missing.txt")))
        self.assertEqual(result.outcome, ExecutionOutcome.FATAL_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.DETERMINISTIC_ERROR)

    def test_not_implemented_is_unsupported(self):
        result = execute_boundary(_booms(NotImplementedError("not yet")))
        self.assertEqual(result.outcome, ExecutionOutcome.UNSUPPORTED)
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNSUPPORTED_OPERATION)

    def test_value_error_is_validation(self):
        result = execute_boundary(_booms(ValueError("malformed JSON")))
        self.assertEqual(result.outcome, ExecutionOutcome.FATAL_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.VALIDATION_FAILED)

    def test_generic_runtime_error_is_unknown_fatal(self):
        result = execute_boundary(_booms(RuntimeError("something unexpected")))
        self.assertEqual(result.outcome, ExecutionOutcome.FATAL_FAILURE)
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNKNOWN_FATAL)

    def test_failure_records_elapsed_ms(self):
        result = execute_boundary(_booms(RuntimeError("boom")))
        self.assertIsNotNone(result.elapsed_ms)
        self.assertGreaterEqual(result.elapsed_ms, 0)

    def test_failure_captures_stack_trace(self):
        result = execute_boundary(_booms(ValueError("test error")))
        self.assertIsNotNone(result.diagnostics.stack_trace)
        self.assertIn("ValueError", result.diagnostics.stack_trace)

    def test_failure_preserves_error_type(self):
        result = execute_boundary(_booms(TypeError("bad type")))
        self.assertEqual(result.diagnostics.raw_error_type, "TypeError")

    def test_failure_preserves_raw_message(self):
        result = execute_boundary(_booms(RuntimeError("specific message")))
        self.assertEqual(result.diagnostics.raw_message, "specific message")


class TestGenericBoundaryExceptionContainment(unittest.TestCase):
    """The boundary must NEVER let a raw exception escape (except signals)."""

    def test_no_exception_propagates(self):
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
                result = execute_boundary(_booms(exc))
                self.assertIsNotNone(result)
                self.assertIsNotNone(result.outcome)
                self.assertIsNotNone(result.failure_class)

    def test_keyboard_interrupt_propagates(self):
        with self.assertRaises(KeyboardInterrupt):
            execute_boundary(_booms(KeyboardInterrupt()))

    def test_system_exit_propagates(self):
        with self.assertRaises(SystemExit):
            execute_boundary(_booms(SystemExit(1)))


class TestGenericBoundaryIsolation(unittest.TestCase):
    """The generic boundary must NOT touch the capability registry."""

    def test_no_registry_dependency(self):
        # execute_boundary takes no registry parameter — it's purely
        # a normalization wrapper.
        result = execute_boundary(lambda: "ok")
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        # No capability_id should be set
        self.assertIsNone(result.capability_id)


# ==========================================================================
# 2. execute_llm_boundary
# ==========================================================================

class TestLLMBoundarySuccess(unittest.TestCase):
    @patch("core.execution_boundary._complete")
    def test_success_returns_completion(self, mock_complete):
        completion = {"choices": [{"message": {"content": "hello"}}]}
        mock_complete.return_value = completion
        result = execute_llm_boundary("hi", [], [{"provider": "groq", "api_key": "k"}])
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(result.data, completion)
        mock_complete.assert_called_once()

    @patch("core.execution_boundary._complete")
    def test_success_forwards_kwargs(self, mock_complete):
        mock_complete.return_value = {"choices": []}
        execute_llm_boundary(
            "hi", [], [{"provider": "groq", "api_key": "k"}],
            system_prompt="test", tools=[], on_delta=None,
        )
        _, kwargs = mock_complete.call_args
        self.assertEqual(kwargs["system_prompt"], "test")


class TestLLMBoundaryFailure(unittest.TestCase):
    @patch("core.execution_boundary._complete")
    def test_llm_error_is_classified(self, mock_complete):
        from core.llm_handler import LLMError
        mock_complete.side_effect = LLMError("All configured providers failed: groq: timeout")
        result = execute_llm_boundary("hi", [], [{"provider": "groq", "api_key": "k"}])
        self.assertNotEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertIsNotNone(result.failure_class)
        self.assertIsNotNone(result.diagnostics)
        self.assertIn("All configured providers failed", result.diagnostics.raw_message)

    @patch("core.execution_boundary._complete")
    def test_llm_timeout_classified_as_transient(self, mock_complete):
        from core.llm_handler import LLMError
        mock_complete.side_effect = LLMError("All configured providers failed: groq: timed out")
        result = execute_llm_boundary("hi", [], [{"provider": "groq", "api_key": "k"}])
        self.assertEqual(result.failure_class, CanonicalFailureClass.TRANSIENT)
        self.assertEqual(result.outcome, ExecutionOutcome.RETRYABLE_FAILURE)

    @patch("core.execution_boundary._complete")
    def test_llm_auth_failure_classified(self, mock_complete):
        from core.llm_handler import LLMError
        mock_complete.side_effect = LLMError("All configured providers failed: openai: invalid api key")
        result = execute_llm_boundary("hi", [], [{"provider": "openai", "api_key": "k"}])
        self.assertEqual(result.failure_class, CanonicalFailureClass.AUTH_DENIED)
        self.assertEqual(result.outcome, ExecutionOutcome.DENIED)

    @patch("core.execution_boundary._complete")
    def test_llm_connection_error_classified(self, mock_complete):
        mock_complete.side_effect = ConnectionError("connection refused")
        result = execute_llm_boundary("hi", [], [{"provider": "groq", "api_key": "k"}])
        self.assertEqual(result.failure_class, CanonicalFailureClass.NETWORK_ISOLATED)

    @patch("core.execution_boundary._complete")
    def test_llm_unknown_error_classified(self, mock_complete):
        mock_complete.side_effect = RuntimeError("something completely unexpected")
        result = execute_llm_boundary("hi", [], [{"provider": "groq", "api_key": "k"}])
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNKNOWN_FATAL)

    @patch("core.execution_boundary._complete")
    def test_llm_failure_preserves_stack_trace(self, mock_complete):
        mock_complete.side_effect = ValueError("bad input")
        result = execute_llm_boundary("hi", [], [{"provider": "groq", "api_key": "k"}])
        self.assertIsNotNone(result.diagnostics.stack_trace)
        self.assertIn("ValueError", result.diagnostics.stack_trace)

    @patch("core.execution_boundary._complete")
    def test_llm_keyboard_interrupt_propagates(self, mock_complete):
        mock_complete.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            execute_llm_boundary("hi", [], [])


# ==========================================================================
# 3. execute_tool_boundary
# ==========================================================================

class TestToolBoundarySuccess(unittest.TestCase):
    @patch("core.execution_boundary._execute_tool")
    def test_success_returns_result(self, mock_exec):
        mock_exec.return_value = '{"result": "ok", "files": ["a.py"]}'
        result = execute_tool_boundary("list_files", {"path": "/tmp"})
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(result.data, '{"result": "ok", "files": ["a.py"]}')

    @patch("core.execution_boundary._execute_tool")
    def test_success_with_complex_json(self, mock_exec):
        data = json.dumps({"results": [{"title": "Test", "snippet": "A test result"}]})
        mock_exec.return_value = data
        result = execute_tool_boundary("search", {"query": "test"})
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(result.data, data)

    @patch("core.execution_boundary._execute_tool")
    def test_success_records_elapsed_ms(self, mock_exec):
        mock_exec.return_value = '{"ok": true}'
        result = execute_tool_boundary("read_file", {"path": "/tmp/f"})
        self.assertIsNotNone(result.elapsed_ms)
        self.assertGreaterEqual(result.elapsed_ms, 0)


class TestToolBoundaryErrorDetection(unittest.TestCase):
    """execute_tool() never raises — it returns {"error": "..."} JSON."""

    @patch("core.execution_boundary._execute_tool")
    def test_tool_not_found_classified_as_unsupported(self, mock_exec):
        mock_exec.return_value = json.dumps({"error": "Tool not found: nonexistent_tool"})
        result = execute_tool_boundary("nonexistent_tool", {})
        self.assertEqual(result.outcome, ExecutionOutcome.UNSUPPORTED)
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNSUPPORTED_OPERATION)
        # Data is preserved for the caller
        self.assertIsNotNone(result.data)
        self.assertIn("Tool not found", result.data)

    @patch("core.execution_boundary._execute_tool")
    def test_bad_arguments_classified_as_validation(self, mock_exec):
        mock_exec.return_value = json.dumps({"error": "Tool arguments must be a JSON object."})
        result = execute_tool_boundary("search", "not a dict")
        self.assertEqual(result.failure_class, CanonicalFailureClass.VALIDATION_FAILED)
        self.assertEqual(result.outcome, ExecutionOutcome.FATAL_FAILURE)

    @patch("core.execution_boundary._execute_tool")
    def test_execution_failure_with_not_found_is_deterministic(self, mock_exec):
        mock_exec.return_value = json.dumps({"error": "Tool execution failed: file not found: /tmp/missing"})
        result = execute_tool_boundary("read_file", {"path": "/tmp/missing"})
        # "not found" in the message → DETERMINISTIC_ERROR
        self.assertEqual(result.failure_class, CanonicalFailureClass.DETERMINISTIC_ERROR)

    @patch("core.execution_boundary._execute_tool")
    def test_execution_failure_with_unknown_error_is_fatal(self, mock_exec):
        # When the error message doesn't match any known pattern,
        # the classifier falls back to UNKNOWN_FATAL.
        mock_exec.return_value = json.dumps({"error": "Tool execution failed: something weird happened"})
        result = execute_tool_boundary("read_file", {"path": "/tmp/missing"})
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNKNOWN_FATAL)

    @patch("core.execution_boundary._execute_tool")
    def test_timeout_in_tool_failure_is_transient(self, mock_exec):
        mock_exec.return_value = json.dumps({"error": "Tool execution failed: timed out waiting for response"})
        result = execute_tool_boundary("web_search", {"query": "test"})
        self.assertEqual(result.failure_class, CanonicalFailureClass.TRANSIENT)
        self.assertEqual(result.outcome, ExecutionOutcome.RETRYABLE_FAILURE)

    @patch("core.execution_boundary._execute_tool")
    def test_raw_json_preserved_on_error(self, mock_exec):
        error_json = json.dumps({"error": "Tool execution failed: connection refused"})
        mock_exec.return_value = error_json
        result = execute_tool_boundary("search", {"query": "test"})
        # Data preserves the raw JSON for callers that need it
        self.assertEqual(result.data, error_json)

    @patch("core.execution_boundary._execute_tool")
    def test_error_in_non_json_string_handled(self, mock_exec):
        """If the tool returns a non-JSON string containing "error", handle gracefully."""
        mock_exec.return_value = 'some error text with "error" in it'
        result = execute_tool_boundary("broken_tool", {})
        self.assertNotEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertIsNotNone(result.failure_class)

    @patch("core.execution_boundary._execute_tool")
    def test_diagnostics_populated_on_error(self, mock_exec):
        mock_exec.return_value = json.dumps({"error": "Tool not found: missing"})
        result = execute_tool_boundary("missing", {})
        self.assertIsNotNone(result.diagnostics)
        self.assertIsNotNone(result.diagnostics.raw_message)
        self.assertIn("Tool not found", result.diagnostics.raw_message)


class TestToolBoundaryExceptionContainment(unittest.TestCase):
    """Defensive: even if execute_tool somehow raises, the boundary catches it."""

    @patch("core.execution_boundary._execute_tool")
    def test_unexpected_exception_caught(self, mock_exec):
        mock_exec.side_effect = RuntimeError("unexpected blow-up")
        result = execute_tool_boundary("broken", {})
        self.assertNotEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNKNOWN_FATAL)

    @patch("core.execution_boundary._execute_tool")
    def test_keyboard_interrupt_propagates_from_tool(self, mock_exec):
        mock_exec.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            execute_tool_boundary("any", {})


# ==========================================================================
# 4. execute_search_boundary
# ==========================================================================

class TestSearchBoundarySuccess(unittest.TestCase):
    @patch("tools.web_search.search")
    def test_success_returns_results(self, mock_search):
        results = {"query": "test", "results": [{"title": "T", "snippet": "S"}]}
        mock_search.return_value = results
        result = execute_search_boundary("test")
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(result.data, results)

    @patch("tools.web_search.search")
    def test_success_forwards_kwargs(self, mock_search):
        mock_search.return_value = {"query": "q", "results": []}
        execute_search_boundary("q", limit=3)
        mock_search.assert_called_once_with("q", limit=3)

    @patch("tools.web_search.search")
    def test_success_records_elapsed_ms(self, mock_search):
        mock_search.return_value = {"query": "q", "results": []}
        result = execute_search_boundary("q")
        self.assertIsNotNone(result.elapsed_ms)
        self.assertGreaterEqual(result.elapsed_ms, 0)


class TestSearchBoundaryFailure(unittest.TestCase):
    @patch("tools.web_search.search")
    def test_connection_error_is_network_isolated(self, mock_search):
        mock_search.side_effect = ConnectionError("connection refused")
        result = execute_search_boundary("test")
        self.assertEqual(result.failure_class, CanonicalFailureClass.NETWORK_ISOLATED)
        self.assertEqual(result.outcome, ExecutionOutcome.RETRYABLE_FAILURE)

    @patch("tools.web_search.search")
    def test_timeout_is_transient(self, mock_search):
        mock_search.side_effect = TimeoutError("timed out")
        result = execute_search_boundary("test")
        self.assertEqual(result.failure_class, CanonicalFailureClass.TRANSIENT)
        self.assertEqual(result.outcome, ExecutionOutcome.RETRYABLE_FAILURE)

    @patch("tools.web_search.search")
    def test_value_error_is_validation(self, mock_search):
        mock_search.side_effect = ValueError("invalid response")
        result = execute_search_boundary("test")
        self.assertEqual(result.failure_class, CanonicalFailureClass.VALIDATION_FAILED)

    @patch("tools.web_search.search")
    def test_generic_error_is_unknown_fatal(self, mock_search):
        mock_search.side_effect = RuntimeError("unexpected")
        result = execute_search_boundary("test")
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNKNOWN_FATAL)

    @patch("tools.web_search.search")
    def test_failure_preserves_diagnostics(self, mock_search):
        mock_search.side_effect = ConnectionError("DNS resolution failed")
        result = execute_search_boundary("test")
        self.assertIsNotNone(result.diagnostics)
        self.assertEqual(result.diagnostics.raw_error_type, "ConnectionError")
        self.assertIn("DNS resolution failed", result.diagnostics.raw_message)

    @patch("tools.web_search.search")
    def test_keyboard_interrupt_propagates(self, mock_search):
        mock_search.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            execute_search_boundary("test")

    @patch("tools.web_search.search")
    def test_http_error_status_classification(self, mock_search):
        """requests.HTTPError with a status code should be classified via the status."""
        import requests
        mock_response = MagicMock()
        mock_response.status_code = 429
        http_err = requests.HTTPError("Too Many Requests", response=mock_response)
        mock_search.side_effect = http_err
        result = execute_search_boundary("test")
        self.assertEqual(result.failure_class, CanonicalFailureClass.TRANSIENT)
        self.assertEqual(result.diagnostics.http_status, 429)


# ==========================================================================
# 5. outcome_for_failure — shared mapping
# ==========================================================================

class TestOutcomeForFailure(unittest.TestCase):
    def test_all_failure_classes_have_outcomes(self):
        for fc in CanonicalFailureClass:
            outcome = outcome_for_failure(fc)
            self.assertIsInstance(outcome, ExecutionOutcome, f"{fc.name} maps to non-ExecutionOutcome")

    def test_specific_mappings(self):
        self.assertEqual(outcome_for_failure(CanonicalFailureClass.TRANSIENT), ExecutionOutcome.RETRYABLE_FAILURE)
        self.assertEqual(outcome_for_failure(CanonicalFailureClass.NETWORK_ISOLATED), ExecutionOutcome.RETRYABLE_FAILURE)
        self.assertEqual(outcome_for_failure(CanonicalFailureClass.AUTH_DENIED), ExecutionOutcome.DENIED)
        self.assertEqual(outcome_for_failure(CanonicalFailureClass.POLICY_BLOCKED), ExecutionOutcome.BLOCKED)
        self.assertEqual(outcome_for_failure(CanonicalFailureClass.VALIDATION_FAILED), ExecutionOutcome.FATAL_FAILURE)
        self.assertEqual(outcome_for_failure(CanonicalFailureClass.DETERMINISTIC_ERROR), ExecutionOutcome.FATAL_FAILURE)
        self.assertEqual(outcome_for_failure(CanonicalFailureClass.UNSUPPORTED_OPERATION), ExecutionOutcome.UNSUPPORTED)
        self.assertEqual(outcome_for_failure(CanonicalFailureClass.UNKNOWN_FATAL), ExecutionOutcome.FATAL_FAILURE)


# ==========================================================================
# 6. Compatibility — boundary results are compatible with existing callers
# ==========================================================================

class TestCallerCompatibility(unittest.TestCase):
    """Verify that boundary results can be consumed the same way existing
    callers expect to consume raw results."""

    def test_success_data_can_be_used_as_dict(self):
        """Existing callers do: completion.get('choices')"""
        completion = {"choices": [{"message": {"content": "hello"}}]}
        result = execute_boundary(lambda: completion)
        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        # Caller can still do:
        choices = result.data.get("choices", [])
        self.assertEqual(len(choices), 1)

    def test_tool_result_string_can_be_checked_for_error(self):
        """Existing callers do: '\"error\"' not in result_text[:160]"""
        success_text = '{"result": "ok"}'
        result = execute_boundary(lambda: success_text)
        self.assertNotIn('"error"', result.data[:160])

    def test_failure_result_can_be_logged(self):
        """Existing callers log: f'Error: {result.diagnostics.raw_message}'"""
        result = execute_boundary(_booms(RuntimeError("something broke")))
        self.assertIn("something broke", result.diagnostics.raw_message)

    def test_boundary_result_is_pydantic_model(self):
        """ExecutionResult is a Pydantic BaseModel — can be serialized."""
        result = execute_boundary(lambda: "ok")
        d = result.model_dump()
        self.assertIn("outcome", d)
        self.assertEqual(d["outcome"], "success")


# ==========================================================================
# 7. No capability-wide state mutation
# ==========================================================================

class TestNoStateMutation(unittest.TestCase):
    """Boundaries must not mutate any global or shared state."""

    def test_execute_boundary_has_no_side_effects(self):
        """Running execute_boundary twice with the same input produces identical results."""
        r1 = execute_boundary(lambda: 42)
        r2 = execute_boundary(lambda: 42)
        self.assertEqual(r1.outcome, r2.outcome)
        self.assertEqual(r1.data, r2.data)

    def test_execute_tool_boundary_has_no_side_effects(self):
        with patch("core.execution_boundary._execute_tool", return_value='{"ok": true}'):
            r1 = execute_tool_boundary("t", {})
            r2 = execute_tool_boundary("t", {})
            self.assertEqual(r1.outcome, r2.outcome)
            self.assertEqual(r1.data, r2.data)

    def test_failure_does_not_pollute_subsequent_calls(self):
        """A failure in one call must not affect a subsequent success."""
        call_count = 0
        def alternating():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("timed out")
            return "ok"

        r1 = execute_boundary(alternating)
        self.assertEqual(r1.outcome, ExecutionOutcome.RETRYABLE_FAILURE)

        r2 = execute_boundary(alternating)
        self.assertEqual(r2.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(r2.data, "ok")


if __name__ == "__main__":
    unittest.main()
