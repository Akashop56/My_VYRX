"""Focused Phase 10 integration tests for the bounded knowledge wiring."""
from __future__ import annotations

import asyncio
import inspect
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityHealth,
    SemanticCapabilityType,
)
from core.capability_lifecycle import bootstrap_application_capabilities
from core.execution_boundary import execute_tool_boundary
from core.knowledge.engine import KnowledgeEngine
from core.knowledge.parsers import ParsedUnit, ParserRegistry
from core.knowledge.integration import (
    KNOWLEDGE_CAPABILITY_ID,
    KNOWLEDGE_TOOL_NAME,
    establish_knowledge_health,
    execute_knowledge_search,
    knowledge_capability_descriptor,
    knowledge_tool_schema,
    ingestion_report_dict,
    scan_report_dict,
)
from core.planner import (
    BrainContext,
    _execute_tool_request,
    _knowledge_tool_is_selectable,
    _tool_execution_observation,
    build_capability_plan,
    identify_required_capabilities,
)
from core.registry import CapabilityRegistry


class _ProviderManager:
    def list_providers(self):
        return []


class KnowledgeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.root = self.base / "knowledge"
        self.root.mkdir()
        self.db = self.base / "brain.sqlite"
        self.engine = KnowledgeEngine(self.root, db_path=self.db)
        self.registry = CapabilityRegistry()

    def tearDown(self) -> None:
        self.engine.close()
        self.tempdir.cleanup()

    def _context(self) -> BrainContext:
        # The execution helpers only touch knowledge_engine and the registry;
        # the other fields are deliberately lightweight test doubles.
        return BrainContext(
            log=SimpleNamespace(),
            state=SimpleNamespace(),
            stats=SimpleNamespace(),
            provider_manager=_ProviderManager(),
            memory_engine=SimpleNamespace(),
            knowledge_engine=self.engine,
            capability_registry=self.registry,
        )

    def test_application_lifespan_stops_watcher_before_closing_engine(self):
        import main

        events: list[str] = []

        class FakeEngine:
            root = Path("/tmp/fake-knowledge")

            def __init__(self) -> None:
                self.close_calls = 0

            def scan(self):
                return None

            def ingest(self, scan):
                return None

            def close(self) -> None:
                self.close_calls += 1
                events.append("engine-close")

        class FakeWatcher:
            def __init__(self, engine, **kwargs) -> None:
                self.engine = engine

            def start(self):
                events.append("watcher-start")

            async def stop(self) -> None:
                events.append("watcher-stop")

        engines: list[FakeEngine] = []
        original_engine = main.CTX.knowledge_engine
        original_watcher = main.CTX.knowledge_watcher
        original_initialized = main._KNOWLEDGE_ENGINE_INITIALIZED
        try:
            main.CTX.knowledge_engine = None
            main.CTX.knowledge_watcher = None
            main._KNOWLEDGE_ENGINE_INITIALIZED = False

            def make_engine(**kwargs):
                engine = FakeEngine()
                engines.append(engine)
                return engine

            with patch.object(main, "KnowledgeEngine", side_effect=make_engine), \
                    patch.object(main, "KnowledgeIngestionWatcher", FakeWatcher), \
                    patch.object(main, "initialize_database", new=AsyncMock()), \
                    patch.object(main.MEMORY_ENGINE, "init", new=AsyncMock()), \
                    patch.object(main.STATS, "init", new=AsyncMock()), \
                    patch.object(main, "bootstrap_application_capabilities", return_value=1):
                async def exercise():
                    async with main.lifespan(main.app):
                        pass

                asyncio.run(exercise())

            self.assertEqual(events, ["watcher-start", "watcher-stop", "engine-close"])
            self.assertEqual(len(engines), 1)
            self.assertEqual(engines[0].close_calls, 1)
            self.assertIsNone(main.CTX.knowledge_engine)
        finally:
            main.CTX.knowledge_engine = original_engine
            main.CTX.knowledge_watcher = original_watcher
            main._KNOWLEDGE_ENGINE_INITIALIZED = original_initialized

    def test_lifecycle_registers_one_local_descriptor_and_health(self):
        count = bootstrap_application_capabilities(
            self.registry,
            _ProviderManager(),
            knowledge_engine=self.engine,
        )
        self.assertGreaterEqual(count, 1)
        descriptor = self.registry.get(KNOWLEDGE_CAPABILITY_ID)
        self.assertIsNotNone(descriptor)
        self.assertEqual(descriptor.capability_type, SemanticCapabilityType.LOCAL_KNOWLEDGE_SEARCH)
        self.assertFalse(descriptor.requires_internet)
        self.assertFalse(descriptor.requires_auth)
        self.assertTrue(descriptor.is_local)
        self.assertEqual(descriptor.metadata["engine"], "KnowledgeEngine")
        self.assertEqual(descriptor.metadata["retrieval_method"], "lexical_fts5")
        self.assertEqual(descriptor.health, CapabilityHealth.AVAILABLE)

    def test_registration_is_not_availability_and_unknown_is_not_selectable(self):
        descriptor = knowledge_capability_descriptor(self.engine)
        self.registry.register_if_absent(descriptor)
        self.assertEqual(self.registry.get(KNOWLEDGE_CAPABILITY_ID).health, CapabilityHealth.UNKNOWN)
        requirement = next(
            item for item in identify_required_capabilities("search local knowledge")
            if item.capability_type == SemanticCapabilityType.LOCAL_KNOWLEDGE_SEARCH
        )
        plan = build_capability_plan("search local knowledge", self.registry)
        local_goal = plan.sub_goal(requirement.sub_goal_id)
        self.assertEqual(local_goal.candidate_ids, ())
        self.assertEqual(local_goal.unknown_candidate_ids, (KNOWLEDGE_CAPABILITY_ID,))
        self.assertFalse(_knowledge_tool_is_selectable(self._context()))

    def test_context_bound_search_preserves_metadata_and_empty_is_success(self):
        source = self.root / "guide.md"
        source.write_text("SQLite local marker\nsecond line", encoding="utf-8")
        self.engine.ingest()
        establish_knowledge_health(self.registry, self.engine)
        context = self._context()
        self.assertTrue(_knowledge_tool_is_selectable(context))

        result = _execute_tool_request(KNOWLEDGE_TOOL_NAME, {"query": "SQLite marker"}, context)
        self.assertEqual(result.outcome.value, "success")
        self.assertIn("guide.md", result.data)
        self.assertIn("lines 1-2", result.data)
        self.assertIn("chars 0-31", result.data)

        empty = _execute_tool_request(KNOWLEDGE_TOOL_NAME, {"query": "absent-term"}, context)
        self.assertEqual(empty.outcome.value, "success")
        self.assertIn("No local knowledge matches found", empty.data)

    def test_search_schema_is_read_only_and_ingestion_is_not_a_react_tool(self):
        schema = knowledge_tool_schema()
        self.assertEqual(schema["function"]["name"], KNOWLEDGE_TOOL_NAME)
        self.assertEqual(schema["function"]["parameters"]["required"], ["query"])
        self.assertNotIn("scan", schema["function"]["name"])
        self.assertNotIn("ingest", schema["function"]["name"])
        source = inspect.getsource(_execute_tool_request)
        self.assertNotIn("engine.ingest", source)

    def test_boundary_sanitizes_engine_exception_for_react(self):
        class BrokenEngine:
            def search(self, query, limit=20):
                raise RuntimeError("secret sqlite path /private/db.sqlite and raw stack detail")

        result = execute_tool_boundary(
            KNOWLEDGE_TOOL_NAME,
            {"query": "anything"},
            executor=lambda name, args: execute_knowledge_search(BrokenEngine(), args),
        )
        self.assertNotEqual(result.outcome.value, "success")
        self.assertEqual(result.failure_class, CanonicalFailureClass.UNKNOWN_FATAL)
        observation, ok = _tool_execution_observation(KNOWLEDGE_TOOL_NAME, result)
        self.assertFalse(ok)
        self.assertNotIn("secret sqlite path", observation)
        self.assertNotIn("private/db.sqlite", observation)
        self.assertNotIn("raw stack detail", observation)
        self.assertIn("UNKNOWN_FATAL", observation)

    def test_planner_routes_local_request_to_local_capability(self):
        self.root.joinpath("notes.md").write_text("routing marker", encoding="utf-8")
        self.engine.ingest()
        establish_knowledge_health(self.registry, self.engine)
        plan = build_capability_plan("search my local knowledge base for routing marker", self.registry)
        goal = plan.sub_goal("subgoal-local-knowledge")
        self.assertEqual(goal.selected_capability_id, KNOWLEDGE_CAPABILITY_ID)
        self.assertEqual(goal.candidates[0].capability_type, SemanticCapabilityType.LOCAL_KNOWLEDGE_SEARCH)
        self.assertTrue(_knowledge_tool_is_selectable(self._context()))

    def test_report_serialization_is_deterministic_and_path_safe(self):
        source = self.root / "one.md"
        source.write_text("report marker", encoding="utf-8")
        scan = self.engine.scan()
        first_scan = scan_report_dict(scan)
        second_scan = scan_report_dict(scan)
        self.assertEqual(first_scan, second_scan)
        self.assertNotIn("absolute_path", first_scan["observations"][0])
        ingestion = self.engine.ingest(scan)
        self.assertEqual(ingestion_report_dict(ingestion), ingestion_report_dict(ingestion))

    def test_reader_remains_usable_during_real_background_writer(self):
        class BlockingParser:
            name = "blocking-test"

            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def parse(self, path: Path, *, read_size: int = 65536):
                self.started.set()
                self.release.wait(timeout=2)
                yield ParsedUnit("real reader writer overlap", 0, 26, 1, 1)

        async def exercise() -> None:
            source = self.root / "overlap.md"
            source.write_text("source", encoding="utf-8")
            parser = BlockingParser()
            self.engine.parsers = ParserRegistry({".md": parser})
            scan = self.engine.scan()

            ingestion = asyncio.create_task(asyncio.to_thread(self.engine.ingest, scan))
            self.assertTrue(await asyncio.to_thread(parser.started.wait, 1))

            # This is the real KnowledgeEngine reader connection while the
            # writer connection holds BEGIN IMMEDIATE inside _index_one().
            during_write = await asyncio.to_thread(
                self.engine.search,
                "real reader writer overlap",
            )
            self.assertEqual(during_write, [])

            parser.release.set()
            report = await ingestion
            self.assertEqual(len(report.indexed), 1)
            self.assertTrue(self.engine.search("real reader writer overlap"))

        asyncio.run(exercise())

    def test_sqlite_storage_coexists_with_concurrent_memory_style_writes(self):
        # Use the same SQLite file and WAL connection model as the existing
        # memory tables. The knowledge connection is serialized internally,
        # while an independent connection writes a neighboring table.
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE memory_probe (value TEXT NOT NULL)")

        self.root.joinpath("concurrent.md").write_text("concurrency marker", encoding="utf-8")
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def indexer() -> None:
            try:
                barrier.wait()
                self.engine.ingest()
            except BaseException as exc:  # pragma: no cover - failure assertion below
                errors.append(exc)

        def memory_writer() -> None:
            try:
                barrier.wait()
                with sqlite3.connect(self.db, timeout=5) as db:
                    for index in range(20):
                        db.execute("INSERT INTO memory_probe(value) VALUES(?)", (str(index),))
                        db.commit()
            except BaseException as exc:  # pragma: no cover - failure assertion below
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda fn: fn(), (indexer, memory_writer)))
        self.assertEqual(errors, [])
        self.assertEqual(self.engine.search("concurrency marker")[0].matched_chunk, "concurrency marker")


if __name__ == "__main__":
    unittest.main()
