"""Real-time action log: ring buffer + thread-safe event publisher.

Every planner/tool step is recorded here; the body consumes it through
`GET /api/action_logs` (history) and `GET /api/events` (SSE live stream).

Publishing happens from worker threads, so subscriber queues are the
thread-safe stdlib `queue.Queue`; SSE generators poll them via
`asyncio.to_thread`.
"""
from __future__ import annotations

import json
import queue
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

LogLevel = Literal["info", "success", "warning", "error", "tool"]


def _utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _local_time_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


@dataclass
class ActionLogEntry:
    ts: str
    time: str
    level: LogLevel
    text: str


class ActionLog:
    def __init__(self, maxlen: int = 200) -> None:
        self._lock = threading.Lock()
        self._entries: deque[ActionLogEntry] = deque(maxlen=maxlen)
        self._subscribers: set[queue.Queue] = set()
        self._sub_lock = threading.Lock()

    # -- recording ---------------------------------------------------------
    def log(self, text: str, level: LogLevel = "info") -> ActionLogEntry:
        entry = ActionLogEntry(ts=_utc_now_iso(), time=_local_time_hms(), level=level, text=text)
        with self._lock:
            self._entries.append(entry)
        self._publish({"type": "log", "log": asdict(entry)})
        return entry

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            items = list(self._entries)
        return [asdict(entry) for entry in items[-max(1, limit):]][-limit:]

    # -- SSE subscriptions ---------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._sub_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            self._subscribers.discard(q)

    def _publish(self, event: dict[str, Any]) -> None:
        with self._sub_lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # slow consumer: drop, never block the planner

    def publish_state(self, snapshot: dict) -> None:
        self._publish({"type": "state", "state": snapshot})

    @staticmethod
    def encode_sse(event: dict[str, Any]) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
