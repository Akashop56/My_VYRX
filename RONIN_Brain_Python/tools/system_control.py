from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, model_validator

Action = Literal["click", "set_text", "scroll_forward", "scroll_backward", "global_back", "global_home", "open_bubble"]

class AndroidCommand(BaseModel):
    action: Action
    text: str | None = Field(default=None, max_length=500)
    package_name: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_payload(self):
        if self.action in ("click", "set_text") and not self.text:
            raise ValueError(f"{self.action} requires text")
        return self

def command_for_request(message: str) -> AndroidCommand | None:
    normalized = message.strip()
    lower = normalized.lower()
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
    return None
