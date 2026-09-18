"""Focused tests for planner-side semantic capability planning."""
from __future__ import annotations

import unittest

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    ExecutionOutcome,
    SemanticCapabilityType,
)
from core.planner import (
    MAX_AGENT_STEPS,
    build_capability_plan,
    identify_required_capabilities,
    query_capability_candidates,
    replan_affected_subgoal,
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


class CapabilityAwarePlannerTests(unittest.TestCase):
    def test_identifies_semantic_requirements_without_provider_names(self):
        requirements = identify_required_capabilities(
            "Read my local project and compare it with current external information"
        )
        types = {requirement.capability_type for requirement in requirements}
        self.assertIn(SemanticCapabilityType.REASONING, types)
        self.assertIn(SemanticCapabilityType.FILE_SYSTEM_IO, types)
        self.assertIn(SemanticCapabilityType.WEB_RETRIEVAL, types)
        self.assertIn(SemanticCapabilityType.SYNTHESIS, types)
        self.assertTrue(all("groq" not in item.description.casefold() for item in requirements))

    def test_queries_multiple_implementations_and_keeps_identity_separate(self):
        registry = CapabilityRegistry()
        registry.register(descriptor("reasoning-a", SemanticCapabilityType.REASONING))
        registry.register(descriptor("reasoning-b", SemanticCapabilityType.REASONING))
        registry.update_health("reasoning-a", success=True)
        registry.update_health("reasoning-b", success=True)
        requirement = next(
            item for item in identify_required_capabilities("hello")
            if item.capability_type == SemanticCapabilityType.REASONING
        )

        candidates = query_capability_candidates(registry, requirement)

        self.assertEqual({candidate.id for candidate in candidates}, {"reasoning-a", "reasoning-b"})
        self.assertTrue(all(candidate.capability_type == SemanticCapabilityType.REASONING
                            for candidate in candidates))

    def test_unavailable_capabilities_are_excluded(self):
        registry = CapabilityRegistry()
        registry.register(descriptor("reasoning-good", SemanticCapabilityType.REASONING))
        registry.register(descriptor("reasoning-bad", SemanticCapabilityType.REASONING))
        registry.update_health("reasoning-good", success=True)
        registry.update_health(
            "reasoning-bad",
            success=False,
            failure_class=CanonicalFailureClass.AUTH_DENIED,
        )
        requirement = next(iter(identify_required_capabilities("hello")))

        candidates = query_capability_candidates(registry, requirement)

        self.assertEqual([candidate.id for candidate in candidates], ["reasoning-good"])
        self.assertEqual(registry.get("reasoning-bad").health, CapabilityHealth.UNAVAILABLE)

    def test_provider_failure_does_not_disable_web_retrieval(self):
        registry = CapabilityRegistry()
        registry.register(descriptor(
            "reasoning-remote",
            SemanticCapabilityType.REASONING,
            requires_internet=True,
            is_local=False,
        ))
        registry.register(descriptor(
            "web-search-a",
            SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True,
            is_local=False,
        ))
        registry.update_health(
            "reasoning-remote",
            success=False,
            failure_class=CanonicalFailureClass.AUTH_DENIED,
        )
        registry.update_health("web-search-a", success=True)

        plan = build_capability_plan("find current external information", registry)
        reasoning = plan.sub_goal("subgoal-reasoning")
        web = plan.sub_goal("subgoal-web-retrieval")

        self.assertFalse(reasoning.available)
        self.assertEqual(web.candidate_ids, ("web-search-a",))

    def test_internet_failure_does_not_disable_local_capability(self):
        registry = CapabilityRegistry()
        registry.register(descriptor(
            "web-search-a",
            SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True,
            is_local=False,
        ))
        registry.register(descriptor(
            "local-files-a",
            SemanticCapabilityType.FILE_SYSTEM_IO,
            is_local=True,
        ))
        registry.update_health(
            "web-search-a",
            success=False,
            failure_class=CanonicalFailureClass.NETWORK_ISOLATED,
        )
        registry.update_health("local-files-a", success=True)

        plan = build_capability_plan("read my local project and find current information", registry)

        self.assertFalse(plan.sub_goal("subgoal-web-retrieval").available)
        self.assertEqual(plan.sub_goal("subgoal-local-files").candidate_ids, ("local-files-a",))

    def test_one_failed_subgoal_leaves_independent_subgoal_usable(self):
        registry = CapabilityRegistry()
        registry.register(descriptor(
            "web-search-a",
            SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True,
            is_local=False,
        ))
        registry.register(descriptor("local-files-a", SemanticCapabilityType.FILE_SYSTEM_IO))
        plan = build_capability_plan("read my local project and find current information", registry)

        plan.record_outcome("subgoal-web-retrieval", ExecutionResultForTest.fatal("web-search-a"))
        plan.record_outcome("subgoal-local-files", ExecutionResultForTest.success("local-files-a"))

        self.assertEqual(plan.outcome, ExecutionOutcome.PARTIAL_SUCCESS)
        self.assertEqual(plan.outcomes["subgoal-local-files"], ExecutionOutcome.SUCCESS)

    def test_failed_subgoal_can_be_reconsidered_without_replaying_parent(self):
        registry = CapabilityRegistry()
        registry.register(descriptor(
            "web-search-a",
            SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True,
            is_local=False,
        ))
        registry.register(descriptor(
            "web-search-b",
            SemanticCapabilityType.WEB_RETRIEVAL,
            requires_internet=True,
            is_local=False,
        ))
        registry.update_health("web-search-a", success=True)
        registry.update_health("web-search-b", success=True)
        plan = build_capability_plan("find current information", registry)
        failed = ExecutionResultForTest.fatal("web-search-a")
        registry.update_health(
            "web-search-a",
            success=False,
            failure_class=CanonicalFailureClass.NETWORK_ISOLATED,
        )

        replanned, affected = replan_affected_subgoal(
            plan,
            registry,
            failed,
        )

        self.assertIs(replanned, plan)
        self.assertEqual(affected, "subgoal-web-retrieval")
        self.assertEqual(plan.sub_goal("subgoal-web-retrieval").candidate_ids, ("web-search-b",))
        self.assertNotIn("subgoal-reasoning", plan.outcomes)

    def test_partial_success_remains_representable(self):
        registry = CapabilityRegistry()
        registry.register(descriptor("local-files-a", SemanticCapabilityType.FILE_SYSTEM_IO))
        plan = build_capability_plan("read my local project", registry)
        plan.record_outcome(
            "subgoal-local-files",
            ExecutionResultForTest.partial("local-files-a"),
        )

        self.assertEqual(plan.outcome, ExecutionOutcome.PARTIAL_SUCCESS)

    def test_existing_react_step_limit_is_unchanged(self):
        self.assertEqual(MAX_AGENT_STEPS, 8)


class ExecutionResultForTest:
    """Small factory kept local to tests to make outcome intent explicit."""

    @staticmethod
    def success(capability_id: str):
        from core.capabilities import ExecutionResult
        return ExecutionResult(
            outcome=ExecutionOutcome.SUCCESS,
            capability_id=capability_id,
        )

    @staticmethod
    def partial(capability_id: str):
        from core.capabilities import ExecutionResult
        return ExecutionResult(
            outcome=ExecutionOutcome.PARTIAL_SUCCESS,
            capability_id=capability_id,
            partial_data={"verified": True},
        )

    @staticmethod
    def fatal(capability_id: str):
        from core.capabilities import ExecutionResult
        return ExecutionResult(
            outcome=ExecutionOutcome.FATAL_FAILURE,
            capability_id=capability_id,
            failure_class=CanonicalFailureClass.NETWORK_ISOLATED,
        )


if __name__ == "__main__":
    unittest.main()
