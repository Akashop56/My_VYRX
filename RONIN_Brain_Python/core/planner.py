"""VYRX Planner: autonomous ReAct agent (Thought -> Action -> Observation).

Every user request flows through:

    1. Planner   : receive request, inject persistent memory into the prompt
    2. Agent loop: LLM emits native function calls or ``<tool>`` JSON tags
    3. Executor  : Brain tools run inline (memory / web / shell); device tools
                   (open app / read screen / click / …) are dispatched to the
                   Kotlin Body as a pending ``AgentAction``
    4. Observer  : the Body POSTs the execution result to ``/agent/result``
                   and the loop continues — verify, self-correct, repeat —
                   until the goal is achieved and the agent speaks plain text.

Each step emits an action-log entry and a state change, which the body
consumes through ``GET /api/state``, ``GET /api/action_logs`` and the SSE
stream ``GET /api/events``.

``POST /ask_ronin`` can additionally run the whole loop *inside* a live SSE
stream (see :mod:`core.streaming`): ``thinking`` / ``thought`` / ``tool_call``
/ ``observation`` / ``self_correction`` frames are emitted as they happen, the
final plain-text answer is streamed chunk-by-chunk for the Body's typing
effect, and a dispatched device tool is awaited over the same connection
(:data:`core.streaming.TOOL_RESULT_BRIDGE`) instead of ending the request.
Streaming is purely additive: with no stream bound, every function below behaves
exactly as it always did.

Legacy keyword routes (``legacy_route``) survive as offline fallbacks when no
AI provider is reachable, so explicit commands keep working with zero keys.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from core.action_log import ActionLog
from core.llm_handler import (
    SYSTEM_PROMPT,
    LLMError,
    build_tool_instructions,
    complete,
    extract_tool_calls,
    strip_tool_tags,
)
from core.provider_manager import ProviderManager
from core.router import legacy_route, route_request
from core.schemas import AgentAction, AskRequest, AskResponse, ToolResultRequest, UpdateProposal
from core.state_manager import StateManager
from core.stats import StatsTracker
from core.streaming import (
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_OBSERVATION,
    EVENT_SELF_CORRECTION,
    EVENT_START,
    EVENT_THINKING,
    EVENT_TOOL_CALL,
    TOOL_RESULT_BRIDGE,
    TOOL_RESULT_TIMEOUT_SECONDS,
    AgentStreamer,
    bind_stream,
    current_stream,
    emit_event,
    unbind_stream,
)
from core.tool_registry import execute_tool, get_available_tools
from core.tools_catalog import (
    HIDDEN_LEGACY_TOOLS,
    device_tool_schemas,
    is_device_tool,
    tool_label,
)
from memory.db_manager import recent_history, save_conversation, search_facts, store_fact
from memory.memory_engine import MemoryEngine
from tools.system_control import command_for_request
from tools.web_search import search


#: Maximum LLM round-trips per user request (device callbacks included).
MAX_AGENT_STEPS = 8

#: Truncation for tool observations fed back into the context window.
MAX_OBSERVATION_CHARS = 6000

#: Politeness gap between ReAct steps (GROQ rate limit bypass). Kept as a
#: module constant so tests and local runs can retune it without patching
#: ``asyncio.sleep`` globally.
RATE_LIMIT_PAUSE_SECONDS: float = 2.0


async def _rate_limit_pause() -> None:
    if RATE_LIMIT_PAUSE_SECONDS > 0:
        await asyncio.sleep(RATE_LIMIT_PAUSE_SECONDS)


def _clip(text: str, limit: int = 64) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _response_text(message: dict) -> str:
    content = message.get("content")
    if content is None:
        return "No response generated."
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


def _assistant_message(completion: dict) -> dict:
    choices = completion.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return {"role": "assistant", "content": "No response generated."}
    message = choices[0].get("message")
    return message if isinstance(message, dict) else {"role": "assistant", "content": "No response generated."}


def _conversation_messages(message: str, history: list[dict[str, str]], system_prompt: str) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for item in history:
        messages.append({"role": "user", "content": item["user_message"]})
        messages.append({"role": "assistant", "content": item["assistant_response"]})
    messages.append({"role": "user", "content": message})
    return messages


def _assistant_replay_message(assistant_message: dict, tool_calls: list[dict]) -> dict:
    """Rebuild the assistant turn with OpenAI-wire tool calls for replay."""
    content = assistant_message.get("content")
    if content is not None and not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    wire_calls = []
    for call in tool_calls:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        args = function.get("arguments", {})
        wire_calls.append({
            "id": call.get("id", "") if isinstance(call, dict) else "",
            "type": "function",
            "function": {
                "name": str(function.get("name", "")),
                "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False, default=str),
            },
        })
    return {"role": "assistant", "content": content, "tool_calls": wire_calls}


PERSONALITY_SUFFIX: dict[str, str] = {
    "professional": " PERSONA OVERRIDE: Respond in a formal, professional tone.",
    "friendly": " PERSONA OVERRIDE: Respond in a warm, casual, friendly tone.",
    "creative": " PERSONA OVERRIDE: Respond with creative, imaginative phrasing.",
    "developer": " PERSONA OVERRIDE: Respond with a technical, developer-focused tone.",
}

RESPONSE_MODE_SUFFIX: dict[str, str] = {
    "fast": " RESPONSE MODE: Keep responses short and direct; prefer the minimum useful detail.",
    "balanced": "",
    "deep": " RESPONSE MODE: Reason deeply and provide detailed, thorough explanations.",
}


def build_system_prompt(personality: str | None, response_mode: str | None) -> str:
    prompt = SYSTEM_PROMPT
    if personality and personality.lower() in PERSONALITY_SUFFIX:
        prompt += PERSONALITY_SUFFIX[personality.lower()]
    if response_mode and response_mode.lower() in RESPONSE_MODE_SUFFIX:
        prompt += RESPONSE_MODE_SUFFIX[response_mode.lower()]
    return prompt


async def _memory_context(ctx: BrainContext, message: str, limit: int = 5) -> str:
    """Standing orders from persistent memory, injected into every prompt."""
    try:
        engine = getattr(ctx, "memory_engine", None)
        if engine is None:
            return ""
        relevant = await engine.search_relevant(message, limit)
        facts = await search_facts(message, 3)
        if not relevant and not facts:
            return ""
        lines = ["", "[MEMORY — standing orders from past learnings; obey unless Boss overrides them now]"]
        for item in (relevant or [])[:limit]:
            lines.append(
                f"- ({item.get('category', 'personal')}|importance {item.get('importance', 3)}) "
                f"{item.get('title', '')}: {str(item.get('content', ''))[:300]}"
            )
        for fact in (facts or [])[:3]:
            lines.append(f"- (fact) {str(fact.get('fact_value', ''))[:300]}")
        return "\n".join(lines)
    except Exception:
        return ""


@dataclass
class BrainContext:
    """Everything the planner needs, owned by the app lifespan in main.py."""
    log: ActionLog
    state: StateManager
    stats: StatsTracker
    provider_manager: ProviderManager
    memory_engine: MemoryEngine
    device_status: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Live stream helpers
#
# A request either owns an AgentStreamer (streamed /ask_ronin) or none at all
# (legacy JSON contract). Every helper below degrades to a no-op in the second
# case, which is what keeps the pre-existing behaviour byte-for-byte identical.
# ---------------------------------------------------------------------------

def _stream() -> AgentStreamer | None:
    return current_stream()


def _thinking(text: str, *, step: int = 0, phase: str = "plan") -> None:
    emit_event(EVENT_THINKING, step=step, phase=phase, text=text)


def _tool_call(step: int, tool: str, arguments: dict, *, device: bool,
               label: str | None = None, thought: str | None = None,
               action: dict | None = None) -> None:
    emit_event(
        EVENT_TOOL_CALL,
        step=step,
        tool=tool,
        label=label or tool_label(_tool_catalog_id(tool)),
        args=arguments,
        device=device,
        thought=thought or None,
        action=action,
    )


def _observation(step: int, tool: str, ok: bool, result: str, ms: int) -> None:
    emit_event(EVENT_OBSERVATION, step=step, tool=tool, ok=bool(ok), ms=int(ms),
               result=_clip(str(result), 320))


def _self_correction(step: int, tool: str | None, reason: str, strategy: str,
                     attempt: int = 0) -> None:
    """One frame per healing attempt; ``reflexion`` is the semantic alias."""
    emit_event(
        EVENT_SELF_CORRECTION,
        step=step,
        tool=tool,
        reason=_clip(reason, 240),
        strategy=strategy,
        attempt=attempt,
        reflexion=_clip(reason, 240),
    )


def _correction_for_failure(result_text: str, tool_name: str) -> str:
    """Turn a raw tool failure into the agent's stated recovery strategy."""
    lowered = str(result_text or "").casefold()
    if "timeout" in lowered:
        return "retry with a shorter timeout, then degrade gracefully"
    if "not found" in lowered or "no such" in lowered or "error" in lowered:
        return f"change strategy instead of repeating {tool_name} (re-inspect, different args, or another tool)"
    return "re-read the state and try a different approach"


# ---------------------------------------------------------------------------
# Pending device-action sessions (Body executes, then calls back)
# ---------------------------------------------------------------------------

_AGENT_SESSIONS: dict[str, dict[str, Any]] = {}
_AGENT_LOCK = threading.Lock()


def _save_agent_session(session_id: str, payload: dict[str, Any]) -> None:
    with _AGENT_LOCK:
        if len(_AGENT_SESSIONS) >= 100:
            _AGENT_SESSIONS.pop(next(iter(_AGENT_SESSIONS)), None)
        _AGENT_SESSIONS[session_id] = payload


def _load_agent_session(session_id: str) -> dict[str, Any] | None:
    with _AGENT_LOCK:
        session = _AGENT_SESSIONS.get(session_id)
        return dict(session) if session is not None else None


def _clear_agent_session(session_id: str) -> None:
    with _AGENT_LOCK:
        _AGENT_SESSIONS.pop(session_id, None)


# ---------------------------------------------------------------------------
# Settings helpers (moved from main.py so the planner is self-contained)
# ---------------------------------------------------------------------------

def _runtime_settings(settings_path: Path) -> dict:
    defaults = {"tool_execution_mode": "full", "developer_mode": True, "allow_dynamic_tools": True}
    try:
        values = json.loads(settings_path.read_text(encoding="utf-8"))
        return {**defaults, **values} if isinstance(values, dict) else defaults
    except (OSError, json.JSONDecodeError):
        return defaults


def _tool_execution_enabled(settings: dict) -> bool:
    mode = str(settings.get("tool_execution_mode", "full")).strip().lower()
    return (bool(settings.get("developer_mode", True))
            and bool(settings.get("allow_dynamic_tools", True))
            and mode not in {"disabled", "off", "none"})


def _filter_tools(tools: list[dict], tools_enabled: dict[str, bool]) -> list[dict]:
    """Map UI tool toggles onto registry tool names."""
    if not tools_enabled:
        return tools
    return [tool for tool in tools if tools_enabled.get(
        _tool_catalog_id(tool.get("function", {}).get("name", "")), True
    )]


def _tool_catalog_id(function_name: str) -> str:
    return {
        # Brain tools
        "search": "web_search",
        "save_memory": "agent_memory",
        "retrieve_memory": "agent_memory",
        "run_termux_command": "terminal",
        "read_file": "file_manager",
        "write_file": "file_manager",
        "list_files": "file_manager",
        # Device tools
        "open_app": "app_control",
        "list_apps": "app_control",
        "click": "app_control",
        "set_text": "app_control",
        "scroll": "app_control",
        "press_back": "app_control",
        "press_home": "app_control",
        "read_screen": "screen_reader",
        "click_xy": "screen_reader",
        "click_node": "screen_reader",
        "get_notifications": "notification_manager",
        # Legacy
        "command_for_request": "app_control",
    }.get(function_name, function_name)


# ---------------------------------------------------------------------------
# Route executors (offline fallbacks + developer agent)
# ---------------------------------------------------------------------------

async def _run_android_command(request: AskRequest, ctx: BrainContext) -> AskResponse:
    # Device-side execution is enforced by the body (Accessibility service);
    # the brain only checks whether the user disabled the tool in the UI.
    if _tool_disabled(request, "app_control"):
        return AskResponse(
            response="App Control is disabled in the Tools module. Enable it to run device commands.",
            route="android_command",
        )
    command = command_for_request(request.message)
    ctx.state.set("executing", f"Executing: {command.action}...")
    ctx.log.log(f"Executing device command: {command.action}", "tool")
    # The offline/legacy path hands the command to the Body in the response, so
    # there is no observation to wait for — still surface the intent in the log.
    _tool_call(0, command.action, command.model_dump(mode="json", exclude_none=True), device=True,
               label=f"Device command · {command.action}")
    _observation(0, command.action, True, "Queued on the Body for execution.", 0)
    await ctx.stats.bump("apps_opened")
    await ctx.stats.record_tool_usage("app_control", command.action, True)
    return AskResponse(
        response=f"Approved Android command prepared: {command.action}.",
        route="android_command",
        command=command,
    )


async def _run_web_search(request: AskRequest, ctx: BrainContext) -> AskResponse:
    if _tool_disabled(request, "web_search"):
        return AskResponse(
            response="Web Search is disabled in the Tools module. Enable it to search the web.",
            route="web_search",
        )
    query = request.message.split(" ", 2)[-1]
    ctx.state.set("executing", "Searching web...")
    ctx.log.log(f"Searching web for: \"{_clip(query, 48)}\"", "tool")
    _tool_call(0, "search", {"query": query}, device=False, label="Web Search")
    started = time.monotonic()
    try:
        data = await asyncio.to_thread(search, query)
    except Exception as exc:
        await ctx.stats.record_tool_usage("web_search", query, False)
        ctx.log.log(f"Web search failed: {exc}", "error")
        _observation(0, "search", False, str(exc), int((time.monotonic() - started) * 1000))
        _self_correction(0, "search", str(exc), "answer from memory/reasoning instead of search")
        return AskResponse(response=f"Web search failed: {exc}", route="web_search", error=str(exc))
    latency_ms = int((time.monotonic() - started) * 1000)
    results = data.get("results") or []
    ctx.log.log(f"Parsing {len(results)} results...", "info")
    _observation(0, "search", len(results) > 0, f"{len(results)} results parsed", latency_ms)
    await ctx.stats.bump("web_searches")
    await ctx.stats.record_tool_usage("web_search", query, len(results) > 0)
    response = "\n".join(f"{x['title']}: {x['snippet']}" for x in results) or "No web results were returned."
    return AskResponse(response=response, route="web_search")


async def _run_local_tool(request: AskRequest, ctx: BrainContext) -> AskResponse:
    fact = request.message.split(" ", 1)[-1]
    ctx.state.set("learning", "Saving memory...")
    ctx.log.log("Saving memory...", "info")
    _tool_call(0, "save_memory", {"content": _clip(fact, 120)}, device=False, label="Agent Memory")
    await store_fact(fact[:120].lower(), fact)
    title = _clip(fact, 48)
    await ctx.memory_engine.add(category="knowledge", title=title, content=fact,
                                importance=3, source="chat")
    await ctx.stats.bump("learned")
    await ctx.stats.record_tool_usage("note_creator", title, True)
    ctx.log.log(f"Memory saved: \"{title}\"", "success")
    _observation(0, "save_memory", True, f"Stored as \"{title}\"", 0)
    return AskResponse(response="Stored in persistent memory.", route="local_tool")


async def _run_tool_creation(request: AskRequest, ctx: BrainContext) -> AskResponse:
    """The Developer Agent: generate a new Python tool and propose it for approval."""
    tool_creation_system_prompt = (
        "You generate a single Python tool file for RONIN. "
        "Return only complete, valid raw Python source code for exactly one file. "
        "Do not use Markdown or code fences. Do not include explanations, tutorials, or prose. "
        "CRITICAL RULE: Write simple standalone Python functions (def). DO NOT create classes, BaseModels, or use fake AI tool frameworks. "
        "DO NOT import non-existent modules like 'rpn_tools'. Use only standard Python libraries and 'requests'. "
        "The entire response must compile as the requested Python tool."
    )
    ctx.state.set("thinking", "Generating code...")
    ctx.log.log("Generating new tool code...", "info")
    _thinking("Developer agent: writing a new Python tool", phase="call")
    provider_payload = [provider.model_dump() for provider in request.providers]
    started = time.monotonic()
    stream = _stream()
    completion = await asyncio.to_thread(
        complete, request.message, [], provider_payload,
        system_prompt=tool_creation_system_prompt,
        on_delta=stream.delta_forwarder(0) if stream is not None else None,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if stream is not None:
        stream.flush_delta(0)
    _record_provider_latency(ctx, completion, elapsed_ms, provider_payload)
    generated_code = _response_text(_assistant_message(completion))
    cleaned_code = generated_code.replace("```python", "").replace("```", "").strip()

    prop_id = str(uuid.uuid4())[:8]
    module_name = f"tools.dynamic_{prop_id}"
    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    file_path = str(tools_dir / f"dynamic_{prop_id}.py")
    proposal = UpdateProposal(
        proposal_id=prop_id,
        file_path=file_path,
        module_name=module_name,
        new_code=cleaned_code,
        summary=f"Generated a new tool for: {request.message}",
    )
    ctx.log.log("Tool code ready — awaiting approval", "success")
    response_text = "Boss, maine is tool ka Python code likh liya hai. Please screen par review aur approve kijiye."
    return AskResponse(response=response_text, route="tool_creation", update_proposal=proposal)


# ---------------------------------------------------------------------------
# ReAct agent loop
# ---------------------------------------------------------------------------

async def _run_llm(request: AskRequest, ctx: BrainContext) -> AskResponse:
    """Contain provider failures, including failures after a tool result."""
    try:
        return await _run_llm_unchecked(request, ctx)
    except LLMError:
        ctx.log.log("All configured AI providers failed", "error")
        ctx.state.set("idle", "AI unavailable. Please retry.")
        _self_correction(0, None, "all configured AI providers failed",
                          "offline legacy route (keyword commands without a key)")
        return AskResponse(
            response="The AI service is temporarily unavailable. Please check your provider settings and try again.",
            route="llm",
            error="llm_unavailable",
        )


async def _run_llm_unchecked(request: AskRequest, ctx: BrainContext) -> AskResponse:
    log, stats, pm, state = ctx.log, ctx.stats, ctx.provider_manager, ctx.state
    stream = _stream()
    history = await recent_history(request.session_id)
    if stream is not None and history:
        stream.emit(EVENT_THINKING, step=0, phase="recall",
                    text=f"Recalling {len(history)} previous turn(s) for this session")
    system_prompt = build_system_prompt(request.personality, request.response_mode)
    memory_block = await _memory_context(ctx, request.message)
    if memory_block:
        injected = max(0, len([line for line in memory_block.splitlines() if line.startswith("- ")]))
        log.log(f"Injecting {injected} standing order(s) from memory", "info")
        if stream is not None:
            stream.emit(EVENT_THINKING, step=0, phase="recall",
                        text=f"{injected} standing order(s) recalled from long-term memory")
    system_prompt += memory_block
    provider_payload = [provider.model_dump() for provider in request.providers]
    settings = _runtime_settings(Path(__file__).resolve().parent.parent / "config" / "settings.json")

    if _tool_execution_enabled(settings):
        server_tools = [
            tool for tool in get_available_tools()
            if tool.get("function", {}).get("name") not in HIDDEN_LEGACY_TOOLS
        ]
        server_tools = _filter_tools(server_tools, _enabled_map(request))
        device_tools = _filter_tools(device_tool_schemas(), _enabled_map(request))
        available_tools = server_tools + device_tools
    else:
        available_tools = []
    if available_tools:
        log.log(f"{len(available_tools)} tools available for this request", "info")
        system_prompt += build_tool_instructions(available_tools)
        if stream is not None:
            names = ", ".join(tool.get("function", {}).get("name", "?") for tool in available_tools[:8])
            stream.emit(EVENT_THINKING, step=0, phase="prompt",
                        text=f"{len(available_tools)} tools armed: {names}"
                              + ("…" if len(available_tools) > 8 else ""))

    state.set("thinking", "Calling API...")
    active = pm.active_name(provider_payload)
    if active:
        log.log(f"Calling {active} model...", "info")
    if stream is not None:
        stream.emit(EVENT_THINKING, step=0, phase="call",
                    text=f"Calling {active or 'default'} provider"
                         + (f" ({pm.model_for(active)})" if active else "") + " — reasoning…")
    provider_calls: dict[str, bool] = {}

    def _on_provider(name: str, ok: bool) -> None:
        provider_calls[name] = ok
        if not ok:
            # Failover is self-healing the user should see happening.
            emit_event(EVENT_SELF_CORRECTION, step=0, tool=None, reason=f"provider {name} failed",
                       strategy="failing over to the next configured provider", attempt=0,
                       reflexion=f"{name} failed — rotating provider")

    started = time.monotonic()
    messages = _conversation_messages(request.message, history, system_prompt)
    completion = await asyncio.to_thread(
        complete, request.message, history, provider_payload,
        system_prompt=system_prompt, tools=available_tools, on_provider=_on_provider,
        on_delta=stream.delta_forwarder(0) if stream is not None else None,
    )
    if stream is not None:
        stream.flush_delta(0, text=_stream_visible_thought(_assistant_message(completion)))
    assistant_message = _assistant_message(completion)
    return await _react_loop(
        ctx, provider_payload=provider_payload, available_tools=available_tools,
        messages=messages, assistant_message=assistant_message, completion=completion,
        provider_calls=provider_calls, on_provider=_on_provider,
        steps=0, brain_tools_called=[], session_id=request.session_id,
        original_message=request.message, started=started,
        input_mode=str(request.input_mode or "text"),
    )


def _stream_visible_thought(message: dict) -> str:
    """The model's own words for this step, safe to show in the terminal."""
    raw = message.get("reasoning_content") or message.get("content")
    if not isinstance(raw, str):
        return ""
    return strip_tool_tags(raw)[:1200]


def _arguments_preview(arguments: dict) -> str:
    try:
        return _clip(json.dumps(arguments, ensure_ascii=False, default=str), 120)
    except (TypeError, ValueError):
        return "{}"


async def _await_device_observation(
    session_id: str, tool_name: str, timeout: float | None = None,
) -> tuple[str, bool, str, bool]:
    """Park the live stream until the Body POSTs /agent/result for this session.

    Returns ``(observation_text, success, tool, timed_out)``. The legacy
    (non-streamed) contract never gets here: it ends the request and resumes
    from the saved agent session instead.
    """
    limit = timeout if timeout is not None else TOOL_RESULT_TIMEOUT_SECONDS
    future = TOOL_RESULT_BRIDGE.register(session_id)
    try:
        payload = await asyncio.wait_for(future, timeout=limit)
    except asyncio.TimeoutError:
        return ("", False, tool_name, True)
    finally:
        TOOL_RESULT_BRIDGE.release(session_id, future)
    result = str(payload.get("result") or "")
    success = bool(payload.get("success", True))
    tool = str(payload.get("tool") or tool_name)
    status = "succeeded" if success else "FAILED"
    return (f"[{tool} {status}]: {result.strip() or '(empty result)'}", success, tool, False)



async def _react_loop(
    ctx: BrainContext,
    *,
    provider_payload: list[dict],
    available_tools: list[dict],
    messages: list[dict],
    assistant_message: dict,
    completion: dict,
    provider_calls: dict[str, bool],
    on_provider,
    steps: int,
    brain_tools_called: list[str],
    session_id: str,
    original_message: str,
    started: float,
    input_mode: str = "text",
) -> AskResponse:
    """Thought -> Action -> Observation until final speech or device dispatch.

    With a live stream attached, dispatched device tools are awaited *inside*
    the loop (the Body answers on /agent/result, which resolves the bridge), so
    the whole ReAct run rides one SSE connection. Without a stream the dispatch
    returns a pending ``AgentAction`` exactly as before.
    """
    log, stats, state = ctx.log, ctx.stats, ctx.state
    stream = _stream()
    #: True once a dispatched device tool has been observed inside this stream.
    resume_after_dispatch = False

    while True:
        tool_calls = extract_tool_calls(assistant_message) if available_tools else []
        if not tool_calls:
            break
        if steps >= MAX_AGENT_STEPS:
            log.log("Agent step budget exhausted; summarizing", "warning")
            _self_correction(steps, None, f"step budget ({MAX_AGENT_STEPS}) exhausted",
                             "stop acting and summarize what is verified so far", attempt=steps)
            break

        messages.append(_assistant_replay_message(assistant_message, tool_calls))
        replay_index = len(messages) - 1
        brain_done = 0
        resume_after_dispatch = False

        for tool_call in tool_calls:
            function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
            tool_name = str(function.get("name", ""))
            arguments = function.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}

            raw_thought = assistant_message.get("content")
            thought = strip_tool_tags(raw_thought)[:2000] if isinstance(raw_thought, str) else None

            # --- Device tool: dispatch to the Kotlin Body, await callback ---
            if is_device_tool(tool_name):
                trimmed = tool_calls[: brain_done + 1]
                messages[replay_index] = _assistant_replay_message(assistant_message, trimmed)
                action_payload = {
                    "tool": tool_name, "args": arguments,
                    "tool_call_id": tool_call.get("id") if isinstance(tool_call, dict) else None,
                    "thought": thought or None,
                }
                _save_agent_session(session_id, {
                    "messages": messages,
                    "provider_payload": provider_payload,
                    "available_tools": available_tools,
                    "steps": steps,
                    "brain_tools_called": brain_tools_called,
                    "original_message": original_message,
                    "pending_tool": tool_name,
                    "pending_id": tool_call.get("id", "") if isinstance(tool_call, dict) else "",
                    "provider_calls": provider_calls,
                    "started": started,
                    "input_mode": input_mode,
                })
                catalog_id = _tool_catalog_id(tool_name)
                state.set("executing", f"Running {tool_label(catalog_id)} on device...")
                log.log(f"Dispatching device tool: {tool_name} { _clip(json.dumps(arguments, ensure_ascii=False), 80)}", "tool")
                _tool_call(steps, tool_name, arguments, device=True, thought=thought,
                           label=f"Executing {tool_name} on the Body", action=action_payload)
                if stream is None:
                    return AskResponse(
                        response="",
                        route="agent_action",
                        action=AgentAction(**action_payload),
                        needs_tool_result=True,
                        thought=thought or None,
                        steps=steps,
                    )
                # Live stream: keep the connection and wait for the observation.
                dispatch_started = time.monotonic()
                observation_text, device_ok, observed_tool, timed_out = await _await_device_observation(
                    session_id, tool_name)
                device_ms = int((time.monotonic() - dispatch_started) * 1000)
                if timed_out:
                    observation_text = f"[{tool_name} TIMEOUT]: no result from the Body in " \
                                       f"{int(TOOL_RESULT_TIMEOUT_SECONDS)} s"
                    log.log(f"Device tool {tool_name} timed out waiting for the Body", "error")
                    # Nobody is coming with an answer: drop the resume session so a
                    # late callback cannot run the loop a second time.
                    _clear_agent_session(session_id)
                if device_ok:
                    log.log(f"Device tool {tool_name} finished in {device_ms} ms", "success")
                else:
                    log.log(f"Device tool {tool_name} failed in {device_ms} ms — agent will self-correct",
                            "warning")
                _observation(steps, tool_name, device_ok, observation_text, device_ms)
                if not device_ok:
                    _self_correction(
                        steps, tool_name,
                        "device action timed out; retry with another strategy" if timed_out
                        else _clip(observation_text, 200),
                        _correction_for_failure(observation_text if not timed_out else "timeout", tool_name),
                        attempt=steps)
                messages.append({
                    "role": "tool",
                    "tool_call_id": action_payload.get("tool_call_id") or "",
                    "name": observed_tool or tool_name,
                    "content": observation_text[:MAX_OBSERVATION_CHARS],
                })
                await stats.record_tool_usage(_tool_catalog_id(observed_tool or tool_name),
                                              _clip(observation_text, 80), device_ok)
                # One dispatched action per step: reason over its observation now.
                resume_after_dispatch = True
                break

            # --- Brain tool: execute inline, feed observation back ---
            state.set("executing", f"Running {tool_label(_tool_catalog_id(tool_name))}...")
            log.log(f"Executing tool: {tool_name}", "tool")
            _tool_call(steps, tool_name, arguments, device=False, thought=thought,
                       label=f"Executing {tool_name} in the Brain")
            tool_started = time.monotonic()
            try:
                result_text = await asyncio.to_thread(execute_tool, tool_name, arguments)
            except Exception as exc:  # never let one tool kill the loop; observe + self-correct
                result_text = json.dumps({"error": f"Tool execution failed: {exc}"}, ensure_ascii=False)
            tool_ms = int((time.monotonic() - tool_started) * 1000)
            success = '"error"' not in result_text[:160]
            catalog_id = _tool_catalog_id(tool_name)
            await stats.record_tool_usage(catalog_id, _clip(json.dumps(arguments, ensure_ascii=False)[:80]), success)
            if catalog_id == "web_search":
                await stats.bump("web_searches")
            if catalog_id == "app_control":
                await stats.bump("apps_opened")
            if catalog_id in {"agent_memory", "note_creator"} and success:
                await stats.bump("learned")
            brain_tools_called.append(tool_name)
            _observation(steps, tool_name, success, result_text, tool_ms)
            if success:
                log.log(f"Tool {tool_name} finished in {tool_ms} ms", "success")
            else:
                # Self-correction fuel: the next Thought sees the error and adapts.
                log.log(f"Tool {tool_name} failed in {tool_ms} ms — agent will self-correct", "warning")
                _self_correction(steps, tool_name, _clip(result_text, 200),
                                 _correction_for_failure(result_text, tool_name), attempt=steps)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.get("id", "") if isinstance(tool_call, dict) else "",
                "name": tool_name,
                "content": result_text[:MAX_OBSERVATION_CHARS],
            })
            brain_done += 1

        # GROQ RATE LIMIT BYPASS: 2 second ka pause
        await _rate_limit_pause()
        state.set("thinking", "Reasoning over tool results...")
        _thinking("Reasoning over tool results…", step=steps + 1,
                  phase="reason" if not resume_after_dispatch else "verify")
        next_delta = stream.delta_forwarder(steps + 1) if stream is not None else None
        completion = await asyncio.to_thread(
            complete, "", [], provider_payload, tools=available_tools, messages=messages,
            on_delta=next_delta,
        )
        if stream is not None:
            stream.flush_delta(steps + 1, text=_stream_visible_thought(_assistant_message(completion)))
        assistant_message = _assistant_message(completion)
        steps += 1

    elapsed_ms = int((time.monotonic() - started) * 1000)
    _record_provider_latency(ctx, completion, elapsed_ms, provider_payload, provider_calls)
    final_text = strip_tool_tags(_response_text(assistant_message)) or _response_text(assistant_message)

    # Self-learning safety net: an explicit "remember X" must persist even if
    # the model forgot to call save_memory in this task.
    await _ensure_remember_persisted(ctx, original_message, brain_tools_called)

    acted = bool(brain_tools_called) or steps > 0 or resume_after_dispatch
    log.log("Response ready", "success")
    if stream is not None:
        # The task completed on this connection: no resume session to keep.
        _clear_agent_session(session_id)
    return AskResponse(
        response=final_text, route="agent_final" if acted else "llm", steps=steps,
    )


async def _ensure_remember_persisted(ctx: BrainContext, message: str, brain_tools_called: list[str]) -> None:
    value = " ".join(message.split())
    lowered = value.casefold()
    if not lowered.startswith(("remember ", "save fact ")):
        return
    if "save_memory" in brain_tools_called:
        return
    fact = value.split(" ", 1)[-1].strip()
    if not fact:
        return
    try:
        engine = getattr(ctx, "memory_engine", None)
        await store_fact(fact[:120].lower(), fact)
        if engine is not None:
            title = _clip(fact, 48)
            await engine.add(category="knowledge", title=title, content=fact,
                             importance=3, source="chat")
        await ctx.stats.bump("learned")
        ctx.log.log("Auto-saved to memory (agent skipped save_memory)", "info")
    except Exception as exc:
        ctx.log.log(f"Memory safety-net failed: {exc}", "warning")


async def continue_with_tool_result(request: ToolResultRequest, ctx: BrainContext) -> AskResponse:
    """Resume the ReAct loop with a Body execution observation (``/agent/result``)."""
    log, state, stats = ctx.log, ctx.state, ctx.stats
    session = _load_agent_session(request.session_id)
    if session is None:
        log.log("Tool result arrived with no pending action", "warning")
        return AskResponse(
            response="I lost the thread of that action, Boss. Please ask again.",
            route="agent_final",
            error="no_pending_action",
        )

    messages = list(session.get("messages", []))
    provider_payload = session.get("provider_payload", [])
    available_tools = session.get("available_tools", [])
    steps = int(session.get("steps", 0))
    brain_tools_called = list(session.get("brain_tools_called", []))
    original_message = str(session.get("original_message", ""))
    provider_calls = dict(session.get("provider_calls", {}))
    started = float(session.get("started", time.monotonic()))
    pending_id = str(session.get("pending_id", "") or request.tool_call_id or "")
    input_mode = str(session.get("input_mode", "text") or "text")

    status = "succeeded" if request.success else "FAILED"
    observation = f"[{request.tool} {status}]: {(request.result or '').strip() or '(empty result)'}"
    messages.append({
        "role": "tool",
        "tool_call_id": pending_id,
        "name": request.tool,
        "content": observation[:MAX_OBSERVATION_CHARS],
    })
    catalog_id = _tool_catalog_id(request.tool)
    await stats.record_tool_usage(catalog_id, _clip(request.result or "", 80), request.success)
    if request.tool == "open_app" and request.success:
        await stats.bump("apps_opened")
    if request.success:
        log.log(f"Device tool {request.tool} finished", "success")
    else:
        log.log(f"Device tool {request.tool} failed — agent will self-correct", "warning")

    def _on_provider(name: str, ok: bool) -> None:
        provider_calls[name] = ok

    state.set("thinking", "Reasoning over device result...")
    await _rate_limit_pause()  # GROQ RATE LIMIT BYPASS
    try:
        completion = await asyncio.to_thread(
            complete, "", [], provider_payload, tools=available_tools,
            messages=messages,
        )
    except LLMError:
        log.log("All configured AI providers failed", "error")
        state.set("idle", "AI unavailable. Please retry.")
        return AskResponse(
            response="The AI service dropped mid-action, Boss. Please try again.",
            route="agent_final",
            error="llm_unavailable",
        )
    assistant_message = _assistant_message(completion)
    result = await _react_loop(
        ctx, provider_payload=provider_payload, available_tools=available_tools,
        messages=messages, assistant_message=assistant_message, completion=completion,
        provider_calls=provider_calls, on_provider=_on_provider,
        steps=steps + 1, brain_tools_called=brain_tools_called,
        session_id=request.session_id, original_message=original_message, started=started,
        input_mode=input_mode,
    )
    if not result.needs_tool_result:
        _clear_agent_session(request.session_id)
        # The task completes here: persist the full turn + finalize stats.
        await save_conversation(request.session_id, original_message, result.response)
        if result.error is None:
            await stats.bump("tasks_completed")
        if input_mode.lower() == "voice":
            await stats.bump("voice_commands")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.log(f"Agent task complete in {elapsed_ms} ms", "warning" if result.error else "success")
        state.set("idle", "Ready. Waiting for your command.")
    return result


def _record_provider_latency(
    ctx: BrainContext,
    completion: dict,
    elapsed_ms: int,
    provider_payload: list[dict],
    provider_calls: dict[str, bool] | None = None,
) -> None:
    """Attribute latency/success to the provider that actually answered."""
    pm = ctx.provider_manager
    success_name: str | None = None
    if provider_calls:
        success_name = next((name for name, ok in provider_calls.items() if ok), None)
    if success_name is None:
        # No callback info: attribute to the active provider when the call succeeded.
        ok = isinstance(completion, dict) and bool(completion.get("choices"))
        if ok:
            success_name = pm.active_name(provider_payload)
    if success_name is None:
        return
    ok = isinstance(completion, dict) and bool(completion.get("choices"))
    pm.record(success_name, elapsed_ms if ok else None, ok)
    if ok:
        model = provider_payload and None
        for provider in provider_payload:
            if str(provider.get("provider") or "").lower() == success_name:
                model = provider.get("model")
                break
        ctx.state.set(ctx.state.get()["state"], provider=success_name,
                      model=model or pm.model_for(success_name))


def _enabled_map(request: AskRequest) -> dict[str, bool]:
    if not request.tools_enabled:
        return {}
    return {str(k).lower(): bool(v) for k, v in request.tools_enabled.items()}


def _tool_disabled(request: AskRequest, tool_id: str) -> bool:
    enabled = _enabled_map(request)
    return enabled.get(tool_id, True) is False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def plan_request(request: AskRequest, ctx: BrainContext,
                       settings_path: Path | None = None,
                       *, stream: AgentStreamer | None = None) -> AskResponse:
    """Full pipeline for one user request (state + logs + stats + answer).

    Pass ``stream`` to also publish the loop's internal monologue in real time
    and to stream the final answer chunk-by-chunk; ``None`` keeps the original
    single-shot JSON behaviour untouched.
    """
    log, state, stats = ctx.log, ctx.state, ctx.stats
    token = bind_stream(stream) if stream is not None else None
    if stream is not None:
        stream.bind_loop()
    try:
        return await _plan_request_inner(request, ctx, stream=stream)
    except Exception as exc:  # a stream must always end with a terminal frame
        if stream is not None:
            stream.emit(EVENT_ERROR, code="brain_error", message=_clip(str(exc), 300),
                        fatal=True, elapsed_ms=_elapsed(stream))
        raise
    finally:
        if token is not None:
            unbind_stream(token)


def _elapsed(stream: AgentStreamer | None) -> int:
    if stream is None:
        return 0
    return int((time.monotonic() - stream.started_at) * 1000)


async def _plan_request_inner(request: AskRequest, ctx: BrainContext, *,
                              stream: AgentStreamer | None) -> AskResponse:
    """The routing + execution half of :func:`plan_request` (stream-aware)."""
    log, state, stats = ctx.log, ctx.state, ctx.stats
    message = request.message
    started = time.monotonic()

    ctx.provider_manager.mark_keys([p.model_dump() for p in request.providers if p.api_key])
    log.log(f"Received request: \"{_clip(message, 64)}\"", "info")

    active = ctx.provider_manager.active_name([p.model_dump() for p in request.providers])
    state.set("thinking", "Analyzing request...", provider=active,
              model=ctx.provider_manager.model_for(active) if active else None)
    _thinking("Analyzing prompt…", phase="plan")

    decision = route_request(message)  # Planner -> Intent Router (agent-first)
    log.log(f"Intent routed to {decision.route} — {decision.reason}", "info")
    if stream is not None:
        stream.emit(EVENT_THINKING, step=0, phase="route",
                    text=f"Intent → {decision.route} ({decision.reason})")

    try:
        route = decision.route
        if route == "tool_creation":
            result = await _run_tool_creation(request, ctx)
        elif route == "android_command":
            result = await _run_android_command(request, ctx)
        elif route == "web_search":
            result = await _run_web_search(request, ctx)
        elif route == "local_tool":
            result = await _run_local_tool(request, ctx)
        else:
            result = await _run_llm(request, ctx)
            if result.error == "llm_unavailable":
                # Offline fallback: explicit commands still work with zero keys.
                fallback = legacy_route(message)
                if fallback.route not in {"llm", "tool_creation"}:
                    log.log(f"LLM unavailable — offline fallback: {fallback.route}", "warning")
                    _self_correction(0, None, "no AI provider reachable",
                                     f"offline legacy route → {fallback.route}", attempt=0)
                    if fallback.route == "android_command":
                        result = await _run_android_command(request, ctx)
                    elif fallback.route == "web_search":
                        result = await _run_web_search(request, ctx)
                    elif fallback.route == "local_tool":
                        result = await _run_local_tool(request, ctx)
    except LLMError as exc:
        log.log(f"AI engine error: {exc}", "error")
        print(f"\n[🔥 RONIN CRITICAL ERROR]:\n{traceback.format_exc()}\n", flush=True)
        result = AskResponse(response="RONIN could not complete that request.", route=decision.route, error=str(exc))
    except Exception as exc:  # keep the endpoint alive no matter what
        log.log(f"Unexpected error: {exc}", "error")
        print(f"\n[🔥 RONIN CRITICAL ERROR]:\n{traceback.format_exc()}\n", flush=True)
        result = AskResponse(response="RONIN could not complete that request.", route=decision.route, error=str(exc))

    # Pending device action: the Body will call back on /agent/result, which
    # finalizes the turn. Stay in executing state so the orb shows acting.
    if result.needs_tool_result and result.action is not None:
        log.log(f"Waiting for device result: {result.action.tool}", "info")
        if stream is not None:
            # A legacy Body on a streaming connection still needs a terminal frame.
            stream.emit(EVENT_DONE, response=result.response, route=result.route,
                        steps=result.steps, thought=result.thought, error=result.error,
                        needs_tool_result=True, action=result.action.model_dump(mode="json"),
                        elapsed_ms=_elapsed(stream))
        return result

    await save_conversation(request.session_id, message, result.response)
    if result.error is None:
        await stats.bump("tasks_completed")
    if str(request.input_mode or "text").lower() == "voice":
        await stats.bump("voice_commands")

    # Typewriter: the finalized, tag-free answer is streamed to the Body before
    # the terminal `done` frame so the bubble can render it as it is "typed".
    if stream is not None and result.response:
        await stream.stream_answer(result.response, step=result.steps)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.log(f"Request complete in {elapsed_ms} ms", "warning" if result.error else "success")
    state.set("idle", "Ready. Waiting for your command.",
              provider=ctx.provider_manager.active_name([p.model_dump() for p in request.providers]),
              response_ms=elapsed_ms)
    if stream is not None:
        stream.emit(EVENT_DONE, response=result.response, route=result.route,
                    steps=result.steps, thought=result.thought, error=result.error,
                    needs_tool_result=False, streamed=True, elapsed_ms=elapsed_ms,
                    command=result.command.model_dump(mode="json") if result.command else None,
                    update_proposal=result.update_proposal.model_dump(mode="json")
                    if result.update_proposal else None)
    return result


async def plan_request_stream(request: AskRequest, ctx: BrainContext) -> AsyncIterator[str]:
    """SSE frame generator for one autonomous-agent turn.

    The agent runs as an independent task so a disconnected client never kills
    the loop (memory, stats and history still get written); the generator only
    forwards frames while the Body is listening.
    """
    streamer = AgentStreamer()
    streamer.bind_loop()

    async def _drive() -> None:
        try:
            # plan_request() owns the terminal `done` frame (it holds the result),
            # and the `error` frame for contained failures.
            await plan_request(request, ctx, stream=streamer)
        except asyncio.CancelledError:
            streamer.emit(EVENT_ERROR, code="cancelled", message="stream cancelled", fatal=True)
            raise
        except Exception:
            # plan_request re-raises only unexpected blow-ups after emitting `error`.
            pass
        finally:
            streamer.close()

    task = asyncio.create_task(_drive(), name=f"vyrx-stream-{streamer.request_id}")
    _DETACHED_TASKS.add(task)
    task.add_done_callback(_DETACHED_TASKS.discard)
    streamer.emit(
        EVENT_START,
        request_id=streamer.request_id,
        session_id=request.session_id,
        message=_clip(request.message, 200),
        input_mode=request.input_mode,
        state=ctx.state.get(),
    )
    try:
        async for frame in streamer.frames():
            yield frame
    finally:
        # Client gone (or stream exhausted): stop generating frames, let the
        # agent finish its work in the background.
        streamer.detach()


#: In-flight streamed turns that outlived their HTTP connection.
_DETACHED_TASKS: set[asyncio.Task] = set()

