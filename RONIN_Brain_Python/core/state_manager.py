"""Thread-safe holder for the current VYRX AI state.

The body polls `GET /api/state` and receives push updates over SSE.
States: idle (blue), listening (blue), thinking (purple),
executing (green), learning (amber).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable, Literal

StateName = Literal["idle", "listening", "thinking", "executing", "learning"]

DEFAULT_MESSAGES: dict[str, str] = {
    "idle": "Ready. Waiting for your command.",
    "listening": "Listening... Waiting for voice command.",
    "thinking": "Processing API request...",
    "executing": "Running device action...",
    "learning": "Saving new memory...",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict], None]] = []
        self._snapshot: dict = {
            "state": "idle",
            "message": DEFAULT_MESSAGES["idle"],
            "provider": None,
            "model": None,
            "response_ms": None,
            "updated_at": _now_iso(),
        }

    def add_listener(self, callback: Callable[[dict], None]) -> None:
        """Register a callback fired (with the snapshot) on every state change."""
        self._listeners.append(callback)

    def set(
        self,
        state: StateName,
        message: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        response_ms: int | None = None,
    ) -> dict:
        with self._lock:
            self._snapshot = {
                "state": state,
                "message": message if message is not None else DEFAULT_MESSAGES.get(state, ""),
                "provider": provider if provider is not None else self._snapshot.get("provider"),
                "model": model if model is not None else self._snapshot.get("model"),
                "response_ms": response_ms if response_ms is not None else self._snapshot.get("response_ms"),
                "updated_at": _now_iso(),
            }
            snapshot = dict(self._snapshot)
        for callback in list(self._listeners):
            try:
                callback(snapshot)
            except Exception:
                # Listeners must never break the state machine.
                pass
        return snapshot

    def get(self) -> dict:
        with self._lock:
            return dict(self._snapshot)
