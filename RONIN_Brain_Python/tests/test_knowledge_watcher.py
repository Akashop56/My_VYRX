"""Deterministic tests for the lifecycle-scoped ingestion watcher."""
from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

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


class GateSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []
        self.gate = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        await self.gate.wait()
        self.gate.clear()


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
        duplicate = watcher.start()
        await asyncio.sleep(0)
        self.assertIs(task, duplicate)
        self.assertTrue(watcher.running)
        await watcher.stop()

        self.assertFalse(watcher.running)
        self.assertTrue(task.done())

    async def test_periodic_execution_triggers_scan_and_ingest(self):
        engine = FakeEngine()
        sleeper = GateSleep()
        watcher = KnowledgeIngestionWatcher(
            engine,
            config=IngestionWatcherConfig(interval_seconds=300),
            sleep=sleeper,
        )

        watcher.start()
        for _ in range(20):
            await asyncio.sleep(0)
            if len(sleeper.calls) >= 1:
                break
        self.assertEqual(engine.scan_calls, 1)
        self.assertEqual(engine.ingest_calls, 1)
        sleeper.gate.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if len(sleeper.calls) >= 2:
                break
        self.assertEqual(engine.scan_calls, 2)
        self.assertEqual(engine.ingest_calls, 2)
        sleeper.gate.set()
        await watcher.stop()
        self.assertGreaterEqual(watcher.sweeps_completed, 2)

    async def test_fault_is_logged_and_loop_survives_next_cycle(self):
        engine = FakeEngine()
        engine.fail_scan_once = True
        sleeper = GateSleep()
        logs: list[tuple[str, str]] = []
        watcher = KnowledgeIngestionWatcher(
            engine,
            config=IngestionWatcherConfig(interval_seconds=300),
            log=lambda message, level: logs.append((message, level)),
            sleep=sleeper,
        )

        watcher.start()
        for _ in range(20):
            await asyncio.sleep(0)
            if len(sleeper.calls) >= 1:
                break
        self.assertEqual(engine.scan_calls, 1)
        self.assertEqual(engine.ingest_calls, 0)
        sleeper.gate.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if len(sleeper.calls) >= 2:
                break
        self.assertEqual(engine.scan_calls, 2)
        self.assertEqual(engine.ingest_calls, 1)
        self.assertTrue(any(level == "warning" for _, level in logs))
        sleeper.gate.set()
        await watcher.stop()
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

    async def test_shutdown_bounds_a_hung_worker_and_logs_warning(self):
        class HangingEngine(FakeEngine):
            def __init__(self) -> None:
                super().__init__()
                self.started = threading.Event()
                self.release = threading.Event()

            def scan(self):
                self.started.set()
                self.release.wait(timeout=2)
                return super().scan()

        engine = HangingEngine()
        logs: list[tuple[str, str]] = []
        watcher = KnowledgeIngestionWatcher(
            engine,
            config=IngestionWatcherConfig(interval_seconds=300),
            log=lambda message, level: logs.append((message, level)),
        )

        task = watcher.start()
        await asyncio.to_thread(engine.started.wait, 1)
        with patch("core.knowledge.watcher._INFLIGHT_DRAIN_TIMEOUT_SECONDS", 0.01):
            await watcher.stop()
        engine.release.set()

        self.assertTrue(task.done())
        self.assertFalse(watcher.running)
        self.assertTrue(any(level == "warning" for _, level in logs))

    async def test_disabled_configuration_creates_no_task(self):
        watcher = KnowledgeIngestionWatcher(
            FakeEngine(),
            config=IngestionWatcherConfig(enabled=False, interval_seconds=300),
        )

        self.assertIsNone(watcher.start())
        self.assertFalse(watcher.running)
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
