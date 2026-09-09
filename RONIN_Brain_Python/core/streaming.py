"""Live agent stream: Server-Sent Events protocol + typewriter pacing.

This module is the *only* place that knows how the ReAct loop's internal
monologue leaves the process and reaches the Kotlin Body while it is still
running. Nothing here changes the loop itself: the planner calls
:func:`emit_event` at meaningful moments, and :class:`ActionLog` /
:class:`StateManager` forward everything they already record.

Protocol (one SSE frame per event, ``data`` is always a JSON object that also
carries ``type`` + a monotonically increasing ``seq`` so a client can detect
gaps)::

    id: 7
    event: tool_call
    data: {"type":"tool_call","seq":7,"step":1,"tool":"open_app", ...}

Event vocabulary
----------------
``start``             request accepted; echoes routing context
``thinking``          a reasoning *phase* began (plan / recall / prompt / call / reason)
``thought``           live reasoning text from the model (incremental deltas)
``tool_call``         the agent decided to execute a tool (Brain-inline or device)
``observation``       the tool returned
``self_correction``   a failure the agent is healing from (alias: ``reflexion``)
``token``             a chunk of the final answer (typewriter feed)
``log``               an :class:`~core.action_log.ActionLog` entry for this request
``state``             an :class:`~core.state_manager.StateManager` snapshot
``done``              terminal frame: the full response + route + step count
``error``             contained failure (non-fatal: the stream still ends with ``done``)

Design notes
------------
* ``emit()`` is thread-safe and never raises: the planner runs LLM/tool calls in
  worker threads (``asyncio.to_thread``) and a broken UI must never break the agent.
* When the HTTP client disconnects the streamer is :meth:`detached
  <AgentStreamer.detach>` — events become no-ops but the agent keeps running to
  completion (history, stats and memory still persist).
* Device tools dispatched mid-stream are awaited through :data:`TOOL_RESULT_BRIDGE`
  instead of ending the request, so the Body keeps one connection for the whole loop.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------

EVENT_START = "start"
EVENT_THINKING = "thinking"
EVENT_THOUGHT = "thought"
EVENT_TOOL_CALL = "tool_call"
EVENT_OBSERVATION = "observation"
EVENT_SELF_CORRECTION = "self_correction"
#: Semantic alias clients may subscribe to instead of ``self_correction``.
EVENT_REFLEXION = "reflexion"
EVENT_TOKEN = "token"
#: Typewriter finished server-side (the Body keeps its own reveal cadence).
EVENT_ANSWER_END = "answer_end"
EVENT_LOG = "log"
EVENT_STATE = "state"
EVENT_DONE = "done"
EVENT_ERROR = "error"

#: Every event type a well-behaved Body should render (used by the contract test).
STREAM_EVENT_TYPES: tuple[str, ...] = (
    EVENT_START, EVENT_THINKING, EVENT_THOUGHT, EVENT_TOOL_CALL, EVENT_OBSERVATION,
    EVENT_SELF_CORRECTION, EVENT_REFLEXION, EVENT_TOKEN, EVENT_ANSWER_END, EVENT_LOG,
    EVENT_STATE, EVENT_DONE, EVENT_ERROR,
)

MEDIA_TYPE_SSE = "text/event-stream"

#: Headers that keep SSE intact through Termux/proxy buffers on Android.
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Content-Type": f"{MEDIA_TYPE_SSE}; charset=utf-8",
}

#: Idle gap before a ``: keep-alive`` comment frame is emitted.
KEEPALIVE_SECONDS = 10.0

#: How long a mid-stream device dispatch waits for the Body's /agent/result.
TOOL_RESULT_TIMEOUT_SECONDS = 120.0


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

@dataclass
class AgentEvent:
    """One SSE frame payload."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: float = 0.0

    def payload(self) -> dict[str, Any]:
        return {"type": self.type, "seq": self.seq, "ts": round(self.ts, 3), **self.data}

    def encode(self) -> str:
        """Render as an SSE frame (``event:`` line + single ``data:`` JSON line)."""
        return (
            f"id: {self.seq}\n"
            f"event: {self.type}\n"
            f"data: {json.dumps(self.payload(), ensure_ascii=False, default=str)}\n"
            "\n"
        )


def encode_sse(event: AgentEvent) -> str:
    return event.encode()


def keepalive_frame() -> str:
    return ": keep-alive\n\n"


# ---------------------------------------------------------------------------
# Typewriter pacing
# ---------------------------------------------------------------------------

def typewriter_chunks(
    text: str,
    *,
    min_seconds: float = 0.7,
    max_seconds: float = 5.0,
    target_cps: float = 34.0,
    chunk_chars: int = 3,
    frame_ms: float = 42.0,
) -> list[tuple[str, float]]:
    """Split ``text`` into ``(chunk, delay_seconds)`` pairs for a typing effect.

    The pacing is deliberately length-adaptive: short answers still take long
    enough to feel typed (``min_seconds``), long answers are merged so they
    finish inside ``max_seconds`` instead of dribbling in for a minute.
    """
    if not text:
        return []
    pieces = [text[i:i + chunk_chars] for i in range(0, len(text), max(1, chunk_chars))]
    seconds = min(max(len(text) / target_cps, min_seconds), max_seconds)
    frame = frame_ms / 1000.0
    ticks = max(1, min(len(pieces), int(seconds / frame)))
    per_tick = max(1, math.ceil(len(pieces) / ticks))
    out: list[tuple[str, float]] = []
    for i in range(0, len(pieces), per_tick):
        out.append(("".join(pieces[i:i + per_tick], ), frame))
    if not out:
        return [(text, 0.0)]
    delay = seconds / len(out)
    return [(chunk, delay) for chunk, _ in out]


# ---------------------------------------------------------------------------
# Streamer
# ---------------------------------------------------------------------------

class AgentStreamer:
    """Thread-safe fan-out of :class:`AgentEvent` frames into an SSE response.

    The planner runs in the same task (and in worker threads for LLM/tool
    calls) while the HTTP generator drains ``_queue``. Both sides only ever
    touch a bounded ``asyncio.Queue`` and never block the agent.
    """

    def __init__(self, *, request_id: str | None = None, maxsize: int = 1024,
                 recorder: list["AgentEvent"] | None = None, pace: float = 1.0) -> None:
        self.request_id = request_id or f"req_{uuid.uuid4().hex[:10]}"
        self.started_at = time.monotonic()
        #: Optional sink that sees every event, for tests and local tooling.
        self.recorder = recorder
        #: 1.0 = human typing cadence; 0 = emit instantly (tests, non-typing clients).
        self.pace = pace
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._seq = 0
        self._closed = False
        self._detached = False
        self._dropped = 0
        self._pumps: dict[int, Any] = {}

    # -- lifecycle ---------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Capture the owning event loop (call from the request coroutine)."""
        self._loop = loop or asyncio.get_running_loop()

    @property
    def live(self) -> bool:
        return not self._closed and not self._detached

    @property
    def detached(self) -> bool:
        return self._detached or self._closed

    @property
    def emitted(self) -> int:
        return self._seq

    def detach(self) -> None:
        """Client is gone: keep the agent running, stop paying for frames."""
        self._detached = True

    def close(self) -> None:
        """Terminate the stream (idempotent)."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._put_raw(None)  # type: ignore[arg-type]

    # -- emitting ----------------------------------------------------------
    def emit(self, type_: str, **data: Any) -> AgentEvent | None:
        """Publish an event. Never raises, drops silently when unobserved."""
        if self._closed or self._detached:
            return None
        with self._lock:
            self._seq += 1
            event = AgentEvent(type=type_, data=data, seq=self._seq, ts=time.time())
        if self.recorder is not None:
            self.recorder.append(event)
        loop = self._loop
        if loop is None:
            # No loop captured (legacy/non-async caller): still safe to fill.
            self._put_raw(event)
            return event
        try:
            running = None
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                self._put_raw(event)
            else:
                loop.call_soon_threadsafe(self._put_raw, event)
        except RuntimeError:
            self._detached = True
        return event

    def _put_raw(self, event: AgentEvent | None) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            if event is not None:
                self._dropped += 1

    # -- consuming ---------------------------------------------------------
    async def frames(self) -> AsyncIterator[str]:
        """Yield encoded SSE frames until :meth:`close` (with keep-alives)."""
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield keepalive_frame()
                continue
            if event is None:
                return
            yield event.encode()

    async def drain(self) -> list[AgentEvent]:
        """Test helper: pull everything currently buffered (non-blocking)."""
        out: list[AgentEvent] = []
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return out
            if event is None:
                self._queue.put_nowait(None)
                return out
            out.append(event)

    async def snapshot(self) -> list[AgentEvent]:
        """Await the close sentinel while collecting every frame (tests/tooling)."""
        out: list[AgentEvent] = []
        while True:
            event = await self._queue.get()
            if event is None:
                return out
            out.append(event)

    # -- request-scoped passthroughs (called from log/state managers) ------
    def forward_log(self, entry: dict[str, Any]) -> None:
        self.emit(EVENT_LOG, **{key: value for key, value in entry.items() if key != "ts"})

    def forward_state(self, snapshot: dict[str, Any]) -> None:
        self.emit(EVENT_STATE, **snapshot)

    # -- final answer typewriter -------------------------------------------
    async def stream_answer(self, text: str, *, step: int = 0) -> int:
        """Emit ``answer_start`` -> ``token``* -> ``answer_end`` for the final text.

        Returns the number of characters streamed. A detached (or closed) stream
        skips the pacing loop entirely — nobody is waiting for the effect.
        """
        if not text:
            return 0
        chunks = typewriter_chunks(text) if self.pace > 0 else [(text, 0.0)]
        if self.live:
            self.emit(EVENT_THINKING, step=step, phase="answer",
                      text=f"Composing answer ({len(text)} chars)…")
        for index, (chunk, delay) in enumerate(chunks):
            if not self.live:
                break
            if delay and self.pace > 0:
                await asyncio.sleep(delay * self.pace)
            self.emit(EVENT_TOKEN, i=index, text=chunk)
        if self.live:
            self.emit(EVENT_ANSWER_END, i=len(chunks), chars=len(text), tail=text[-80:])
        return len(text)

    # -- reasoning delta plumbing ------------------------------------------
    def delta_forwarder(self, step: int = 0, *, min_interval: float = 0.12,
                        min_chars: int = 48) -> "_DeltaPump":
        """Return an ``on_delta`` callback that throttles model deltas into events.

        Token-per-token frames would flood a phone socket; deltas are buffered
        and flushed on a time or size threshold, with a guaranteed final flush
        via :meth:`flush_delta`.
        """
        pump = _DeltaPump(self, step, min_interval=min_interval, min_chars=min_chars)
        self._pumps[step] = pump
        return pump

    def flush_delta(self, step: int = 0, *, text: str | None = None) -> None:
        """Close the reasoning line for ``step``.

        With ``text`` the client *replaces* the line with that authoritative
        version (the full assistant message, tool tags stripped); without it the
        buffered remainder is flushed and marked final.
        """
        pump = self._pumps.pop(step, None)
        pending = pump.take() if pump is not None else ""
        if text is not None:
            self.emit(EVENT_THOUGHT, step=step, stream=f"t{step}", delta="", final=True,
                      text=text[:4000])
            return
        self.emit(EVENT_THOUGHT, step=step, stream=f"t{step}", delta=pending, final=True)


class _DeltaPump:
    """Buffers provider deltas and republishes them as ``thought`` frames."""

    def __init__(self, streamer: "AgentStreamer", step: int, *, min_interval: float,
                 min_chars: int) -> None:
        self._streamer = streamer
        self.step = step
        self.stream_id = f"t{step}"
        self._min_interval = min_interval
        self._min_chars = min_chars
        self._buffer = ""
        self._last = 0.0

    def take(self) -> str:
        pending, self._buffer = self._buffer, ""
        return pending

    def __call__(self, piece: str) -> None:
        if not piece:
            return
        self._buffer += piece
        now = time.monotonic()
        if len(self._buffer) < self._min_chars and (now - self._last) < self._min_interval:
            return
        self._last = now
        self._streamer.emit(EVENT_THOUGHT, step=self.step, stream=self.stream_id,
                            delta=self._buffer, final=False)
        self._buffer = ""



# ---------------------------------------------------------------------------
# Per-request binding (contextvar so deep call sites need no new parameters)
# ---------------------------------------------------------------------------

_CURRENT: contextvars.ContextVar["AgentStreamer | None"] = contextvars.ContextVar(
    "vyrx_agent_stream", default=None
)


def bind_stream(streamer: AgentStreamer | None) -> contextvars.Token:
    return _CURRENT.set(streamer)


def unbind_stream(token: contextvars.Token) -> None:
    try:
        _CURRENT.reset(token)
    except ValueError:
        _CURRENT.set(None)


def current_stream() -> AgentStreamer | None:
    streamer = _CURRENT.get()
    if streamer is None or streamer.detached:
        return None
    return streamer


def emit_event(type_: str, **data: Any) -> None:
    """Emit to whatever stream owns the current task (no-op for legacy calls)."""
    streamer = _CURRENT.get()
    if streamer is not None:
        streamer.emit(type_, **data)


# ---------------------------------------------------------------------------
# Device-action bridge: the Body answers on /agent/result, we resume in-stream
# ---------------------------------------------------------------------------

class ToolResultBridge:
    """Hand-off between ``POST /agent/result`` and a waiting stream generator.

    A dispatched device tool normally ends the request (the Body starts a new
    call). While an SSE stream is open we instead park on a Future, so the
    whole Thought -> Action -> Observation loop rides one connection and the
    Body sees ``observation`` arrive in order.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future] = {}
        self._lock = threading.Lock()

    def register(self, session_id: str) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        with self._lock:
            previous = self._waiters.pop(session_id, None)
        if previous is not None and not previous.done():
            previous.cancel()
        with self._lock:
            self._waiters[session_id] = future
        return future

    def resolve(self, session_id: str, payload: dict[str, Any]) -> bool:
        """Resolve a waiting stream. ``True`` = the stream consumed the result."""
        with self._lock:
            future = self._waiters.pop(session_id, None)
        if future is None or future.done():
            return False
        future.set_result(payload)
        return True

    def release(self, session_id: str, future: asyncio.Future | None = None) -> None:
        with self._lock:
            current = self._waiters.get(session_id)
            if current is None or (future is not None and current is not future):
                return
            self._waiters.pop(session_id, None)

    def pending(self) -> list[str]:
        with self._lock:
            return list(self._waiters)


TOOL_RESULT_BRIDGE = ToolResultBridge()
