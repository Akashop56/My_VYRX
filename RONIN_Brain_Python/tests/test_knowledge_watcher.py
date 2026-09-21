"""Deterministic tests for the lifecycle-scoped ingestion watcher."""
from __future__ import annotations

import asyncio
import threading
import unittest

from core.knowledge.watcher import IngestionWatcherConfig, KnowledgeIngestionWatcher


class FakeEngine:
    def __init__(self) -> None:
        self.scan_calls = 0
        self.ingest_calls = 0
        self.fail_scan_once = False

    def scan(self):
        self.scan_calls += 1
        if self.fail_scan_once:
            self.fail_scan_once = False
            raise OSError("synthetic corrupt directory")
        return {"scan": self.scan_calls}

    def ingest(self, scan):
        self.ingest_calls += 1
        return {"ingested": scan}


class ConcurrentFakeEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.write_started = threading.Event()
        self.release_write = threading.Event()
        self.writing = False
        self.read_during_write = False

    def ingest(self, scan):
        self.writing = True
        self.write_started.set()
        self.release_write.wait(timeout=2)
        self.writing = False
        return super().ingest(scan)

    def search(self, query: str):
        if self.writing:
            self.read_during_write = True
        return [query]


class KnowledgeWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_task_starts_and_cancels_cleanly(self):
        engine = FakeEngine()
        watcher = KnowledgeIngestionWatcher(
            engine,
            config=IngestionWatcherConfig(interval_seconds=60),
        )

        task = watcher.start()
        await asyncio.sleep(0.02)
        self.assertTrue(watcher.running)
        await watcher.stop()

        self.assertFalse(watcher.running)
        self.assertTrue(task.done())

    async def test_periodic_execution_triggers_scan_and_ingest(self):
        engine = FakeEngine()
        watcher = KnowledgeIngestionWatcher(
            engine,
            config=IngestionWatcherConfig(interval_seconds=0.01),
        )

        watcher.start()
        await asyncio.sleep(0.055)
        await watcher.stop()

        self.assertGreaterEqual(engine.scan_calls, 2)
        self.assertEqual(engine.scan_calls, engine.ingest_calls)
        self.assertGreaterEqual(watcher.sweeps_completed, 2)

    async def test_fault_is_logged_and_loop_survives_next_cycle(self):
        engine = FakeEngine()
        engine.fail_scan_once = True
        logs: list[tuple[str, str]] = []
        watcher = KnowledgeIngestionWatcher(
            engine,
            config=IngestionWatcherConfig(interval_seconds=0.01),
            log=lambda message, level: logs.append((message, level)),
        )

        watcher.start()
        await asyncio.sleep(0.055)
        self.assertTrue(watcher.running)
        await watcher.stop()

        self.assertGreaterEqual(engine.scan_calls, 2)
        self.assertGreaterEqual(engine.ingest_calls, 1)
        self.assertTrue(any(level == "warning" for _, level in logs))
        self.assertGreaterEqual(watcher.sweeps_completed, 1)

    async def test_foreground_read_can_run_during_background_write(self):
        engine = ConcurrentFakeEngine()
        watcher = KnowledgeIngestionWatcher(
            engine,
            config=IngestionWatcherConfig(interval_seconds=60),
        )

        sweep = asyncio.create_task(watcher.run_once())
        await asyncio.to_thread(engine.write_started.wait, 2)
        result = await asyncio.to_thread(engine.search, "foreground query")
        engine.release_write.set()
        self.assertTrue(await sweep)

        self.assertEqual(result, ["foreground query"])
        self.assertTrue(engine.read_during_write)
        await watcher.stop()

    async def test_run_once_fault_does_not_escape_to_caller(self):
        engine = FakeEngine()
        engine.fail_scan_once = True
        watcher = KnowledgeIngestionWatcher(
            engine,
            config=IngestionWatcherConfig(interval_seconds=60),
        )

        self.assertFalse(await watcher.run_once())
        self.assertEqual(watcher.sweeps_completed, 0)
        self.assertIsNotNone(watcher.last_error)
        await watcher.stop()


if __name__ == "__main__":
    unittest.main()
