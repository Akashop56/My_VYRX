# My_VYRX — VYRX 2099 Autonomous OS Core

VYRX is a system-level autonomous agent for Android: a Python **Brain**
(ReAct planner, persistent memory, self-healing shell) and a Kotlin **Body**
(accessibility God-mode, notification ear, voice).

- Brain: `RONIN_Brain_Python/` — FastAPI on `:8000` (`/ask_ronin`, `/agent/result`, `/agent/tools`)
- Body: `RONIN_Body_Kotlin/` — native Android app driving the device
- Real-time: `/ask_ronin` streams the loop (CoT, tool calls, observations, typing effect)
  over SSE — see [`docs/REALTIME_STREAMING.md`](docs/REALTIME_STREAMING.md)
- Architecture: [`docs/AGENTIC_ARCHITECTURE.md`](docs/AGENTIC_ARCHITECTURE.md)

```bash
cd RONIN_Brain_Python && python3 -m pytest tests/ -q

# Watch the agentic loop stream, no phone required:
python3 devtools/stream_preview.py "open youtube" --fake-llm
```
