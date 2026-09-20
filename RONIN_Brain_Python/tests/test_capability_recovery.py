"""Behavioral fault-injection tests for Phase 11 capability recovery."""
from __future__ import annotations

import unittest

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    DiagnosticContext,
    ExecutionOutcome,
    ExecutionResult,
    SemanticCapabilityType,
)
from core.planner import (
    MAX_CAPABILITY_RECOVERY_ATTEMPTS,
    _tool_execution_observation,
    build_capability_plan,
    recover_failed_subgoal,
)
from core.registry import CapabilityRegistry


def _descriptor(
    capability_id: str,
    capability_type: SemanticCapabilityType,
    *,
    requires_internet: bool = False,
    is_local: bool = True,
    metadata: dict | None = None,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=capability_id,
        capability_type=capability_type,
        description=f"Fault-injection implementation {capability_id}",
        requires_internet=requires_internet,
        is_local=is_local,
        metadata=metadata or {},
    )


def _available(
    registry: CapabilityRegistry,
    capability_id: str,
    capability_type: SemanticCapabilityType,
    *,
    requires_internet: bool = False,
    is_local: bool = True,
    metadata: dict | None = None,
) -> None:
    registry.register(_descriptor(
        capability_id,
        capability_type,
        requires_internet=requires_internet,
        is_local=is_local,
        metadata=metadata,
    ))
    registry.update_health(capability_id, success=True)


def _failed(capability_id: str, failure_class: CanonicalFailureClass = CanonicalFailureClass.NETWORK_ISOLATED) -> ExecutionResult:
    return ExecutionResult(
        outcome=ExecutionOutcome.RETRYABLE_FAILURE,
        capability_id=capability_id,
        failure_class=failure_class,
        data={"raw": "secret backend diagnostic"},
        diagnostics=DiagnosticContext(raw_message="secret backend diagnostic"),
    )


def _success(capability_id: str) -> ExecutionResult:
    return ExecutionResult(
        outcome=ExecutionOutcome.SUCCESS,
        capability_id=capability_id,
        data={"capability": capability_id, "verified": True},
    )


class AdaptiveCapabilityRecoveryTests(unittest.TestCase):
    def test_same_semantic_alternative_is_selected_and_reexecuted(self):
        registry = CapabilityRegistry()
        _available(
            registry, "web-a", SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True, is_local=False,
        )
        _available(
            registry, "web-b", SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True, is_local=False,
        )
        plan = build_capability_plan("find current external information", registry)
        calls: list[str] = []

        def execute(candidate: CapabilityDescriptor) -> ExecutionResult:
            calls.append(candidate.id)
            return _success(candidate.id)

        recovered, affected, did_recover = recover_failed_subgoal(
            plan,
            registry,
            _failed("web-a"),
            execute_candidate=execute,
        )

        self.assertTrue(did_recover)
        self.assertEqual(affected, "subgoal-web-retrieval")
        self.assertEqual(calls, ["web-b"])
        self.assertEqual(recovered.capability_id, "web-b")
        sub_goal = plan.sub_goal("subgoal-web-retrieval")
        self.assertEqual(sub_goal.attempted_capabilities, ["web-a", "web-b"])
        self.assertEqual(plan.outcomes["subgoal-web-retrieval"], ExecutionOutcome.SUCCESS)

    def test_unrelated_semantic_capability_remains_usable(self):
        registry = CapabilityRegistry()
        _available(
            registry, "web-a", SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True, is_local=False,
        )
        _available(
            registry, "web-b", SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True, is_local=False,
        )
        _available(registry, "files-a", SemanticCapabilityType.FILE_SYSTEM_IO)
        plan = build_capability_plan(
            "read my local project and find current external information", registry,
        )

        recovered, _, did_recover = recover_failed_subgoal(
            plan,
            registry,
            _failed("web-a"),
            execute_candidate=lambda candidate: _success(candidate.id),
        )

        self.assertTrue(did_recover)
        self.assertEqual(recovered.capability_id, "web-b")
        self.assertEqual(plan.sub_goal("subgoal-local-files").candidate_ids, ("files-a",))
        self.assertEqual(plan.sub_goal("subgoal-local-files").attempted_capabilities, [])
        self.assertEqual(registry.get("files-a").health, CapabilityHealth.AVAILABLE)

    def test_exhausted_alternatives_are_bounded_and_sanitized(self):
        registry = CapabilityRegistry()
        for capability_id in ("web-a", "web-b"):
            _available(
                registry, capability_id, SemanticCapabilityType.WEB_RETRIEVAL,
                requires_internet=True, is_local=False,
            )
        plan = build_capability_plan("find current external information", registry)
        calls: list[str] = []

        def execute(candidate: CapabilityDescriptor) -> ExecutionResult:
            calls.append(candidate.id)
            return _failed(candidate.id)

        result, affected, did_recover = recover_failed_subgoal(
            plan,
            registry,
            _failed("web-a"),
            tool_name="search",
            execute_candidate=execute,
        )
        observation, ok = _tool_execution_observation("search", result)

        self.assertFalse(did_recover)
        self.assertEqual(affected, "subgoal-web-retrieval")
        self.assertEqual(calls, ["web-b"])
        self.assertLessEqual(len(calls), MAX_CAPABILITY_RECOVERY_ATTEMPTS)
        self.assertEqual(plan.sub_goal("subgoal-web-retrieval").attempted_capabilities, ["web-a", "web-b"])
        self.assertFalse(ok)
        self.assertNotIn("secret backend diagnostic", observation)
        self.assertNotIn("raw", observation)
        self.assertIn("NETWORK_ISOLATED", observation)

    def test_recovery_attempt_bound_does_not_try_a_third_alternative(self):
        registry = CapabilityRegistry()
        for capability_id in ("web-a", "web-b", "web-c"):
            _available(
                registry, capability_id, SemanticCapabilityType.WEB_RETRIEVAL,
                requires_internet=True, is_local=False,
            )
        plan = build_capability_plan("find current external information", registry)
        calls: list[str] = []

        result, _, did_recover = recover_failed_subgoal(
            plan,
            registry,
            _failed("web-a"),
            execute_candidate=lambda candidate: calls.append(candidate.id) or _failed(candidate.id),
        )

        self.assertFalse(did_recover)
        self.assertEqual(calls, ["web-b", "web-c"])
        self.assertEqual(len(calls), MAX_CAPABILITY_RECOVERY_ATTEMPTS)
        self.assertEqual(result.capability_id, "web-c")

    def test_partial_parent_task_preserves_success_and_recovers_only_web_subgoal(self):
        registry = CapabilityRegistry()
        _available(registry, "files-a", SemanticCapabilityType.FILE_SYSTEM_IO)
        _available(
            registry, "web-a", SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True, is_local=False,
        )
        _available(
            registry, "web-b", SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True, is_local=False,
        )
        _available(registry, "synthesis-a", SemanticCapabilityType.SYNTHESIS)
        plan = build_capability_plan(
            "read my local project and compare it with current external information",
            registry,
        )
        plan.record_outcome(
            "subgoal-local-files",
            _success("files-a"),
        )

        recovered, affected, did_recover = recover_failed_subgoal(
            plan,
            registry,
            _failed("web-a"),
            execute_candidate=lambda candidate: _success(candidate.id),
        )
        plan.record_outcome("subgoal-synthesis", _success("synthesis-a"))

        self.assertTrue(did_recover)
        self.assertEqual(affected, "subgoal-web-retrieval")
        self.assertEqual(recovered.capability_id, "web-b")
        self.assertEqual(plan.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(plan.sub_goal("subgoal-local-files").attempted_capabilities, ["files-a"])
        self.assertEqual(plan.sub_goal("subgoal-web-retrieval").attempted_capabilities, ["web-a", "web-b"])
        self.assertEqual(plan.sub_goal("subgoal-synthesis").attempted_capabilities, ["synthesis-a"])

    def test_reasoning_failure_does_not_blacklist_web_retrieval(self):
        registry = CapabilityRegistry()
        _available(
            registry, "reasoning-a", SemanticCapabilityType.REASONING,
            requires_internet=True, is_local=False,
        )
        _available(
            registry, "reasoning-b", SemanticCapabilityType.REASONING,
            requires_internet=True, is_local=False,
        )
        _available(
            registry, "web-a", SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True, is_local=False,
        )
        plan = build_capability_plan("find current external information", registry)

        recovered, _, did_recover = recover_failed_subgoal(
            plan,
            registry,
            _failed("reasoning-a"),
            execute_candidate=lambda candidate: _success(candidate.id),
        )

        self.assertTrue(did_recover)
        self.assertEqual(recovered.capability_id, "reasoning-b")
        self.assertEqual(plan.sub_goal("subgoal-web-retrieval").candidate_ids, ("web-a",))
        self.assertEqual(plan.sub_goal("subgoal-web-retrieval").attempted_capabilities, [])
        self.assertEqual(registry.get("web-a").health, CapabilityHealth.AVAILABLE)

    def test_unknown_alternative_is_never_selected(self):
        registry = CapabilityRegistry()
        _available(
            registry, "web-a", SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True, is_local=False,
        )
        registry.register(_descriptor(
            "web-unknown", SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True, is_local=False,
        ))
        plan = build_capability_plan("find current external information", registry)
        calls: list[str] = []

        result, _, did_recover = recover_failed_subgoal(
            plan,
            registry,
            _failed("web-a"),
            execute_candidate=lambda candidate: calls.append(candidate.id) or _success(candidate.id),
        )

        self.assertFalse(did_recover)
        self.assertEqual(calls, [])
        self.assertEqual(result.capability_id, "web-a")
        self.assertEqual(plan.sub_goal("subgoal-web-retrieval").unknown_candidate_ids, ("web-unknown",))
        self.assertEqual(plan.sub_goal("subgoal-web-retrieval").attempted_capabilities, ["web-a"])


if __name__ == "__main__":
    unittest.main()
