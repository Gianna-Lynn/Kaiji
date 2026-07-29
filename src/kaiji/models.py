from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ToolErrorCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    TIMEOUT = "timeout"
    EXECUTION_FAILED = "execution_failed"
    INVALID_RESULT = "invalid_result"


class StopReason(StrEnum):
    COMPLETED = "completed"
    TOOL_FAILED = "tool_failed"
    MAX_STEPS = "max_steps"


class ToolCall(BaseModel):
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class ToolFailure(BaseModel):
    code: ToolErrorCode
    message: str
    retryable: bool


class ToolResult(BaseModel):
    call_id: str
    tool_name: str
    ok: bool
    attempts: int = Field(ge=0)
    output: dict[str, Any] | None = None
    error: ToolFailure | None = None

    @model_validator(mode="after")
    def result_has_exactly_one_payload(self) -> ToolResult:
        if self.ok == (self.output is None):
            raise ValueError("a successful result needs output; a failure needs error")
        if self.ok == (self.error is not None):
            raise ValueError("output and error must match the ok flag")
        return self


class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_result: ToolResult | None = None


class ModelAction(BaseModel):
    kind: Literal["tool_call", "final_answer"]
    tool_call: ToolCall | None = None
    final_answer: str | None = None

    @model_validator(mode="after")
    def action_has_matching_payload(self) -> ModelAction:
        if self.kind == "tool_call" and self.tool_call is None:
            raise ValueError("tool_call action needs a tool call")
        if self.kind == "final_answer" and not self.final_answer:
            raise ValueError("final_answer action needs non-empty text")
        return self


class TraceEvent(BaseModel):
    step: int
    event: Literal["model_action", "tool_result", "stop"]
    data: dict[str, Any]


class RunResult(BaseModel):
    answer: str | None
    stop_reason: StopReason
    steps: int
    messages: list[Message]
    successful_tool_results: list[ToolResult]
    trace: list[TraceEvent]
