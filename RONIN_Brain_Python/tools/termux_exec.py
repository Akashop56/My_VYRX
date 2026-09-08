"""Code execution & self-healing tools for the ReAct agent (Brain-side).

:func:`run_termux_command` gives the agent a shell: inspect state, read logs,
diagnose failures and repair the Brain itself. :func:`read_file`,
:func:`write_file` and :func:`list_files` provide scoped workspace file
access for config rewrites. All executions are logged to stdout so they show
up in the Brain process log for audit.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
MAX_OUTPUT_CHARS = 4000

#: Hard blocklist: never execute commands matching these (substring, lowercase).
_BLOCKED_SUBSTRINGS = (
    "rm -rf /", "rm -rf ~", "rm -rf /*", ":(){", "mkfs", "dd if=", "dd of=/dev",
    "shutdown", "reboot", "> /dev/sda", "> /dev/mmc",
)

#: Refuse to write outside the Brain workspace (configs/tools only).
_ALLOWED_WRITE_SUFFIXES = {".json", ".py", ".txt", ".md", ".sh", ".log"}


def _resolve_inside_workspace(path: str) -> Path | None:
    try:
        target = (WORKSPACE / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    except (OSError, ValueError):
        return None
    try:
        target.relative_to(WORKSPACE.resolve())
    except ValueError:
        return None
    return target


def run_termux_command(command: str, timeout: int = 15) -> dict:
    """Execute a Termux shell command and return stdout/stderr/returncode.

    Use for diagnostics and self-healing: check files, read logs, verify
    configs, reinstall missing pieces. Output is truncated to 4000 chars.
    Destructive commands are refused.
    """
    command = (command or "").strip()
    if not command:
        return {"error": "command must not be empty"}
    lowered = " ".join(command.lower().split())
    for blocked in _BLOCKED_SUBSTRINGS:
        if blocked in lowered:
            return {"error": f"refused destructive command (matched {blocked!r})",
                    "command": command[:200]}
    try:
        timeout = max(1, min(120, int(timeout)))
    except (TypeError, ValueError):
        timeout = 15
    started = time.monotonic()
    print(f"[VYRX exec] $ {command[:300]}", flush=True)
    try:
        completed = subprocess.run(
            command, shell=True, cwd=str(WORKSPACE), capture_output=True,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        return {"command": command[:500], "timeout": True,
                "returncode": None, "elapsed_ms": int((time.monotonic() - started) * 1000),
                "output": out[-MAX_OUTPUT_CHARS:]}
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent
        return {"command": command[:500], "error": f"execution failed: {exc}"}
    output = (completed.stdout or "") + (completed.stderr or "")
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[-MAX_OUTPUT_CHARS:] + "\n…(truncated)"
    return {"command": command[:500], "returncode": completed.returncode,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "output": output, "python": sys.version.split()[0]}


def read_file(path: str, max_chars: int = 4000) -> dict:
    """Read a Brain workspace file (Termux side). Path is relative to the Brain root."""
    if not (path or "").strip():
        return {"error": "path must not be empty"}
    target = _resolve_inside_workspace(path.strip())
    if target is None or not target.is_file():
        return {"error": f"file not found in workspace: {path[:200]}"}
    try:
        max_chars = max(100, min(20000, int(max_chars)))
    except (TypeError, ValueError):
        max_chars = 4000
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": f"read failed: {exc}"}
    truncated = len(text) > max_chars
    return {"path": str(target.relative_to(WORKSPACE.resolve())), "size": len(text),
            "truncated": truncated, "content": text[:max_chars]}


def write_file(path: str, content: str) -> dict:
    """Rewrite a Brain workspace file (self-healing config/code repair).

    Only .json/.py/.txt/.md/.sh/.log files INSIDE the Brain workspace may be
    written. Parent directories are created as needed.
    """
    if not (path or "").strip():
        return {"error": "path must not be empty"}
    target = _resolve_inside_workspace(path.strip())
    if target is None:
        return {"error": "refused: path escapes the Brain workspace"}
    if target.suffix.lower() not in _ALLOWED_WRITE_SUFFIXES:
        return {"error": f"refused: suffix {target.suffix!r} is not writable"}
    if target.suffix.lower() == ".py":
        try:
            compile(content or "", str(target), "exec")
        except SyntaxError as exc:
            return {"error": f"refused: Python syntax invalid: {exc}"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content or "", encoding="utf-8")
    except OSError as exc:
        return {"error": f"write failed: {exc}"}
    print(f"[VYRX self-heal] rewrote {target.relative_to(WORKSPACE.resolve())}", flush=True)
    return {"path": str(target.relative_to(WORKSPACE.resolve())),
            "bytes_written": len(content or "")}


def list_files(path: str = ".", limit: int = 50) -> dict:
    """List Brain workspace files (relative paths, largest dirs skipped)."""
    target = _resolve_inside_workspace((path or ".").strip() or ".")
    if target is None or not target.exists():
        return {"error": f"path not found in workspace: {path[:200]}"}
    try:
        limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit = 50
    entries: list[str] = []
    skip = {"__pycache__", ".git", "node_modules", ".venv"}
    if target.is_file():
        return {"path": path, "files": [target.name]}
    for child in sorted(target.iterdir()):
        if child.name in skip:
            continue
        entries.append(child.name + ("/" if child.is_dir() else ""))
        if len(entries) >= limit:
            break
    return {"path": str(target.relative_to(WORKSPACE.resolve()) or "."),
            "files": entries, "count": len(entries)}
