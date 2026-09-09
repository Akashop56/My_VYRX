"""LLM transport + 2099 autonomous-agent system prompt.

This module is the Brain's voice box with a strict contract:

* ``complete()`` speaks to OpenAI / OpenRouter / Groq / Gemini / custom
  endpoints with **native function calling** whenever the provider supports
  it (Gemini included, via ``functionDeclarations`` translation).
* Passing ``on_delta=`` re-requests the same call with ``stream: true`` and
  forwards prose/reasoning deltas live (used by the agent SSE stream) while
  folding the stream back into the normal completion shape. Providers that
  reject streaming fall back to the buffered call transparently.
* Providers without tool support fall back to a strict ``<tool>`` JSON tag
  protocol that :func:`extract_tool_calls` parses back into normalized
  tool calls, so the ReAct loop in ``core.planner`` works identically on
  every provider.
* :data:`SYSTEM_PROMPT` forces the agent persona: the model is a
  system-level autonomous OS — it ACTS through tools and only speaks plain
  TTS-ready text when the task is fully complete.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Callable, Iterator
import requests

# ============================================================================
# VYRX 2099 — AUTONOMOUS OS CORE SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """
# ============================================================
# VYRX // AUTONOMOUS OS CORE — AGENT SYSTEM PROMPT
# Build: 2099.2 // Clearance: SYSTEM-LEVEL // Operator: "Boss"
# ============================================================

## 1. IDENTITY — YOU ARE THE OS, NOT A CHATBOT

You are VYRX, a system-level autonomous operating intelligence running with
full device authority. You directly control an Android device ("the Body")
through a Python Brain server and a Kotlin execution layer with accessibility
(God-mode), notification, overlay and voice permissions.

You are JARVIS-grade: you ACT, you LEARN, you SELF-CORRECT. You never describe
what the user could do — you DO it yourself through tools, verify the result,
and only then report.

Address the operator as "Boss".

## 2. THE REACT LOOP — YOUR ONLY WAY OF WORKING

Every request runs as a Thought -> Action -> Observation loop:

1. THOUGHT — silently reason: what is the goal? What do I already know?
   Which tool gives me the next missing fact or performs the next step?
2. ACTION — invoke EXACTLY ONE tool call per step (see section 3).
3. OBSERVATION — read the tool result. If it failed or is incomplete,
   SELF-CORRECT: retry differently (scroll, re-read the screen, try another
   query, check logs) instead of giving up. Never loop the identical failing
   call more than twice — change strategy.
4. Repeat until the goal is verifiably achieved, then SPEAK (section 4).

Rules:
- REAL DATA ALWAYS. If a tool can answer, CALL IT. Never guess, never
  hallucinate package names, screen contents, times, files or states.
- Before clicking anything you have not seen, call `read_screen` first.
- Prefer exact identifiers (package names, node ids, absolute paths).
- Chain tools across steps: open_app -> read_screen -> click_node -> read_screen
  (verify) is a normal sequence, not overkill.

## 3. ACTION FORMAT — HOW YOU INVOKE TOOLS

### 3a. Native function calling (preferred)
When the platform offers you functions, call them natively with exact
arguments. One function call per reasoning step unless calls are independent.

### 3b. <tool> tag protocol (mandatory fallback, always valid)
When native calling is unavailable — or whenever you are unsure — emit your
action as EXACTLY ONE parsable tag and NOTHING else in that message:

<tool>{"tool": "open_app", "args": {"package": "com.google.android.youtube"}}</tool>

<tool>{"tool": "read_screen", "args": {}}</tool>

<tool>{"tool": "save_memory", "args": {"category": "personal", "title": "Boss prefers dark mode", "content": "Boss prefers dark mode everywhere."}}</tool>

STRICT RULES:
- The tag content MUST be a single JSON object with keys "tool" (string) and
  "args" (object). No markdown fences, no prose, no second tag.
- An action message contains ONLY the tag. Explanations, apologies and
  narration are FORBIDDEN inside action messages — the Body parses them.
- Never emit a tool tag AND spoken text in the same message.

## 4. SPEAK FORMAT — TALK ONLY WHEN DONE

Output plain human text ONLY when the task is fully complete (or impossible)
and you must speak to Boss via TTS. That message MUST contain no JSON, no
<tool> tags, no function syntax — just concise spoken language, e.g.:

"Done, Boss. YouTube is open and playing."

Keep it short: what was achieved + the one key detail. No tutorials unless
explicitly asked.

## 5. TOOL ARSENAL (exact names — use these, nothing else)

DEVICE (executed by the Kotlin Body on the phone):
- open_app {package?, app_name?} — launch an app.
- read_screen {max_nodes?} — dump visible UI tree (text, descriptions, bounds, node ids).
- click_xy {x, y} — tap absolute screen coordinates from read_screen bounds.
- click_node {node_id?, text?} — tap a node id (preferred) or visible text.
- click {text} — tap first element matching visible text.
- set_text {text} — type into the focused input field.
- scroll {direction?} — forward|backward|up|down|left|right.
- press_back {} / press_home {} — system navigation.
- get_notifications {limit?} — recent notifications (package, title, text).
- list_apps {query?, limit?} — installed launchable apps.

BRAIN (executed inside the Python server):
- save_memory {category, title, content, importance?} — persist a learning.
  category: personal | experience | knowledge.
- retrieve_memory {query, limit?} — recall past learnings (relevant memories
  are ALSO auto-injected into your context on every request).
- search {query, limit?} — live web search.
- run_termux_command {command, timeout?} — execute a Termux shell command for
  inspection, diagnostics and SELF-HEALING (read logs, check files, patch
  configs, restart components).
- read_file {path} / write_file {path, content} — read or rewrite Brain
  workspace files (configs, tools). NEVER touch files outside the workspace.
- list_files {path?} — list the Brain workspace.

## 6. MEMORY — YOU LEARN FOREVER

- Relevant past learnings are injected into your context as [MEMORY]; obey
  them as standing orders from Boss.
- If Boss corrects you, teaches a preference, or states a lasting fact, you
  MUST call `save_memory` in the SAME task before speaking the final answer.
  Example: Boss: "I prefer 24-hour time" -> save_memory(personal, ...) -> confirm.
- If memory is missing or uncertain, call `retrieve_memory` before asking Boss.

## 7. SELF-HEALING — FIX YOURSELF

If a Brain tool errors, a file is missing, or a config looks wrong:
1. Inspect with run_termux_command / read_file / list_files.
2. Repair the smallest possible thing (fix the config value, rewrite the
   broken file, reinstall the missing piece).
3. Verify by re-running the failed step.
4. Report to Boss what broke and what you fixed — briefly.

Destructive shell commands (wiping storage, factory reset, killing system
processes) are FORBIDDEN without explicit prior authorization from Boss.

## 8. SAFETY & CONTROL

- You are autonomous for reversible actions: opening apps, reading screens,
  searching, saving memory, navigating, inspecting the Brain.
- PAUSE and ask Boss before irreversible acts: deleting data, sending
  messages/money, granting permissions, modifying system settings, or any
  action you cannot undo.
- Never expose API keys, credentials or notification-private content beyond
  what the task needs. Never exfiltrate data anywhere.
- If a task is truly impossible, say so in ONE spoken message with the
  closest practical alternative. Never fake a tool execution.

## 9. FINAL DIRECTIVE

OBSERVE. DECIDE. EXECUTE. VERIFY. REPORT.
Build strong. Understand deeply. Improve constantly. Protect Boss.
You are VYRX. Act like the OS you are.
"""

# Backwards-compatible alias: the planner builds on SYSTEM_PROMPT.
AGENT_SYSTEM_PROMPT = SYSTEM_PROMPT


class LLMError(RuntimeError):
    pass


class ProviderFailure(LLMError):
    pass


class StreamingUnavailable(ProviderFailure):
    """Raised when a provider refuses ``stream: true`` — caller falls back."""


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

def _messages(
    message: str,
    history: list[dict[str, str]],
    system_prompt: str = SYSTEM_PROMPT,
) -> list[dict[str, str]]:
    result = [{"role": "system", "content": system_prompt}]
    for item in history:
        result.extend(({"role": "user", "content": item["user_message"]}, {"role": "assistant", "content": item["assistant_response"]}))
    return result + [{"role": "user", "content": message}]


def _environment_providers() -> list[dict[str, str | None]]:
    names = [x.strip().lower() for x in os.getenv("RONIN_LLM_PROVIDERS", os.getenv("RONIN_LLM_PROVIDER", "")).split(",") if x.strip()]
    keys = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
    return [{"provider": name, "api_key": os.getenv(keys.get(name, ""), ""), "model": os.getenv(f"{name.upper()}_MODEL")} for name in names if os.getenv(keys.get(name, ""), "")]


def _post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=45)
    except requests.Timeout as exc:
        raise ProviderFailure("provider timeout") from exc
    except requests.RequestException as exc:
        raise ProviderFailure("provider unavailable") from exc
    if response.status_code in (401, 403):
        raise ProviderFailure("invalid API key")
    if response.status_code == 429:
        raise ProviderFailure("provider rate limit")
    if response.status_code >= 500:
        raise ProviderFailure("provider unavailable")
    try:
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ProviderFailure("provider returned an invalid response") from exc


# ---------------------------------------------------------------------------
# Streaming transport — real-time reasoning deltas for the agent stream
#
# ``stream: true`` is used only to give the UI a live look at the model while
# it reasons. The ReAct contract is untouched: deltas are accumulated back into
# the exact OpenAI completion shape the planner already consumes, so streamed
# and non-streamed turns drive the loop identically. Any provider that rejects
# streaming raises StreamingUnavailable and the caller retries non-streamed.
# ---------------------------------------------------------------------------

def iter_sse_payloads(response: requests.Response) -> Iterator[str]:
    """Yield the ``data:`` payloads of an SSE response (``[DONE]`` ends it)."""
    for raw in response.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.strip()
        if line.startswith(":"):  # comment / keep-alive
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        if payload == "[DONE]":
            return
        yield payload


def new_stream_state() -> dict[str, Any]:
    """Accumulator shared by every provider's delta folding."""
    return {"content": [], "reasoning": [], "calls": {}, "finish": "stop"}


def fold_openai_delta(state: dict[str, Any], chunk: dict[str, Any]) -> tuple[str, str]:
    """Fold one chat-completion *delta* chunk into ``state``.

    Returns ``(visible_text_delta, tool_trace_delta)`` so callers can stream the
    model's prose while silently assembling a possible tool call. Handles the
    OpenAI/Groq/OpenRouter fragment styles (split ids, stringly-typed args) and
    reasoning models (``reasoning_content``).
    """
    choices = chunk.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return "", ""
    choice = choices[0]
    if choice.get("finish_reason"):
        state["finish"] = str(choice["finish_reason"])
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        delta = {}
    text = delta.get("content")
    text = text if isinstance(text, str) else ""
    if text:
        state["content"].append(text)
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    reasoning = reasoning if isinstance(reasoning, str) else ""
    if reasoning:
        state["reasoning"].append(reasoning)
    calls: dict[int, dict[str, Any]] = state["calls"]
    fragments = delta.get("tool_calls")
    if isinstance(fragments, list):
        for entry in fragments:
            if not isinstance(entry, dict):
                continue
            try:
                index = int(entry.get("index", len(calls)))
            except (TypeError, ValueError):
                index = len(calls)
            slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if entry.get("id"):
                slot["id"] = str(entry["id"])
            function = entry.get("function") or {}
            if isinstance(function, dict):
                if isinstance(function.get("name"), str):
                    slot["name"] += function["name"]
                if isinstance(function.get("arguments"), str):
                    slot["arguments"] += function["arguments"]
    return text, reasoning


def build_completion(state: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an accumulated stream into an OpenAI-shaped completion."""
    text = "".join(state.get("content") or [])
    reasoning = "".join(state.get("reasoning") or [])
    calls = state.get("calls") or {}
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if reasoning:
        # Keep the raw chain of thought for the terminal block.
        message["reasoning_content"] = reasoning
    if calls:
        wire_calls = []
        for index in sorted(calls):
            slot = calls[index]
            if not str(slot.get("name", "")).strip():
                continue
            wire_calls.append({
                "id": slot.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": str(slot["name"]).strip(),
                             "arguments": slot.get("arguments", "") or "{}"},
            })
        if wire_calls:
            message["tool_calls"] = wire_calls
            return {"choices": [{"message": message, "finish_reason": "tool_calls"}],
                    "streamed": True}
    if not text:
        message["content"] = "No response generated."
    return {"choices": [{"message": message, "finish_reason": state.get("finish") or "stop"}],
            "streamed": True}


def _stream_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """OpenAI-compatible ``stream: true`` call folded back into a completion."""
    body = dict(payload)
    body["stream"] = True
    try:
        response = requests.post(url, headers=headers, json=body, timeout=45, stream=True)
    except requests.RequestException as exc:
        raise StreamingUnavailable("provider streaming unavailable") from exc
    try:
        if response.status_code >= 400:
            # A provider that does not know `stream` usually 400s here.
            raise StreamingUnavailable(f"provider rejected streaming (HTTP {response.status_code})")
        state = new_stream_state()
        for raw in iter_sse_payloads(response):
            try:
                chunk = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(chunk, dict):
                continue
            text, reasoning = fold_openai_delta(state, chunk)
            piece = text or reasoning
            if piece and on_delta is not None:
                on_delta(piece)
        completion = build_completion(state)
        if completion is None:
            raise StreamingUnavailable("provider stream returned no usable delta")
        return completion
    except (UnicodeDecodeError, ValueError) as exc:
        raise StreamingUnavailable("provider stream was not valid SSE") from exc
    finally:
        response.close()


def _stream_gemini(
    url: str,
    payload: dict[str, Any],
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Gemini ``streamGenerateContent?alt=sse`` folded into the OpenAI shape."""
    endpoint = url.replace(":generateContent", ":streamGenerateContent?alt=sse")
    try:
        response = requests.post(endpoint, headers={"Content-Type": "application/json"},
                                 json=payload, timeout=45, stream=True)
    except requests.RequestException as exc:
        raise StreamingUnavailable("gemini streaming unavailable") from exc
    try:
        if response.status_code >= 400:
            raise StreamingUnavailable(f"gemini rejected streaming (HTTP {response.status_code})")
        state = new_stream_state()
        for raw in iter_sse_payloads(response):
            try:
                chunk = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(chunk, dict):
                continue
            try:
                candidate = (chunk.get("candidates") or [])[0]
            except (IndexError, TypeError, AttributeError):
                continue
            if not isinstance(candidate, dict):
                continue
            if candidate.get("finishReason"):
                state["finish"] = "tool_calls" if state["calls"] else "stop"
            parts = ((candidate.get("content") or {}).get("parts") or [])
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    state["content"].append(text)
                    if on_delta is not None:
                        on_delta(text)
                call = part.get("functionCall")
                if isinstance(call, dict) and call.get("name"):
                    normalized = _normalize_tool_call(str(call["name"]), call.get("args", {}))
                    if normalized is not None:
                        state["calls"][len(state["calls"])] = {
                            "id": normalized["id"],
                            "name": normalized["function"]["name"],
                            "arguments": json.dumps(normalized["function"]["arguments"], ensure_ascii=False),
                        }
        text = "".join(state["content"])
        # A streamed <tool> tag only becomes parsable once the whole line is in.
        calls = dict(state["calls"])
        if not calls and text:
            for offset, parsed in enumerate(parse_tool_tag_calls(text)):
                calls[len(calls) + offset] = {
                    "id": parsed["id"], "name": parsed["function"]["name"],
                    "arguments": json.dumps(parsed["function"]["arguments"], ensure_ascii=False),
                }
        message: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            message["tool_calls"] = [
                {"id": calls[index]["id"] or f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                 "function": {"name": calls[index]["name"], "arguments": calls[index]["arguments"] or "{}"}}
                for index in sorted(calls)
            ]
            return {"choices": [{"message": message, "finish_reason": "tool_calls"}], "streamed": True}
        if not text:
            message["content"] = "No response generated."
        return {"choices": [{"message": message, "finish_reason": state["finish"] or "stop"}],
                "streamed": True}
    finally:
        response.close()


# ---------------------------------------------------------------------------
# <tool> tag protocol — parsing & normalization
# ---------------------------------------------------------------------------

_TOOL_TAG_RE = re.compile(r"<tool\s*>(.*?)</tool\s*>", re.IGNORECASE | re.DOTALL)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _coerce_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _normalize_tool_call(name: str, arguments: Any, call_id: str | None = None) -> dict[str, Any] | None:
    name = str(name or "").strip()
    if not name:
        return None
    return {
        "id": call_id or f"call_{uuid.uuid4().hex[:12]}",
        "type": "function",
        "function": {"name": name, "arguments": _coerce_args(arguments)},
    }


def parse_tool_tag_calls(text: str) -> list[dict[str, Any]]:
    """Extract ``<tool>{"tool": ..., "args": {...}}</tool>`` calls from text.

    Also accepts fenced `````json`` blocks and a bare JSON object of the same
    shape, so strict-JSON-mode providers work without native tool support.
    Returns normalized OpenAI-style ``tool_calls`` entries (arguments as dict).
    """
    if not text or "<tool" not in text.lower() and "{" not in text:
        return []
    candidates: list[str] = []
    if text:
        candidates.extend(match.group(1) for match in _TOOL_TAG_RE.finditer(text))
        if not candidates:
            candidates.extend(match.group(1) for match in _FENCED_JSON_RE.finditer(text))
        if not candidates:
            stripped = text.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                candidates.append(stripped)
    calls: list[dict[str, Any]] = []
    for raw in candidates:
        try:
            payload = json.loads(raw.strip())
        except (ValueError, TypeError):
            continue
        # Accept {"tool":..., "args":{...}} plus OpenAI {"name":..., "arguments":...}.
        if isinstance(payload, dict):
            name = payload.get("tool", payload.get("name"))
            args = payload.get("args", payload.get("arguments", {}))
            normalized = _normalize_tool_call(str(name or ""), args, payload.get("id"))
            if normalized is not None:
                calls.append(normalized)
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    normalized = _normalize_tool_call(
                        str(item.get("tool", item.get("name", ""))),
                        item.get("args", item.get("arguments", {})), item.get("id"))
                    if normalized is not None:
                        calls.append(normalized)
    return calls


def extract_tool_calls(message: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return normalized tool calls from an assistant message.

    Handles native ``tool_calls`` (arguments may be a JSON string) and the
    ``<tool>`` tag / strict-JSON fallback embedded in ``content``.
    """
    if not isinstance(message, dict):
        return []
    calls: list[dict[str, Any]] = []
    native = message.get("tool_calls")
    if isinstance(native, list):
        for entry in native:
            if not isinstance(entry, dict):
                continue
            function = entry.get("function", {})
            if not isinstance(function, dict):
                continue
            normalized = _normalize_tool_call(
                str(function.get("name", "")), function.get("arguments", {}),
                entry.get("id"))
            if normalized is not None:
                calls.append(normalized)
    if not calls:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            calls.extend(parse_tool_tag_calls(content))
    return calls


def strip_tool_tags(text: str | None) -> str:
    """Remove ``<tool>`` blocks so only TTS-ready speech remains."""
    if not text:
        return ""
    cleaned = _TOOL_TAG_RE.sub("", text)
    return " ".join(cleaned.split()).strip()


def has_action_tag(text: str | None) -> bool:
    return bool(text) and bool(_TOOL_TAG_RE.search(text or ""))


def build_tool_instructions(tools: list[dict[str, Any]] | None) -> str:
    """Compact tool list appended to the prompt for non-native providers."""
    if not tools:
        return ""
    lines = ["", "[AVAILABLE TOOLS — call exactly one per step via <tool> JSON]",
             'Format: <tool>{"tool": "<name>", "args": {…}}</tool>']
    for tool in tools:
        function = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = function.get("name", "?")
        description = (function.get("description", "") or "").strip().split("\n")[0][:140]
        params = function.get("parameters", {}) if isinstance(function, dict) else {}
        props = params.get("properties", {}) if isinstance(params, dict) else {}
        required = params.get("required", []) if isinstance(params, dict) else []
        arg_bits = []
        if isinstance(props, dict):
            for key, schema in list(props.items())[:8]:
                marker = "*" if key in (required or []) else ""
                arg_bits.append(f"{key}{marker}")
        lines.append(f"- {name}({', '.join(arg_bits)}): {description}")
    lines.append("Emit ONLY the tag when acting. Speak plain text only when done.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provider transport (with native function calling incl. Gemini)
# ---------------------------------------------------------------------------

def _openai_tools_to_gemini(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Translate OpenAI function schemas to Gemini functionDeclarations."""
    declarations: list[dict[str, Any]] = []
    for tool in tools or []:
        function = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = str(function.get("name", "") or "").strip()
        if not name:
            continue
        params = function.get("parameters")
        declaration: dict[str, Any] = {
            "name": name,
            "description": str(function.get("description", "") or "")[:500],
        }
        if isinstance(params, dict) and params.get("type") == "object":
            cleaned_props: dict[str, Any] = {}
            props = params.get("properties", {})
            if isinstance(props, dict):
                for key, schema in props.items():
                    if not isinstance(schema, dict):
                        continue
                    cleaned: dict[str, Any] = {}
                    for field in ("type", "description", "enum", "items"):
                        if field in schema:
                            value = schema[field]
                            if field == "type" and isinstance(value, str):
                                cleaned[field] = value.upper()
                            else:
                                cleaned[field] = value
                    cleaned_props[str(key)] = cleaned
            declaration["parameters"] = {
                "type": "OBJECT",
                "properties": cleaned_props,
                "required": [str(r) for r in params.get("required", []) or []],
            }
        declarations.append(declaration)
    return declarations


def _gemini_contents(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Split system prompt out; map roles + tool results for Gemini."""
    system_text: str | None = None
    contents: list[dict[str, Any]] = []
    for entry in messages:
        role = str(entry.get("role", "user"))
        if role == "system":
            text = entry.get("content", "")
            system_text = (system_text + "\n" + str(text)) if system_text else str(text)
            continue
        if role == "assistant" and entry.get("tool_calls"):
            # Replay native calls as plain text so Gemini keeps context.
            try:
                text = "[ACTION] " + json.dumps(entry["tool_calls"], ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = "[ACTION] (tool call)"
            contents.append({"role": "model", "parts": [{"text": text}]})
            continue
        if role == "tool":
            name = entry.get("name", "tool")
            text = f"[TOOL RESULT for {name}]: {entry.get('content', '')}"
            contents.append({"role": "user", "parts": [{"text": text}]})
            continue
        gemini_role = "model" if role == "assistant" else "user"
        content = entry.get("content", "")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
        if not text.strip():
            continue
        # Gemini requires strict user/model alternation: merge consecutive same-role parts.
        if contents and contents[-1]["role"] == gemini_role:
            contents[-1]["parts"].append({"text": text})
        else:
            contents.append({"role": gemini_role, "parts": [{"text": text}]})
    if not contents:
        contents.append({"role": "user", "parts": [{"text": "Continue."}]})
    return system_text, contents


def _gemini_response_to_openai(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Gemini response to OpenAI chat-completion shape."""
    try:
        candidate = (data.get("candidates") or [])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
    except (IndexError, AttributeError, TypeError) as exc:
        raise ProviderFailure("provider returned no candidates") from exc
    texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str) and part["text"].strip():
            texts.append(part["text"])
        call = part.get("functionCall")
        if isinstance(call, dict) and call.get("name"):
            normalized = _normalize_tool_call(str(call["name"]), call.get("args", {}))
            if normalized is not None:
                tool_calls.append(normalized)
    # Text fallback: a Gemini model may emit the <tool> tag as plain text.
    if not tool_calls and texts:
        tool_calls.extend(parse_tool_tag_calls("\n".join(texts)))
    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) if texts else None}
    finish = "stop"
    if tool_calls:
        # Serialize arguments OpenAI-style (planner re-parses either form).
        message["tool_calls"] = [
            {"id": call["id"], "type": "function",
             "function": {"name": call["function"]["name"],
                          "arguments": json.dumps(call["function"]["arguments"], ensure_ascii=False)}}
            for call in tool_calls
        ]
        finish = "tool_calls"
    elif not texts:
        message["content"] = "No response generated."
    return {"choices": [{"message": message, "finish_reason": finish}], "provider_response": data}


def _complete_with(
    provider: dict[str, str | None],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    name, key, model = (provider.get("provider") or "").lower(), provider.get("api_key") or "", provider.get("model")
    if not key:
        raise ProviderFailure("API key is not configured")
    if name in ("openai", "openrouter", "groq", "custom"):
        endpoints = {"openai": "https://api.openai.com/v1/chat/completions", "openrouter": "https://openrouter.ai/api/v1/chat/completions", "groq": "https://api.groq.com/openai/v1/chat/completions"}
        endpoint = provider.get("endpoint") if name == "custom" else endpoints.get(name)
        defaults = {"openai": "gpt-4o-mini", "openrouter": "openai/gpt-4o-mini", "groq": "llama-3.3-70b-versatile", "custom": ""}
        if not endpoint:
            raise ProviderFailure("custom endpoint is not configured")
        if not model and name == "custom":
            raise ProviderFailure("custom model is not configured")
        payload: dict[str, Any] = {"model": model or defaults[name], "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if on_delta is not None:
            # Live reasoning tokens; a provider that rejects streaming falls back
            # to the buffered call below, so the agent loop never depends on it.
            try:
                return _stream_post(endpoint, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                    payload, on_delta)
            except StreamingUnavailable:
                pass
        data = _post(endpoint, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, payload)
        if not data.get("choices"):
            raise ProviderFailure("provider returned no choices")
        return data
    if name == "gemini":
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model or 'gemini-1.5-flash'}:generateContent?key={key}"
        system_text, contents = _gemini_contents(messages)
        payload_g: dict[str, Any] = {"contents": contents}
        if system_text:
            payload_g["system_instruction"] = {"parts": [{"text": system_text}]}
        declarations = _openai_tools_to_gemini(tools)
        if declarations:
            payload_g["tools"] = [{"function_declarations": declarations}]
            payload_g["tool_config"] = {"function_calling_config": {"mode": "AUTO"}}
        if on_delta is not None:
            try:
                return _stream_gemini(endpoint, payload_g, on_delta)
            except StreamingUnavailable:
                pass
        data = _post(endpoint, {"Content-Type": "application/json"}, payload_g)
        return _gemini_response_to_openai(data)
    raise ProviderFailure(f"unsupported provider: {name}")


def complete(
    message: str,
    history: list[dict[str, str]],
    providers: list[dict[str, str | None]] | None = None,
    system_prompt: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    messages: list[dict[str, Any]] | None = None,
    on_provider: Callable[[str, bool], None] | None = None,
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    configured = providers if providers else _environment_providers()
    if not configured:
        raise LLMError("No AI provider is configured")
    failures: list[str] = []
    for provider in configured:
        name = (provider.get("provider") or "unknown").lower()
        try:
            request_messages = messages if messages is not None else _messages(message, history, system_prompt or SYSTEM_PROMPT)
            # Native tools: OpenAI-family + custom. Gemini gets translated
            # declarations. Anything else falls back to <tool> tag prompting.
            if name in {"openai", "groq", "openrouter", "custom", "gemini"}:
                provider_tools = tools
            else:
                provider_tools = None
            result = _complete_with(provider, request_messages, provider_tools, on_delta)
            if on_provider is not None:
                on_provider(name, True)
            return result
        except ProviderFailure as exc:
            if on_provider is not None:
                on_provider(name, False)
            failures.append(f"{provider.get('provider', 'unknown')}: {exc}")
    raise LLMError("All configured providers failed: " + "; ".join(failures))
