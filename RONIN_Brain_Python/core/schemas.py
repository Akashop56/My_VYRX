"""Pydantic request/response schemas shared by the API surface and the planner.

Kept in a separate module so `core.planner` can use them without creating a
circular import with `main.py`.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Route = Literal["android_command", "web_search", "local_tool", "llm", "tool_creation",
                "agent_action", "agent_final"]

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
    #: Force the SSE agent stream (thinking / tool_call / observation / token).
    #: ``None`` = negotiate from the request's ``Accept`` header.
    stream: bool | None = None


class UpdateProposal(BaseModel):
    proposal_id: str
    file_path: str
    module_name: str
    new_code: str
    summary: str


class AgentAction(BaseModel):
    """One pending device-side tool call for the Kotlin Body to execute.

    The Body MUST execute it (open app, read screen, click, …) and POST the
    observation back to ``/agent/result`` without requiring any user tap.
    """

    tool: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str | None = Field(default=None, max_length=64)
    thought: str | None = Field(default=None, max_length=2000)


class AskResponse(BaseModel):
    response: str
    route: Route
    command: AndroidCommandSchema | None = None
    update_proposal: UpdateProposal | None = None
    error: str | None = None
    # --- agentic loop fields (additive; old bodies ignore them) ---
    action: AgentAction | None = None
    needs_tool_result: bool = False
    thought: str | None = None
    steps: int = 0
    #: True when the answer was (or will be) delivered over the live SSE
    #: stream instead of this JSON body — a streamed turn ends with `done`.
    streamed: bool = False


class ToolResultRequest(BaseModel):
    """Hidden background callback: Body -> Brain execution observation.

    Sent automatically by the Kotlin Body after executing an ``AgentAction``,
    continuing the ReAct loop without user interaction.
    """

    session_id: str = Field(default="default", min_length=1, max_length=128)
    tool: str = Field(min_length=1, max_length=64)
    result: str = Field(default="", max_length=20000)
    success: bool = True
    tool_call_id: str | None = Field(default=None, max_length=64)


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
