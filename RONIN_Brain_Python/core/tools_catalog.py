"""The VYRX tool catalog (the 12 tools shown on the Tools screen).

Each catalog entry declares how its *brain-side* availability is computed:

- ``server``        -> active while the Brain is running (capability exists)
- ``server_device`` -> active while Brain is running AND the body reported
                       the required device capability is enabled
- ``custom``        -> active when a custom API provider is configured
- ``planned``       -> capability not shipped yet (honest ``Inactive``)
- ``unavailable``   -> integration not configured (honest ``Inactive``)
"""
from __future__ import annotations

from dataclasses import dataclass


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
    ToolSpec("web_search", "Web Search", "internet",
             "Search the web for latest information.", "server"),
    ToolSpec("code_executor", "Code Executor", "development",
             "Run Python, Bash and other code snippets.", "server"),
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
