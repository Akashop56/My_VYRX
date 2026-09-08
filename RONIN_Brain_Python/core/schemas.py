"""Pydantic request/response schemas shared by the API surface and the planner.

Kept in a separate module so `core.planner` can use them without creating a
circular import with `main.py`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Route = Literal["android_command", "web_search", "local_tool", "llm", "tool_creation"]

from tools.system_control import AndroidCommand as AndroidCommandSchema  # noqa: E402


class ProviderRequest(BaseModel):
    provider: Literal["openai", "gemini", "groq", "openrouter", "custom"]
    api_key: str = Field(min_length=1, max_length=1024)
    endpoint: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=256)


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str = Field(default="default", min_length=1, max_length=128)
    providers: list[ProviderRequest] = Field(default_factory=list, max_length=10)
    # --- VYRX body -> brain signals (additive, optional) ---
    input_mode: str = Field(default="text", max_length=16)  # "text" | "voice"
    tools_enabled: dict[str, bool] = Field(default_factory=dict)
    personality: str | None = Field(default=None, max_length=32)
    response_mode: str | None = Field(default=None, max_length=32)


class UpdateProposal(BaseModel):
    proposal_id: str
    file_path: str
    module_name: str
    new_code: str
    summary: str


class AskResponse(BaseModel):
    response: str
    route: Route
    command: AndroidCommandSchema | None = None
    update_proposal: UpdateProposal | None = None
    error: str | None = None


class ApprovalRequest(BaseModel):
    approved: bool
    proposal: UpdateProposal


class ApprovalResponse(BaseModel):
    accepted: bool
    success: bool
    message: str


# ---------------------------------------------------------------------------
# VYRX UI support schemas
# ---------------------------------------------------------------------------

MemoryCategory = Literal["personal", "experience", "knowledge"]


class MemoryRequest(BaseModel):
    category: MemoryCategory = "personal"
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=8000)
    importance: int = Field(default=3, ge=1, le=5)
    pinned: bool = False
    source: str = Field(default="user", max_length=64)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class MemoryUpdate(BaseModel):
    category: MemoryCategory | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=8000)
    importance: int | None = Field(default=None, ge=1, le=5)
    pinned: bool | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ProviderUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    enabled: bool | None = None
    model: str | None = Field(default=None, max_length=256)
    endpoint: str | None = Field(default=None, max_length=2048)


class FeedbackRequest(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=500)


class DeviceStatusRequest(BaseModel):
    accessibility: bool | None = None
    notifications: bool | None = None
    microphone: bool | None = None
    battery_saver_ok: bool | None = None
    body_version: str | None = Field(default=None, max_length=32)
