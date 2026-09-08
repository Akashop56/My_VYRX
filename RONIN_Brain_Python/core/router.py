"""Intent router: agent-first with offline legacy fallbacks.

The autonomous ReAct loop (``core.planner``) is the primary path for every
intent — the LLM itself decides which device/Brain tool to call, so even
"open YouTube" flows through reasoning, memory and verification instead of a
regex. The legacy keyword routes survive as :func:`legacy_route`, used by the
planner only when no AI provider is reachable (offline fast path) and by the
Android contract tests.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel
from tools.system_control import command_for_request

Route = Literal["android_command", "web_search", "local_tool", "llm", "tool_creation",
                "agent_action", "agent_final"]

TOOL_CREATION_PREFIXES = (
    "make tool",
    "make a tool",
    "create tool",
    "create a tool",
    "build tool",
    "build a tool",
    "write tool",
    "write a tool",
    "add tool",
    "add a tool",
    "generate tool",
    "generate a tool",
    "develop tool",
    "develop a tool",
    "implement tool",
    "implement a tool",
)

class RouteDecision(BaseModel):
    route: Route
    reason: str

def route_request(message: str) -> RouteDecision:
    value = " ".join(message.split()).casefold()

    # 1. The Developer Agent Trigger (Self-Coding)
    if any(value == prefix or value.startswith(f"{prefix} ") for prefix in TOOL_CREATION_PREFIXES):
        return RouteDecision(route="tool_creation", reason="User wants RONIN to write a new Python tool")

    # 2. Everything else -> autonomous agent. The LLM emits structured tool
    # calls (native function calling or <tool> JSON); the planner executes
    # Brain tools inline and dispatches device tools to the Kotlin Body.
    return RouteDecision(route="llm", reason="autonomous agent (ReAct) handles all intents")


def legacy_route(message: str) -> RouteDecision:
    """Pre-agent keyword routing. Offline fallback only (no LLM needed)."""
    value = " ".join(message.split()).casefold()

    if any(value == prefix or value.startswith(f"{prefix} ") for prefix in TOOL_CREATION_PREFIXES):
        return RouteDecision(route="tool_creation", reason="User wants RONIN to write a new Python tool")

    if command_for_request(message):
        return RouteDecision(route="android_command", reason="matches explicit allowlisted Android command")

    if value.startswith(("search web ", "web search ", "look up ")):
        return RouteDecision(route="web_search", reason="explicit web-search request")

    if value.startswith(("remember ", "save fact ")):
        return RouteDecision(route="local_tool", reason="persistent-memory request")

    return RouteDecision(route="llm", reason="general reasoning request")
