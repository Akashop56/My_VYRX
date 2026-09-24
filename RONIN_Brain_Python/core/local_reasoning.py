"""Provider-agnostic local reasoning runtime adapter.

The adapter intentionally does not ship or download a model. It speaks a small
JSON-lines-like process contract to a locally configured runtime command:

* ``COMMAND --health`` must exit successfully and may return ``{"ready": true}``.
* ``COMMAND --serve`` stays resident, emits ``{"ready": true}``, then
  receives one JSON request per stdin line and returns one completion per
  stdout line (OpenAI-shaped or ``{"text": "..."}``).

This keeps runtime/model selection outside planner semantics and makes missing
local infrastructure an honest unavailable capability rather than a fake
fallback.
"""
from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from core.capabilities import (
    CanonicalFailureClass,
    CapabilityHealth,
)

LOCAL_REASONING_CAPABILITY_ID = "reasoning-local-runtime"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_CONTEXT_CHARS = 12000
DEFAULT_MAX_OUTPUT_CHARS = 20000


@dataclass(frozen=True)
class LocalReasoningReadiness:
    """Result of the runtime health/readiness check."""

    health: CapabilityHealth
    reason: str
    failure_class: CanonicalFailureClass | None = None
    runtime_command: str | None = None

    @property
    def ready(self) -> bool:
        return self.health in {CapabilityHealth.AVAILABLE, CapabilityHealth.DEGRADED}


class LocalReasoningAdapter:
    """Serialized, process-backed local reasoning adapter.

    One serving process is owned by the adapter lifecycle and reused across
    requests. This avoids reloading a model for every request, while the lock
    prevents concurrent requests from overcommitting a single external
    runtime/model.
    """

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> None:
        self.command = tuple(str(item) for item in (command or ()) if str(item))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.max_context_chars = max(1000, int(max_context_chars))
        self.max_output_chars = max(1000, int(max_output_chars))
        self._state_lock = threading.RLock()
        self._initialize_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._active: set[subprocess.Popen[str]] = set()
        self._worker: subprocess.Popen[str] | None = None
        self._closed = False
        self._initialized = False
        self._readiness = LocalReasoningReadiness(
            CapabilityHealth.UNKNOWN,
            "Local reasoning readiness has not been checked",
            runtime_command=self.display_command,
        )

    @classmethod
    def from_environment(cls) -> "LocalReasoningAdapter":
        raw = os.getenv("VYRX_LOCAL_REASONING_COMMAND", "").strip()
        try:
            command = shlex.split(raw) if raw else ()
        except ValueError as exc:
            instance = cls()
            instance._readiness = LocalReasoningReadiness(
                CapabilityHealth.UNAVAILABLE,
                f"Invalid VYRX_LOCAL_REASONING_COMMAND: {exc}",
                CanonicalFailureClass.VALIDATION_FAILED,
            )
            instance._initialized = True
            return instance
        def _env_float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        def _env_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        return cls(
            command,
            timeout_seconds=_env_float("VYRX_LOCAL_REASONING_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
            max_context_chars=_env_int("VYRX_LOCAL_REASONING_MAX_CONTEXT_CHARS", DEFAULT_MAX_CONTEXT_CHARS),
            max_output_chars=_env_int("VYRX_LOCAL_REASONING_MAX_OUTPUT_CHARS", DEFAULT_MAX_OUTPUT_CHARS),
        )

    @property
    def display_command(self) -> str | None:
        return " ".join(self.command) if self.command else None

    @property
    def readiness(self) -> LocalReasoningReadiness:
        with self._state_lock:
            return self._readiness

    def initialize(self) -> LocalReasoningReadiness:
        """Probe readiness and start one lifecycle-scoped serving process."""
        with self._initialize_lock:
            with self._state_lock:
                if self._initialized:
                    return self._readiness
                if self._closed:
                    self._initialized = True
                    self._readiness = LocalReasoningReadiness(
                        CapabilityHealth.UNAVAILABLE,
                        "Unsupported operation: local reasoning adapter is closed",
                        CanonicalFailureClass.UNSUPPORTED_OPERATION,
                        self.display_command,
                    )
                    return self._readiness
                if not self.command:
                    self._initialized = True
                    self._readiness = LocalReasoningReadiness(
                        CapabilityHealth.UNAVAILABLE,
                        "VYRX_LOCAL_REASONING_COMMAND is not configured; no local runtime was selected",
                        CanonicalFailureClass.UNSUPPORTED_OPERATION,
                    )
                    return self._readiness
                executable = self.command[0]
                if shutil.which(executable) is None and not os.path.isfile(executable):
                    self._initialized = True
                    self._readiness = LocalReasoningReadiness(
                        CapabilityHealth.UNAVAILABLE,
                        f"Local reasoning executable is not available: {executable}",
                        CanonicalFailureClass.UNSUPPORTED_OPERATION,
                        self.display_command,
                    )
                    return self._readiness

            try:
                stdout, stderr, returncode = self._run_process(
                    [*self.command, "--health"],
                    None,
                    self.timeout_seconds,
                )
            except TimeoutError:
                readiness = LocalReasoningReadiness(
                    CapabilityHealth.UNAVAILABLE,
                    "Local reasoning readiness probe timed out",
                    CanonicalFailureClass.TRANSIENT,
                    self.display_command,
                )
            except Exception as exc:
                detail = " ".join(str(exc).split())[:240]
                reason = f"Local reasoning readiness probe failed: {type(exc).__name__}"
                if detail:
                    reason += f": {detail}"
                readiness = LocalReasoningReadiness(
                    CapabilityHealth.UNAVAILABLE,
                    reason,
                    CanonicalFailureClass.UNKNOWN_FATAL,
                    self.display_command,
                )
            else:
                ready = returncode == 0
                health_detail = ""
                if stdout.strip():
                    try:
                        payload = json.loads(stdout)
                        if isinstance(payload, dict):
                            if payload.get("ready") is False:
                                ready = False
                            health_detail = str(payload.get("reason") or "").strip()
                    except json.JSONDecodeError:
                        pass
                if not ready:
                    detail = (
                        " ".join(health_detail.split())[:240]
                        or " ".join(stderr.split())[:240]
                        or "runtime returned a non-zero health status"
                    )
                    readiness = LocalReasoningReadiness(
                        CapabilityHealth.UNAVAILABLE,
                        f"Local reasoning runtime is not ready: {detail}",
                        CanonicalFailureClass.UNKNOWN_FATAL,
                        self.display_command,
                    )
                else:
                    try:
                        self._start_worker()
                    except TimeoutError:
                        readiness = LocalReasoningReadiness(
                            CapabilityHealth.UNAVAILABLE,
                            "Local reasoning serving process did not become ready before the timeout",
                            CanonicalFailureClass.TRANSIENT,
                            self.display_command,
                        )
                    except ValueError as exc:
                        readiness = LocalReasoningReadiness(
                            CapabilityHealth.UNAVAILABLE,
                            f"Malformed local reasoning readiness output: {exc}",
                            CanonicalFailureClass.VALIDATION_FAILED,
                            self.display_command,
                        )
                    except Exception as exc:
                        detail = " ".join(str(exc).split())[:240]
                        reason = f"Local reasoning serving process failed to start: {type(exc).__name__}"
                        if detail:
                            reason += f": {detail}"
                        readiness = LocalReasoningReadiness(
                            CapabilityHealth.UNAVAILABLE,
                            reason,
                            CanonicalFailureClass.UNKNOWN_FATAL,
                            self.display_command,
                        )
                    else:
                        readiness = LocalReasoningReadiness(
                            CapabilityHealth.AVAILABLE,
                            "Local reasoning runtime passed its readiness probe",
                            runtime_command=self.display_command,
                        )
            with self._state_lock:
                self._readiness = readiness
                self._initialized = True
                return readiness

    def complete(
        self,
        message: str,
        history: list,
        providers: list,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run one normalized completion through the configured local runtime."""
        readiness = self.initialize()
        if not readiness.ready:
            raise RuntimeError(readiness.reason)
        messages = kwargs.get("messages")
        if not isinstance(messages, list):
            messages = self._messages(message, history, kwargs.get("system_prompt"))
        bounded_messages = self._bound_messages(messages)
        request = {
            "messages": bounded_messages,
            "tools": kwargs.get("tools") or [],
            "max_context_chars": self.max_context_chars,
        }
        stdout, stderr = self._run_worker(
            json.dumps(request, ensure_ascii=False),
            self.timeout_seconds,
        )
        if stderr:
            lowered = stderr.casefold()
            if "out of memory" in lowered or "oom" in lowered:
                raise MemoryError("local reasoning runtime reported out of memory")
        completion = self._normalize_output(stdout)
        on_delta: Callable[[str], None] | None = kwargs.get("on_delta")
        if on_delta is not None:
            content = completion["choices"][0]["message"].get("content") or ""
            if content:
                on_delta(content)
        return completion

    def close(self) -> None:
        """Stop active processes and prevent future inference calls."""
        with self._state_lock:
            self._closed = True
            self._readiness = LocalReasoningReadiness(
                CapabilityHealth.UNAVAILABLE,
                "Unsupported operation: local reasoning adapter is closed",
                CanonicalFailureClass.UNSUPPORTED_OPERATION,
                self.display_command,
            )
            active = list(self._active)
        for process in active:
            self._terminate_process(process)

    def _start_worker(self) -> None:
        """Start and validate the one persistent serving process."""
        process = subprocess.Popen(
            [*self.command, "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        with self._state_lock:
            if self._closed:
                self._terminate_process(process)
                raise RuntimeError("local reasoning adapter is closed")
            self._worker = process
            self._active.add(process)
        line = self._readline_with_timeout(process, self.timeout_seconds)
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            self._terminate_process(process)
            raise ValueError("serving process returned non-JSON readiness") from exc
        if not isinstance(payload, dict) or payload.get("ready") is not True:
            self._terminate_process(process)
            reason = str(payload.get("reason") or "serving process did not report ready") if isinstance(payload, dict) else "serving process did not report ready"
            raise ValueError(reason)

    def _run_worker(self, request_text: str, timeout: float) -> tuple[str, str]:
        """Send one line to the persistent worker while holding the adapter lock."""
        with self._inference_lock:
            with self._state_lock:
                process = self._worker
                if self._closed:
                    raise RuntimeError("local reasoning adapter is closed")
            if process is None or process.poll() is not None or process.stdin is None:
                raise RuntimeError("local reasoning runtime crashed: serving process is not running")
            try:
                process.stdin.write(request_text + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._terminate_process(process)
                raise RuntimeError("local reasoning runtime crashed: serving process pipe closed") from exc
            line = self._readline_with_timeout(process, timeout)
            if not line:
                stderr = ""
                if process.stderr is not None:
                    stderr = process.stderr.read(2000)
                self._terminate_process(process)
                detail = " ".join(stderr.split())[:240] or "serving process exited without output"
                lowered = detail.casefold()
                if "out of memory" in lowered or "oom" in lowered:
                    raise MemoryError("local reasoning runtime reported out of memory")
                if "context" in lowered and any(word in lowered for word in ("limit", "length", "overflow")):
                    raise ValueError("local reasoning runtime context limit exceeded")
                raise RuntimeError(f"local reasoning runtime crashed: {detail}")
            stderr = ""
            if process.stderr is not None:
                # Non-blocking stderr consumption is intentionally omitted;
                # stderr is read only when the worker exits so it cannot block
                # an otherwise healthy inference call.
                stderr = ""
            return line[: self.max_output_chars], stderr

    def _readline_with_timeout(self, process: subprocess.Popen[str], timeout: float) -> str:
        if process.stdout is None:
            raise RuntimeError("local reasoning runtime has no stdout pipe")
        result: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_line() -> None:
            try:
                result.put(process.stdout.readline())
            except Exception:
                result.put("")

        reader = threading.Thread(target=read_line, daemon=True)
        reader.start()
        try:
            return result.get(timeout=timeout)
        except queue.Empty as exc:
            self._terminate_process(process)
            raise TimeoutError("local reasoning runtime timed out") from exc

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        try:
            process.terminate()
            process.wait(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        with self._state_lock:
            self._active.discard(process)
            if self._worker is process:
                self._worker = None

    def _run_process(
        self,
        command: list[str],
        stdin_text: str | None,
        timeout: float,
    ) -> tuple[str, str, int]:
        with self._inference_lock:
            with self._state_lock:
                if self._closed:
                    raise RuntimeError("local reasoning adapter is closed")
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._active.add(process)
            try:
                stdout, stderr = process.communicate(stdin_text, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.communicate()
                raise TimeoutError("local reasoning runtime timed out") from exc
            finally:
                with self._state_lock:
                    self._active.discard(process)
            return stdout[: self.max_output_chars], stderr[:2000], int(process.returncode or 0)

    @staticmethod
    def _messages(message: str, history: list, system_prompt: str | None) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for item in history or []:
            if isinstance(item, dict):
                messages.extend([
                    {"role": "user", "content": item.get("user_message", "")},
                    {"role": "assistant", "content": item.get("assistant_response", "")},
                ])
        messages.append({"role": "user", "content": message})
        return messages

    def _bound_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        size = 0
        for item in reversed(messages):
            encoded = json.dumps(item, ensure_ascii=False, default=str)
            if selected and size + len(encoded) > self.max_context_chars:
                break
            selected.append(item)
            size += len(encoded)
        return list(reversed(selected))

    @staticmethod
    def _normalize_output(raw: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed local reasoning output: invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("malformed local reasoning output: invalid JSON")
        if isinstance(payload.get("choices"), list) and payload["choices"]:
            choice = payload["choices"][0]
            message = choice.get("message", {}) if isinstance(choice, dict) else {}
        else:
            choice = {}
            message = {"role": "assistant", "content": payload.get("text")}
        if not isinstance(message, dict):
            raise ValueError("malformed local reasoning output: invalid message")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            content = str(content)
        normalized_message: dict[str, Any] = {"role": "assistant", "content": content}
        if isinstance(message.get("reasoning_content"), str):
            normalized_message["reasoning_content"] = message["reasoning_content"]
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            normalized_calls = []
            for index, call in enumerate(calls):
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                name = str(function.get("name") or "").strip()
                if not name:
                    continue
                arguments = function.get("arguments", {})
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                normalized_calls.append({
                    "id": str(call.get("id") or f"local_call_{index}"),
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                })
            if normalized_calls:
                normalized_message["tool_calls"] = normalized_calls
        if normalized_message["content"] is None and "tool_calls" not in normalized_message:
            raise ValueError("malformed local reasoning output: no usable content or tool call")
        return {
            "choices": [{
                "message": normalized_message,
                "finish_reason": "tool_calls" if "tool_calls" in normalized_message else choice.get("finish_reason", "stop"),
            }],
        }


__all__ = [
    "LOCAL_REASONING_CAPABILITY_ID",
    "LocalReasoningAdapter",
    "LocalReasoningReadiness",
]
