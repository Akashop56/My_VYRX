"""Deterministic Phase 12 tests for the optional local reasoning boundary."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityDescriptor,
    CapabilityHealth,
    ExecutionOutcome,
    SemanticCapabilityType,
)
from core.capability_lifecycle import (
    LOCAL_REASONING_CAPABILITY_ID,
    bootstrap_application_capabilities,
    establish_local_reasoning,
)
from core.execution_boundary import execute_llm_boundary
from core.local_reasoning import LocalReasoningAdapter
from core.planner import build_capability_plan
from core.registry import CapabilityRegistry


_RUNTIME = textwrap.dedent(
    """
    import json
    import sys
    import time

    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    if "--health" in sys.argv:
        if mode == "health-fail":
            print(json.dumps({"ready": False, "reason": "model unavailable"}))
            raise SystemExit(1)
        print(json.dumps({"ready": True}))
        raise SystemExit(0)
    if mode == "timeout":
        time.sleep(2)
    if mode == "oom":
        print("out of memory", file=sys.stderr)
        raise SystemExit(1)
    if mode == "crash":
        raise SystemExit(7)
    if mode == "malformed":
        print("{not-json", end="")
        raise SystemExit(0)
    request = json.load(sys.stdin)
    print(json.dumps({"text": "local answer", "received": request["messages"]}))
    """
)


class LocalReasoningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tempdir.name) / "runtime.py"
        self.runtime.write_text(_RUNTIME, encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def adapter(self, mode: str = "ok", **kwargs) -> LocalReasoningAdapter:
        return LocalReasoningAdapter(
            (sys.executable, str(self.runtime), mode),
            **kwargs,
        )

    def test_missing_runtime_is_registered_unavailable_without_download(self):
        registry = CapabilityRegistry()
        adapter = LocalReasoningAdapter((str(Path(self.tempdir.name) / "missing-runtime"),))

        establish_local_reasoning(registry, adapter)

        descriptor = registry.get(LOCAL_REASONING_CAPABILITY_ID)
        self.assertIsNotNone(descriptor)
        self.assertEqual(descriptor.capability_type, SemanticCapabilityType.REASONING)
        self.assertTrue(descriptor.is_local)
        self.assertFalse(descriptor.requires_internet)
        self.assertFalse(descriptor.requires_auth)
        self.assertEqual(descriptor.health, CapabilityHealth.UNAVAILABLE)
        self.assertIn("not available", descriptor.metadata["readiness_reason"])
        self.assertFalse(list(Path(self.tempdir.name).glob("*.gguf")))

    def test_readiness_is_distinct_from_registration(self):
        registry = CapabilityRegistry()
        adapter = self.adapter("health-fail")

        establish_local_reasoning(registry, adapter)

        self.assertEqual(registry.get(LOCAL_REASONING_CAPABILITY_ID).health,
                         CapabilityHealth.UNAVAILABLE)
        self.assertEqual(adapter.readiness.health, CapabilityHealth.UNAVAILABLE)
        self.assertIn("not ready", adapter.readiness.reason)

    def test_output_is_normalized_by_existing_llm_boundary(self):
        adapter = self.adapter()
        deltas: list[str] = []

        result = execute_llm_boundary(
            "hello", [], [],
            _completion_callable=adapter.complete,
            on_delta=deltas.append,
        )

        self.assertEqual(result.outcome, ExecutionOutcome.SUCCESS)
        self.assertEqual(result.data["choices"][0]["message"]["content"], "local answer")
        self.assertEqual(deltas, ["local answer"])

    def test_timeout_oom_crash_and_malformed_output_use_canonical_classes(self):
        cases = (
            ("timeout", CanonicalFailureClass.TRANSIENT),
            ("oom", CanonicalFailureClass.UNKNOWN_FATAL),
            ("crash", CanonicalFailureClass.UNKNOWN_FATAL),
            ("malformed", CanonicalFailureClass.VALIDATION_FAILED),
        )
        for mode, expected in cases:
            with self.subTest(mode=mode):
                result = execute_llm_boundary(
                    "hello", [], [],
                    _completion_callable=self.adapter(
                        mode, timeout_seconds=1.0,
                    ).complete,
                )
                self.assertEqual(result.failure_class, expected)
                self.assertNotIn("out of memory", str(result.data or ""))

    def test_local_is_an_ordinary_reasoning_candidate_and_keeps_other_domains(self):
        registry = CapabilityRegistry()
        provider_manager = SimpleNamespace(list_providers=lambda: [
            {"name": "remote", "model": "test"},
        ])
        adapter = self.adapter()
        bootstrap_application_capabilities(
            registry,
            provider_manager,
            available_tools=[{
                "function": {"name": "read_file", "description": "Read files"},
            }],
            local_reasoning=adapter,
        )

        local = registry.get(LOCAL_REASONING_CAPABILITY_ID)
        remote = registry.get("provider-remote")
        self.assertEqual(local.health, CapabilityHealth.AVAILABLE)
        self.assertEqual(remote.capability_type, SemanticCapabilityType.REASONING)
        self.assertNotEqual(local.id, local.capability_type.value)
        self.assertFalse(local.requires_internet)
        self.assertTrue(remote.requires_internet)

        plan = build_capability_plan("search current files", registry)
        self.assertEqual(
            plan.sub_goal("subgoal-reasoning").selected_capability_id,
            LOCAL_REASONING_CAPABILITY_ID,
        )
        self.assertIsNotNone(plan.sub_goal("subgoal-web-retrieval"))
        self.assertIsNotNone(plan.sub_goal("subgoal-local-files"))

    def test_close_blocks_future_execution(self):
        adapter = self.adapter()
        adapter.close()
        result = execute_llm_boundary(
            "hello", [], [], _completion_callable=adapter.complete,
        )
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNSUPPORTED_OPERATION)


if __name__ == "__main__":
    unittest.main()
