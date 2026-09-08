# VYRX 2099 — Autonomous OS Core Architecture

> Brain v2.0.0. The chatbot wrapper is gone. VYRX is now a ReAct agent:
> it **reasons, acts through tools, observes results, self-corrects**,
> and only speaks when the task is verifiably done.

## 1. The ReAct Loop (Python Brain)

```
User ──POST /ask_ronin──▶ Planner
                              │ ① inject persistent memory into system prompt
                              ▼
                         LLM (native function calling,
                              or <tool> JSON fallback)
                              │ ② Thought → Action
                    ┌─────────┴─────────┐
                    │                   │
              BRAIN tool            DEVICE tool
           (memory/web/shell)    (app/screen/click/…)
                    │                   │
              execute inline    dispatch AgentAction ──▶ Kotlin Body
                    │              executes, POSTs observation
                    │              back to /agent/result ◀──┘
                    ▼                   │
              Observation ──────────────┘
                    │ ③ verify → self-correct → next step (max 8)
                    ▼
              Final plain-text speech (TTS-ready, no JSON/tags)
```

Key files:

| File | Role |
|---|---|
| `RONIN_Brain_Python/core/planner.py` | `_react_loop`, `_run_llm_unchecked`, `continue_with_tool_result`, pending-session store, offline fallbacks |
| `RONIN_Brain_Python/core/llm_handler.py` | Agent system prompt, native tools (OpenAI/Groq/OpenRouter/**Gemini functionDeclarations**/custom), `<tool>` tag parsing (`extract_tool_calls`, `parse_tool_tag_calls`, `strip_tool_tags`) |
| `RONIN_Brain_Python/core/tools_catalog.py` | `DEVICE_TOOLS` vs `BRAIN_TOOLS` routing + OpenAI schemas for all 11 device tools |
| `RONIN_Brain_Python/core/router.py` | Agent-first routing; `legacy_route()` = offline fallback (no keys needed) |
| `RONIN_Brain_Python/core/schemas.py` | `AgentAction`, `ToolResultRequest`, `AskResponse.needs_tool_result` |
| `RONIN_Brain_Python/main.py` | `POST /agent/result` (hidden callback), `GET /agent/tools` (inspect surface) |

## 2. Action Protocol

Native function calling is preferred. Every provider also supports the
strict fallback — exactly one tag, nothing else in the message:

```html
<tool>{"tool": "open_app", "args": {"package": "com.google.android.youtube"}}</tool>
```

Device tools execute on the Body; Brain tools execute inline in Python:

- **Device:** `open_app`, `read_screen`, `click_xy`, `click_node`, `click`,
  `set_text`, `scroll`, `press_back`, `press_home`, `get_notifications`, `list_apps`
- **Brain:** `save_memory`, `retrieve_memory`, `search`, `run_termux_command`,
  `read_file`, `write_file`, `list_files`

## 3. Accessibility God-Mode (Kotlin Body)

- `services/RoninAccessibilityService.kt` — full implementation:
  `dumpScreen()` (text, content-descriptions, clickable bounds, node ids),
  `tapAt(x, y)` (suspend gesture), `clickNodeById()` (fresh-tree resolution
  + coordinate fallback), `clickText()`, `setFocusedText()`, `scroll()`.
- `service/AccessibilityHelperService.kt` — the manifest-bound service:
  runtime config, `helperInstance`/`isEnabled`, foreground-package tracking.
- `res/xml/accessibility_service_config.xml` — `canPerformGestures`,
  `flagRetrieveInteractiveWindows`, `flagReportViewIds`.
- `services/RoninNotificationListener.kt` — 30-item ring buffer behind
  `get_notifications`.

## 4. Self-Learning Memory

- Every `/ask_ronin` injects keyword-ranked `[MEMORY]` standing orders
  (`MemoryEngine.search_relevant` + `search_facts`) into the system prompt.
- The agent **must** call `save_memory` when Boss corrects/teaches it, before
  speaking the final answer; a safety net in the planner persists explicit
  "remember X" requests even if the model forgets the call.
- Tools: `tools/agent_memory.py` (`save_memory`, `retrieve_memory`),
  shared SQLite schema with `MemoryEngine`.

## 5. Self-Healing Execution

`tools/termux_exec.py`: `run_termux_command` (blocklist-guarded shell),
`read_file` / `write_file` (workspace-scoped, `.py` syntax-validated),
`list_files`. The agent inspects logs/configs, repairs the smallest thing,
and re-verifies. All executions are echoed to the Brain process log.

## 6. Kotlin↔Python Bridge

- `network/Models.kt` — `AgentAction` (tool + args + thought), extended
  `AskResponse` (`action`, `needsToolResult`, `steps`).
- `network/ApiClient.kt` — `submitToolResult()` → `POST /agent/result`;
  extended `parseAsk()`.
- `utils/CommandExecutor.kt` — `executeAction()` (suspend) maps every device
  tool to accessibility/package-manager calls and returns `AgentResult`
  observations; legacy `execute()` kept for the offline path.
- `ui/chat/ChatController.kt` — `runAgentLoop()`: executes dispatched
  actions and posts observations back automatically (max 8 round-trips);
  interim "⚙️ Acting" rows are hidden again; `lastSpeech` holds TTS-ready
  final text. Stable per-controller `sessionId` binds continuations.

## 7. Failure Semantics

- Tool error (either side) → observation fed back → agent self-corrects
  (retry differently, max 2 identical retries per the prompt).
- LLM down mid-task → `llm_unavailable`; explicit commands fall back to the
  offline keyword path (`legacy_route`) so "open X" still works keyless.
- `/agent/result` with no pending session → `no_pending_action`, clean retry.
- Step budget (8 LLM round-trips) → summarize instead of looping forever.

## 8. Verifying

```bash
cd RONIN_Brain_Python
python3 -m pytest tests/ -q          # 21 tests: ReAct, tools, memory, bridge contract
curl localhost:8000/agent/tools      # live tool surface
```

Android: `./gradlew :app:assembleDebug`, enable VYRX in Accessibility +
Notification access, then say "open YouTube" and watch the action log stream.
