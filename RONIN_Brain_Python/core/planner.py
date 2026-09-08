"""VYRX Planner: Planner -> Intent Router -> Tool Execution.

Every user request flows through:

    1. Planner   : receive request, pick a state (thinking), prepare context
    2. Router    : classify intent (core.router.route_request)
    3. Executor  : run the matched route (device command / web search /
                   memory / tool creation / LLM with tool loop)

Each step emits an action-log entry and a state change, which the body
consumes through ``GET /api/state``, ``GET /api/action_logs`` and the SSE
stream ``GET /api/events``.

All route behaviour of the original ``/ask_ronin`` endpoint is preserved;
this module only adds observability, stats and provider metrics on top.
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.action_log import ActionLog
from core.llm_handler import SYSTEM_PROMPT, LLMError, complete
from core.provider_manager import ProviderManager
from core.router import route_request
from core.schemas import AskRequest, AskResponse, UpdateProposal
from core.state_manager import StateManager
from core.stats import StatsTracker
from core.tool_registry import execute_tool, get_available_tools
from core.tools_catalog import tool_label
from memory.db_manager import recent_history, save_conversation, store_fact
from memory.memory_engine import MemoryEngine
from tools.system_control import command_for_request
from tools.web_search import search


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
    blocked = {name for name, enabled in tools_enabled.items() if not enabled}
    if "web_search" in blocked:
        tools = [t for t in tools if t.get("function", {}).get("name") != "search"]
    if "app_control" in blocked:
        tools = [t for t in tools if t.get("function", {}).get("name") != "command_for_request"]
    return tools


def _tool_catalog_id(function_name: str) -> str:
    return {
        "search": "web_search",
        "command_for_request": "app_control",
    }.get(function_name, function_name)


# ---------------------------------------------------------------------------
# Route executors
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
    started = time.monotonic()
    try:
        data = await asyncio.to_thread(search, query)
    except Exception as exc:
        await ctx.stats.record_tool_usage("web_search", query, False)
        ctx.log.log(f"Web search failed: {exc}", "error")
        return AskResponse(response=f"Web search failed: {exc}", route="web_search", error=str(exc))
    latency_ms = int((time.monotonic() - started) * 1000)
    results = data.get("results") or []
    ctx.log.log(f"Parsing {len(results)} results...", "info")
    await ctx.stats.bump("web_searches")
    await ctx.stats.record_tool_usage("web_search", query, len(results) > 0)
    response = "\n".join(f"{x['title']}: {x['snippet']}" for x in results) or "No web results were returned."
    return AskResponse(response=response, route="web_search")


async def _run_local_tool(request: AskRequest, ctx: BrainContext) -> AskResponse:
    fact = request.message.split(" ", 1)[-1]
    ctx.state.set("learning", "Saving memory...")
    ctx.log.log("Saving memory...", "info")
    await store_fact(fact[:120].lower(), fact)
    title = _clip(fact, 48)
    await ctx.memory_engine.add(category="knowledge", title=title, content=fact,
                                importance=3, source="chat")
    await ctx.stats.bump("learned")
    await ctx.stats.record_tool_usage("note_creator", title, True)
    ctx.log.log(f"Memory saved: \"{title}\"", "success")
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
    provider_payload = [provider.model_dump() for provider in request.providers]
    started = time.monotonic()
    completion = await asyncio.to_thread(
        complete, request.message, [], provider_payload,
        system_prompt=tool_creation_system_prompt,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
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


async def _run_llm(request: AskRequest, ctx: BrainContext) -> AskResponse:
    log, stats, pm, state = ctx.log, ctx.stats, ctx.provider_manager, ctx.state
    history = await recent_history(request.session_id)
    system_prompt = build_system_prompt(request.personality, request.response_mode)
    provider_payload = [provider.model_dump() for provider in request.providers]
    settings = _runtime_settings(Path(__file__).resolve().parent.parent / "config" / "settings.json")
    available_tools = get_available_tools() if _tool_execution_enabled(settings) else []
    available_tools = _filter_tools(available_tools, _enabled_map(request))
    if available_tools:
        log.log(f"{len(available_tools)} tools available for this request", "info")

    state.set("thinking", "Calling API...")
    active = pm.active_name(provider_payload)
    if active:
        log.log(f"Calling {active} model...", "info")
    provider_calls: dict[str, bool] = {}

    def _on_provider(name: str, ok: bool) -> None:
        provider_calls[name] = ok

    started = time.monotonic()
    completion = await asyncio.to_thread(
        complete, request.message, history, provider_payload,
        system_prompt=system_prompt, tools=available_tools, on_provider=_on_provider,
    )
    assistant_message = _assistant_message(completion)

    if available_tools and assistant_message.get("tool_calls"):
        messages = _conversation_messages(request.message, history, system_prompt)
        while assistant_message.get("tool_calls"):
            messages.append(assistant_message)
            for tool_call in assistant_message["tool_calls"]:
                function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
                tool_name = str(function.get("name", ""))
                arguments = function.get("arguments", "{}")
                state.set("executing", f"Running {tool_label(_tool_catalog_id(tool_name))}...")
                log.log(f"Executing tool: {tool_name}", "tool")
                tool_started = time.monotonic()
                result_text = await asyncio.to_thread(execute_tool, tool_name, arguments)
                tool_ms = int((time.monotonic() - tool_started) * 1000)
                success = '"error"' not in result_text[:120]
                catalog_id = _tool_catalog_id(tool_name)
                await stats.record_tool_usage(catalog_id, _clip(str(arguments)[:80]), success)
                if catalog_id == "web_search":
                    await stats.bump("web_searches")
                if catalog_id == "app_control":
                    await stats.bump("apps_opened")
                log.log(f"Tool {tool_name} finished in {tool_ms} ms", "success" if success else "warning")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", "") if isinstance(tool_call, dict) else "",
                    "name": tool_name,
                    "content": result_text,
                })

            # GROQ RATE LIMIT BYPASS: 2 second ka pause
            await asyncio.sleep(2)
            state.set("thinking", "Reasoning over tool results...")
            completion = await asyncio.to_thread(
                complete, "", [], provider_payload, tools=available_tools, messages=messages,
            )
            assistant_message = _assistant_message(completion)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    _record_provider_latency(ctx, completion, elapsed_ms, provider_payload, provider_calls)
    log.log("Response ready", "success")
    return AskResponse(response=_response_text(assistant_message), route="llm")


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

async def plan_request(request: AskRequest, ctx: BrainContext, settings_path: Path | None = None) -> AskResponse:
    """Full pipeline for one user request (state + logs + stats + answer)."""
    log, state, stats = ctx.log, ctx.state, ctx.stats
    message = request.message
    started = time.monotonic()

    ctx.provider_manager.mark_keys([p.model_dump() for p in request.providers if p.api_key])
    log.log(f"Received request: \"{_clip(message, 64)}\"", "info")

    active = ctx.provider_manager.active_name([p.model_dump() for p in request.providers])
    state.set("thinking", "Analyzing request...", provider=active,
              model=ctx.provider_manager.model_for(active) if active else None)

    decision = route_request(message)  # Planner -> Intent Router
    log.log(f"Intent routed to {decision.route} — {decision.reason}", "info")

    try:
        route = decision.route
        if route == "android_command":
            result = await _run_android_command(request, ctx)
        elif route == "web_search":
            result = await _run_web_search(request, ctx)
        elif route == "local_tool":
            result = await _run_local_tool(request, ctx)
        elif route == "tool_creation":
            result = await _run_tool_creation(request, ctx)
        else:
            result = await _run_llm(request, ctx)
    except LLMError as exc:
        log.log(f"AI engine error: {exc}", "error")
        print(f"\n[🔥 RONIN CRITICAL ERROR]:\n{traceback.format_exc()}\n", flush=True)
        result = AskResponse(response="RONIN could not complete that request.", route=decision.route, error=str(exc))
    except Exception as exc:  # keep the endpoint alive no matter what
        log.log(f"Unexpected error: {exc}", "error")
        print(f"\n[🔥 RONIN CRITICAL ERROR]:\n{traceback.format_exc()}\n", flush=True)
        result = AskResponse(response="RONIN could not complete that request.", route=decision.route, error=str(exc))

    await save_conversation(request.session_id, message, result.response)
    await stats.bump("tasks_completed")
    if str(request.input_mode or "text").lower() == "voice":
        await stats.bump("voice_commands")

    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.log(f"Request complete in {elapsed_ms} ms", "success")
    state.set("idle", "Ready. Waiting for your command.",
              provider=ctx.provider_manager.active_name([p.model_dump() for p in request.providers]),
              response_ms=elapsed_ms)
    return result
