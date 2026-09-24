"""Lifecycle-scoped asynchronous periodic knowledge ingestion."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.knowledge.engine import KnowledgeEngine


@dataclass(frozen=True)
class IngestionWatcherConfig:
    """Bounded polling configuration for the application-owned watcher."""

    enabled: bool = True
    interval_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

    @classmethod
    def from_environment(cls, settings_path: str | Path | None = None) -> "IngestionWatcherConfig":
        """Read settings JSON first, with environment variables as overrides."""
        values: dict[str, Any] = {}
        path = Path(settings_path) if settings_path else Path(__file__).resolve().parents[2] / "config" / "settings.json"
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                values = loaded
        except (OSError, ValueError):
            pass

        enabled_value: Any = os.getenv(
            "VYRX_KNOWLEDGE_BACKGROUND_WATCH_ENABLED",
            values.get("knowledge_background_watch_enabled", True),
        )
        if isinstance(enabled_value, str):
            enabled = enabled_value.strip().casefold() not in {"0", "false", "off", "no"}
        else:
            enabled = bool(enabled_value)
        raw = os.getenv(
            "VYRX_KNOWLEDGE_WATCH_INTERVAL_SECONDS",
            values.get("knowledge_background_watch_interval_seconds", 300),
        )
        try:
            interval = float(raw)
        except (TypeError, ValueError):
            interval = 300.0
        return cls(enabled=enabled, interval_seconds=max(1.0, interval))


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
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.engine = engine
        self.config = config or IngestionWatcherConfig.from_environment()
        self._log = log or (lambda message, level="info": None)
        self._sleep = sleep or asyncio.sleep
        self._task: asyncio.Task[None] | None = None
        self._sweep_lock = asyncio.Lock()
        self._inflight: set[asyncio.Task[Any]] = set()
        self._stopping = False
        self.sweeps_completed = 0
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> asyncio.Task[None] | None:
        """Start exactly one task on the currently running event loop."""
        if not self.config.enabled:
            return None
        if self.running:
            return self._task
        self._stopping = False
        self._safe_log(
            f"Knowledge watcher started (interval={self.config.interval_seconds:g}s)",
            "info",
        )
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
        self._safe_log("Knowledge watcher stopped", "info")

    def _safe_log(self, message: str, level: str = "info") -> None:
        try:
            self._log(message, level)
        except Exception:
            pass

    async def run_once(self) -> bool:
        """Run one isolated scan/ingest cycle; return whether it succeeded."""
        async with self._sweep_lock:
            started = time.monotonic()
            self._safe_log("Knowledge background sweep started", "info")
            try:
                scan = await self._call_blocking(self.engine.scan)
                observations = getattr(scan, "observations", ())
                deleted = getattr(scan, "deleted", ())
                changed = sum(
                    1 for item in (*observations, *deleted)
                    if getattr(getattr(item, "change", None), "value", None) != "unchanged"
                )
                # Yield between discovery and ingestion even when the scan is
                # small, so callers can service pending chat/stream events.
                await asyncio.sleep(0)
                report = await self._call_blocking(self.engine.ingest, scan)
                files = getattr(report, "files", ())
                indexed = sum(
                    1 for item in files
                    if getattr(getattr(item, "status", None), "value", None) == "indexed"
                )
                failed = sum(
                    1 for item in files
                    if getattr(getattr(item, "status", None), "value", None) == "failed"
                )
                await asyncio.sleep(0)
                self.sweeps_completed += 1
                self.last_error = None
                duration_ms = int((time.monotonic() - started) * 1000)
                self._safe_log(
                    "Knowledge background sweep complete: "
                    f"discovered={len(observations) + len(deleted)} "
                    f"changed={changed} indexed={indexed} failed={failed} "
                    f"duration_ms={duration_ms}",
                    "info",
                )
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
                lowered = self.last_error.casefold()
                level = "warning"
                prefix = "Knowledge background sweep failed"
                if "locked" in lowered or "busy" in lowered or "database" in lowered:
                    prefix = "Knowledge background sweep database contention"
                self._safe_log(f"{prefix}: {self.last_error}", level)
                return False

    async def _run(self) -> None:
        try:
            while not self._stopping:
                await self.run_once()
                await self._sleep(self.config.interval_seconds)
        except asyncio.CancelledError:
            await self._drain_inflight()
            raise
        except Exception as exc:
            # This is a final defensive boundary. A watcher crash is never
            # allowed to escape into FastAPI lifespan handling.
            self.last_error = f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
            self._safe_log(
                f"Knowledge watcher stopped after internal fault: {self.last_error}",
                "error",
            )

    async def _call_blocking(self, function: Callable[..., Any], *args: Any) -> Any:
        """Run synchronous engine work without blocking the asyncio loop."""
        operation = asyncio.create_task(asyncio.to_thread(function, *args))
        self._inflight.add(operation)

        def forget_operation(done: asyncio.Task[Any]) -> None:
            self._inflight.discard(done)

        operation.add_done_callback(forget_operation)
        # Shield lets shutdown cancel the watcher while the SQLite operation
        # completes cooperatively; _drain_inflight waits before resources close.
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # Do not await the thread here: _run() performs the cooperative
            # drain before propagating cancellation to the lifecycle owner.
            raise

    async def _drain_inflight(self) -> None:
        pending = tuple(self._inflight)
        if not pending:
            return
        # asyncio.to_thread() cannot be forcibly cancelled. Keep the engine
        # alive until every worker finishes so lifespan shutdown can safely
        # close its SQLite connections afterward.
        await asyncio.gather(
            *(asyncio.shield(operation) for operation in pending),
            return_exceptions=True,
        )


__all__ = ["IngestionWatcherConfig", "KnowledgeIngestionWatcher"]
