from __future__ import annotations
from typing import Literal
from pydantic import BaseModel
from tools.system_control import command_for_request

Route = Literal["android_command", "web_search", "local_tool", "llm", "tool_creation"]

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
        
    # 2. Normal Android Commands
    if command_for_request(message): 
        return RouteDecision(route="android_command", reason="matches explicit allowlisted Android command")
        
    # 3. Web Search
    if value.startswith(("search web ", "web search ", "look up ")): 
        return RouteDecision(route="web_search", reason="explicit web-search request")
        
    # 4. Memory
    if value.startswith(("remember ", "save fact ")): 
        return RouteDecision(route="local_tool", reason="persistent-memory request")
        
    # 5. Default Chat
    return RouteDecision(route="llm", reason="general reasoning request")
