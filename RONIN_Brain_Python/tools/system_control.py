"""Device command model + offline fast-path parser.

The agentic ReAct loop (see ``core.planner``) is the primary path: the LLM
emits structured device tools (``open_app``, ``read_screen``, ``click_xy``,
…) which the Kotlin Body executes. This module remains the **offline
fallback** — explicit commands still work with zero AI providers configured —
and the shared ``AndroidCommand`` contract both sides parse.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, model_validator

Action = Literal["click", "set_text", "scroll_forward", "scroll_backward", "global_back", "global_home", "open_bubble", "open_app",
                 "read_screen", "click_xy", "click_node", "scroll", "press_back", "press_home", "get_notifications", "list_apps"]

class AndroidCommand(BaseModel):
    action: Action
    text: str | None = Field(default=None, max_length=500)
    package_name: str | None = Field(default=None, max_length=255)
    # --- agentic fields (additive; legacy bodies ignore them) ---
    x: float | None = None
    y: float | None = None
    node_id: str | None = Field(default=None, max_length=64)
    direction: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def validate_payload(self):
        if self.action in ("click", "set_text") and not self.text:
            raise ValueError(f"{self.action} requires text")
        if self.action == "click_xy" and (self.x is None or self.y is None):
            raise ValueError("click_xy requires x and y")
        return self

def command_for_request(message: str) -> AndroidCommand | None:
    normalized = message.strip()
    lower = normalized.lower()
    for prefix in ("open app ", "launch app ", "open ", "launch "):
        if lower.startswith(prefix) and normalized[len(prefix):].strip():
            target = normalized[len(prefix):].strip()
            return AndroidCommand(action="open_app", text=target)
    if lower.startswith("click "):
        return AndroidCommand(action="click", text=normalized[6:].strip())
    if lower.startswith("type "):
        return AndroidCommand(action="set_text", text=normalized[5:].strip())
    if lower in {"go back", "back"}:
        return AndroidCommand(action="global_back")
    if lower in {"go home", "home"}:
        return AndroidCommand(action="global_home")
    if lower in {"scroll down", "scroll forward"}:
        return AndroidCommand(action="scroll_forward")
    if lower in {"scroll up", "scroll backward"}:
        return AndroidCommand(action="scroll_backward")
    if lower in {"read screen", "what is on screen", "what's on screen", "describe screen"}:
        return AndroidCommand(action="read_screen")
    if lower in {"show notifications", "read notifications", "check notifications"}:
        return AndroidCommand(action="get_notifications")
    return None
