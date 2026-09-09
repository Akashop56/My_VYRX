"""Terminal preview of the live agent stream — a text-mode "fake Body".

Use it to see Step 1 (real-time CoT + typing effect) without a phone:

    cd RONIN_Brain_Python
    python3 devtools/stream_preview.py "open youtube" --base http://127.0.0.1:8000

It speaks the exact protocol the Kotlin Body speaks: it POSTs /ask_ronin with
``Accept: text/event-stream``, renders every thinking / thought / tool_call /
observation / self_correction frame as a terminal log line, types the answer
chunk-by-chunk, and — for device tools — executes a canned observation and
posts it back to /agent/result so the parked loop resumes.

With ``--fake-llm`` it also boots the Brain in-process with a scripted model,
so the whole thing runs with zero API keys:

    python3 devtools/stream_preview.py "open youtube" --fake-llm
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parent.parent
if str(BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(BRAIN_DIR))

# ANSI palette matching the app's dark-neon theme.
RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
PURPLE, BLUE, GREEN, AMBER, RED, FAINT = (
    "\033[38;5;141m", "\033[38;5;39m", "\033[38;5;42m",
    "\033[38;5;214m", "\033[38;5;203m", "\033[38;5;244m",
)
LEVEL_COLOR = {
    "info": GREEN, "success": GREEN, "tool": BLUE, "warning": AMBER, "error": RED,
    "thinking": PURPLE, "answer": PURPLE, "recall": FAINT, "route": FAINT, "prompt": FAINT,
}


def paint(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def one_line(text: str, limit: int = 160) -> str:
    return " ".join(str(text or "").split())[:limit]


def render(event: dict, state: dict) -> None:
    """Print one SSE frame the way the Body's terminal block would."""
    kind = event.get("type")
    clock = time.perf_counter() - state["t0"]
    stamp = paint("[%6.2fs]" % clock, FAINT)

    if kind == "start":
        print(stamp + " " + paint("◉ VYRX", BOLD) + " " + str(event.get("request_id"))
              + " · " + str(event.get("message")))
    elif kind == "thinking":
        phase = str(event.get("phase", "thinking"))
        print(stamp + " " + paint("> " + one_line(event.get("text"), 200),
                                  LEVEL_COLOR.get(phase, PURPLE)))
    elif kind == "thought":
        if event.get("final"):
            text = event.get("text")
            if text is not None:
                print(stamp + " " + paint("  " + one_line(text), DIM))
        else:
            print(paint("  " + one_line(event.get("delta")), DIM), end="", flush=True)
    elif kind == "tool_call":
        args = json.dumps(event.get("args") or {}, ensure_ascii=False)
        where = "Body/device" if event.get("device") else "Brain"
        print(stamp + " " + paint("▶ CALL ", BLUE) + paint(str(event.get("tool", "?")), BOLD)
              + paint(" [" + where + "] ", FAINT) + paint(one_line(args, 120), DIM))
    elif kind == "observation":
        ok = bool(event.get("ok"))
        mark, color = ("✔", GREEN) if ok else ("✖", RED)
        print(stamp + " " + paint(mark + " OBS ", color) + str(event.get("tool")) + " "
              + paint("(%s ms)" % event.get("ms"), FAINT) + " "
              + paint(one_line(event.get("result")), DIM))
    elif kind == "self_correction":
        print(stamp + " " + paint("↺ REFLEXION ", AMBER)
              + paint(one_line(event.get("reason"), 140), AMBER)
              + paint(" → " + one_line(event.get("strategy"), 120), DIM))
    elif kind == "log":
        level = str(event.get("level", "info"))
        print(stamp + " " + paint("  · " + one_line(event.get("text")), LEVEL_COLOR.get(level, FAINT)))
    elif kind == "state":
        print(stamp + " " + paint("  ● state → " + str(event.get("state")), FAINT)
              + " " + str(event.get("message", "")))
    elif kind == "answer_end":
        print()
    elif kind == "done":
        print(stamp + " " + paint("◆ done", BOLD) + " " + paint(
            "route=%s steps=%s %s ms" % (event.get("route"), event.get("steps"),
                                         event.get("elapsed_ms")), FAINT))
        if event.get("error"):
            print("        " + paint("error: " + str(event["error"]), RED))
    elif kind == "error":
        print(stamp + " " + paint("⚠ " + str(event.get("message")), RED))


async def preview(message: str, base: str, session: str, typing: bool, raw: bool = False) -> int:
    import httpx

    state = {"t0": time.perf_counter()}
    answer: list[str] = []
    exit_code = 0
    pending: dict | None = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        async with client.stream(
            "POST", f"{base}/ask_ronin",
            json={"message": message, "session_id": session, "stream": True},
            headers={"Accept": "text/event-stream"},
        ) as response:
            if not response.is_success:
                print(paint(f"HTTP {response.status_code}: {(await response.aread()).decode()[:400]}", RED))
                return 1
            print(paint(f"connected · {base}/ask_ronin (SSE)\n", FAINT))
            event_name, data_lines = None, []

            async def dispatch() -> None:
                nonlocal pending
                if not data_lines:
                    return
                event = json.loads("\n".join(data_lines))
                if raw:
                    # Wire format, exactly as the Brain framed it.
                    print(FAINT + "    " + json.dumps(event, ensure_ascii=False,
                                                       separators=(",", ":"))[:300] + RESET)
                if event.get("type") == "token":
                    chunk = event.get("text", "")
                    answer.append(chunk)
                    sys.stdout.write(paint(chunk, RESET))
                    sys.stdout.flush()
                    if typing:
                        await asyncio.sleep(0.02)
                    return
                render(event, state)
                if event.get("type") == "tool_call" and event.get("device") and event.get("action"):
                    pending = event["action"]

            async for line in response.aiter_lines():
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                    continue
                if not line:
                    await dispatch()
                    event_name, data_lines = None, []
                    if pending is not None:
                        # The Body executes the action locally, then reports back;
                        # the Brain resumes the same turn on the open stream.
                        action, pending = pending, None
                        await asyncio.sleep(0.4)  # fake tap/open latency
                        await client.post(f"{base}/agent/result", json={
                            "session_id": session, "tool": action["tool"],
                            "result": f"App launched: {action.get('args', {}).get('package', '?')}",
                            "success": True, "tool_call_id": action.get("tool_call_id"),
                        })
            print()
    return exit_code


def run_fake_brain(message: str, session: str, typing: bool, raw: bool = False) -> None:
    """Boot the Brain in-process with a scripted model (no keys, no port juggling)."""
    import httpx

    from core import planner

    scripted = {
        0: {"choices": [{"message": {"role": "assistant", "content":
            "Boss wants YouTube. I will open it, then verify with a screen read.",
            "tool_calls": [{"id": "call_preview", "type": "function", "function": {
                "name": "open_app",
                "arguments": json.dumps({"package": "com.google.android.youtube"})}}]}}]},
        1: {"choices": [{"message": {"role": "assistant", "content":
            "Done, Boss. YouTube is open and the first video is playing — I checked the "
            "screen to confirm the player is running."}}]},
    }

    def fake_complete(msg, history, providers, system_prompt=None, tools=None, messages=None,
                      on_provider=None, on_delta=None):
        step = len([m for m in (messages or []) if m.get("role") == "tool"])
        if on_delta is not None:
            for piece in ["Analyzing the request… ", "goal: launch YouTube and verify. ",
                          "Tool available: open_app."]:
                on_delta(piece)
                time.sleep(0.05)
        return scripted[step]

    planner.complete = fake_complete
    planner.RATE_LIMIT_PAUSE_SECONDS = 0.3

    import uvicorn

    from main import app

    async def drive() -> int:
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8199, log_level="error"))
        serving = asyncio.create_task(server.serve())
        try:
            async with httpx.AsyncClient() as client:
                for _ in range(150):
                    try:
                        if (await client.get("http://127.0.0.1:8199/health")).status_code == 200:
                            break
                    except Exception:
                        await asyncio.sleep(0.1)
                else:
                    print(paint("Brain did not come up on :8199", RED))
                    return 1
            print(paint("fake-llm mode: scripted open_app → observation → final answer\n", AMBER))
            return await preview(message, "http://127.0.0.1:8199", session, typing, raw)
        finally:
            server.should_exit = True
            await serving

    raise SystemExit(asyncio.run(drive()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview the VYRX live agent SSE stream.")
    parser.add_argument("message", nargs="?", default="open youtube")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--session", default="devtools-preview")
    parser.add_argument("--no-typing", action="store_true", help="print answer chunks instantly")
    parser.add_argument("--fake-llm", action="store_true", help="run the Brain in-process with a scripted model")
    parser.add_argument("--raw", action="store_true", help="also print every decoded frame as JSON")
    args = parser.parse_args()
    if args.fake_llm:
        run_fake_brain(args.message, args.session, not args.no_typing, args.raw)
        return
    raise SystemExit(asyncio.run(preview(args.message, args.base, args.session,
                                       not args.no_typing, args.raw)))


if __name__ == "__main__":
    main()
