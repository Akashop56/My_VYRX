"""Lifecycle-scoped asynchronous periodic knowledge ingestion."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable

from core.knowledge.engine import KnowledgeEngine


@dataclass(frozen=True)
class IngestionWatcherConfig:
    """Bounded polling configuration for the application-owned watcher."""

    interval_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

    @classmethod
    def from_environment(cls) -> "IngestionWatcherConfig":
        raw = os.getenv("VYRX_KNOWLEDGE_WATCH_INTERVAL_SECONDS", "300")
        try:
            interval = float(raw)
        except (TypeError, ValueError):
            interval = 300.0
        return cls(max(1.0, interval))


class KnowledgeIngestionWatcher:
    """One cancellable asyncio task that periodically runs controlled ingest.

    ``KnowledgeEngine.scan`` and ``ingest`` are synchronous SQLite/file
    operations. They run in worker threads so the event loop remains available
    to HTTP, planner, and streaming work. The watcher itself owns no database
    connection and never creates a second ingestion engine.
    """

    def __init__(
        self,
        engine: KnowledgeEngine,
        *,
        config: IngestionWatcherConfig | None = None,
        log: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.engine = engine
        self.config = config or IngestionWatcherConfig.from_environment()
        self._log = log or (lambda message, level="info": None)
        self._task: asyncio.Task[None] | None = None
        self._sweep_lock = asyncio.Lock()
        self._inflight: set[asyncio.Task[Any]] = set()
        self._stopping = False
        self.sweeps_completed = 0
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> asyncio.Task[None]:
        """Start exactly one task on the currently running event loop."""
        if self.running:
            return self._task  # type: ignore[return-value]
        self._stopping = False
        self._task = asyncio.create_task(
            self._run(),
            name="vyrx-knowledge-ingestion",
        )
        return self._task

    async def stop(self) -> None:
        """Cancel the watcher and drain any already-running worker operation."""
        task = self._task
        self._stopping = True
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._drain_inflight()
        self._task = None

    async def run_once(self) -> bool:
        """Run one isolated scan/ingest cycle; return whether it succeeded."""
        async with self._sweep_lock:
            try:
                scan = await self._call_blocking(self.engine.scan)
                # Yield between discovery and ingestion even when the scan is
                # small, so callers can service pending chat/stream events.
                await asyncio.sleep(0)
                await self._call_blocking(self.engine.ingest, scan)
                await asyncio.sleep(0)
                self.sweeps_completed += 1
                self.last_error = None
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
                try:
                    self._log(f"Knowledge background sweep failed: {self.last_error}", "warning")
                except Exception:
                    # Logging must never turn an ingestion fault into an app
                    # fault, especially during shutdown.
                    pass
                return False

    async def _run(self) -> None:
        try:
            while not self._stopping:
                await self.run_once()
                await asyncio.sleep(self.config.interval_seconds)
        except asyncio.CancelledError:
            await self._drain_inflight()
            raise
        except Exception as exc:
            # This is a final defensive boundary. A watcher crash is never
            # allowed to escape into FastAPI lifespan handling.
            self.last_error = f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
            try:
                self._log(f"Knowledge watcher stopped after internal fault: {self.last_error}", "error")
            except Exception:
                pass

    async def _call_blocking(self, function: Callable[..., Any], *args: Any) -> Any:
        """Run synchronous engine work without blocking the asyncio loop."""
        operation = asyncio.create_task(asyncio.to_thread(function, *args))
        self._inflight.add(operation)
        operation.add_done_callback(self._inflight.discard)
        # Shield lets shutdown cancel the watcher while the SQLite operation
        # finishes cleanly; stop() drains the still-running operation below.
        return await asyncio.shield(operation)

    async def _drain_inflight(self) -> None:
        pending = tuple(self._inflight)
        if not pending:
            return
        await asyncio.gather(
            *(asyncio.shield(operation) for operation in pending),
            return_exceptions=True,
        )


__all__ = ["IngestionWatcherConfig", "KnowledgeIngestionWatcher"]
