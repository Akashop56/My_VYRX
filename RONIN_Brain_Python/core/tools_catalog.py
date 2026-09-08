"""The VYRX tool catalog: UI catalog + agentic tool schemas.

Two layers live here:

1. ``TOOLS_CATALOG`` — the user-facing Tools screen entries (brain-side
   availability computation). Unchanged semantics; extended with the new
   autonomous-OS capabilities (screen reading, agent memory, terminal).

2. ``DEVICE_TOOL_SCHEMAS`` — OpenAI-compatible function schemas for tools
   that execute on the Android Body (Kotlin) instead of the Python Brain.
   The planner merges these with the registry's server-side tools on every
   ReAct step so the LLM can natively call *either* side. Device tools are
   returned to the Body as a pending ``AgentAction``; the Body executes the
   action and POSTs the observation back to ``/agent/result`` so the loop
   continues without any user tap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    category: str  # device | internet | automation | development
    description: str
    brain_kind: str  # server | server_device | custom | planned | unavailable
    device_signal: str | None = None  # key into /api/device_status for server_device


TOOLS_CATALOG: list[ToolSpec] = [
    ToolSpec("app_control", "App Control", "device",
             "Open, close and manage apps on your device.", "server_device", "accessibility"),
    ToolSpec("screen_reader", "Screen Reader", "device",
             "Read the current screen's UI tree and click nodes or coordinates.", "server_device", "accessibility"),
    ToolSpec("web_search", "Web Search", "internet",
             "Search the web for latest information.", "server"),
    ToolSpec("code_executor", "Code Executor", "development",
             "Run Python, Bash and other code snippets.", "server"),
    ToolSpec("terminal", "Terminal", "development",
             "Execute Termux shell commands for inspection and self-healing.", "server"),
    ToolSpec("file_manager", "File Manager", "device",
             "Browse, read, write and manage files.", "server"),
    ToolSpec("task_automation", "Task Automation", "automation",
             "Schedule and run automated tasks on your device.", "planned"),
    ToolSpec("notification_manager", "Notification Manager", "device",
             "Send and manage notifications and alerts.", "server_device", "notifications"),
    ToolSpec("telegram", "Telegram Integration", "internet",
             "Send messages, read chats and manage Telegram.", "unavailable"),
    ToolSpec("email", "Email Manager", "internet",
             "Read, send and manage emails (Gmail/Custom).", "unavailable"),
    ToolSpec("system_monitor", "System Monitor", "development",
             "Check battery, CPU, memory and system info.", "server"),
    ToolSpec("custom_api", "Custom API", "development",
             "Use custom AI endpoints and external services.", "custom"),
    ToolSpec("note_creator", "Note Creator", "device",
             "Create notes and save quick knowledge.", "server"),
    ToolSpec("agent_memory", "Agent Memory", "automation",
             "Self-learning persistent memory: the agent saves and recalls preferences.", "server"),
    ToolSpec("image_analyzer", "Image Analyzer", "development",
             "Analyze images with vision models.", "unavailable"),
]

_BY_ID = {spec.id: spec for spec in TOOLS_CATALOG}


def tool_label(tool_id: str) -> str:
    spec = _BY_ID.get(tool_id)
    return spec.name if spec else (tool_id.replace("_", " ").title() or tool_id)


def tool_reason(tool_id: str) -> str:
    spec = _BY_ID.get(tool_id)
    if spec is None:
        return "Unknown tool"
    return {
        "server": "Brain capability online",
        "server_device": "Requires Brain + device permission",
        "custom": "Requires a custom API endpoint",
        "planned": "Automation scheduler not shipped yet",
        "unavailable": "Integration not configured",
    }.get(spec.brain_kind, "Unavailable")


def catalog_ids() -> list[str]:
    return [spec.id for spec in TOOLS_CATALOG]


# ---------------------------------------------------------------------------
# Agentic tool routing
# ---------------------------------------------------------------------------
# Device tools execute on the Kotlin Body. Everything else executes inside the
# Python Brain (synchronously, within the ReAct loop).
# ---------------------------------------------------------------------------

#: Tools that MUST be executed by the Android Body, never by the Brain.
DEVICE_TOOLS: frozenset[str] = frozenset({
    "open_app",
    "read_screen",
    "click_xy",
    "click_node",
    "click",
    "set_text",
    "scroll",
    "press_back",
    "press_home",
    "get_notifications",
    "list_apps",
})

#: Server-side Brain tools (auto-discovered from tools/*.py + registered here
#: for prompt documentation). Kept in sync with the functions exposed by
#: tools/agent_memory.py, tools/termux_exec.py and tools/web_search.py.
BRAIN_TOOLS: frozenset[str] = frozenset({
    "save_memory",
    "retrieve_memory",
    "run_termux_command",
    "read_file",
    "write_file",
    "list_files",
    "search",  # web_search implementation lives in tools/web_search.py
})

#: Legacy registry functions superseded by native device tools. They stay
#: importable (backward compat) but are hidden from the LLM so it always
#: picks the structured device schemas instead.
HIDDEN_LEGACY_TOOLS: frozenset[str] = frozenset({
    "command_for_request",
})


def is_device_tool(name: str) -> bool:
    return str(name or "").strip() in DEVICE_TOOLS


def _schema(name: str, description: str, properties: dict[str, Any],
            required: list[str] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": params,
    }}


#: OpenAI-compatible function schemas for Body-executed tools. The planner
#: injects these into every LLM call alongside the server-side registry
#: tools, so one ReAct loop drives both the Brain and the Body.
DEVICE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    _schema("open_app", "Launch an Android app on the device.",
            {"package": {"type": "string",
                         "description": "Exact Android package name, e.g. com.google.android.youtube."},
             "app_name": {"type": "string",
                          "description": "Human app name (e.g. YouTube) when the package is unknown."}}),
    _schema("read_screen", "Dump the current screen's accessibility UI tree: visible text, "
                           "content descriptions and clickable nodes with bounds. Always call this "
                           "before clicking something you have not seen.",
            {"max_nodes": {"type": "integer",
                           "description": "Maximum nodes to return (default 120)."}}),
    _schema("click_xy", "Tap the screen at absolute pixel coordinates (from read_screen bounds).",
            {"x": {"type": "number", "description": "Absolute X pixel coordinate."},
             "y": {"type": "number", "description": "Absolute Y pixel coordinate."}},
            ["x", "y"]),
    _schema("click_node", "Tap a UI node previously seen via read_screen, by its node id or visible text.",
            {"node_id": {"type": "string", "description": "Node id from read_screen (preferred)."},
             "text": {"type": "string", "description": "Visible text to match when node id is unknown."}}),
    _schema("click", "Tap the first UI element matching the given visible text.",
            {"text": {"type": "string", "description": "Visible text to tap."}}, ["text"]),
    _schema("set_text", "Type text into the currently focused input field.",
            {"text": {"type": "string", "description": "Text to type."}}, ["text"]),
    _schema("scroll", "Scroll the current screen.",
            {"direction": {"type": "string", "enum": ["forward", "backward", "up", "down", "left", "right"],
                           "description": "Scroll direction (default forward)."}}),
    _schema("press_back", "Press the system Back button.", {}),
    _schema("press_home", "Press the system Home button.", {}),
    _schema("get_notifications", "Read the most recent device notifications (package, title, text).",
            {"limit": {"type": "integer", "description": "Maximum notifications (default 10)."}}),
    _schema("list_apps", "List installed launchable apps (label + package).",
            {"query": {"type": "string",
                       "description": "Optional case-insensitive filter on label or package."},
             "limit": {"type": "integer", "description": "Maximum apps (default 50)."}}),
]


def device_tool_schemas() -> list[dict[str, Any]]:
    """Return a fresh copy so the planner can filter without mutation."""
    import copy
    return copy.deepcopy(DEVICE_TOOL_SCHEMAS)
