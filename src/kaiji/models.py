"""定义消息、工具调用、结构化失败、Trace 与 Runtime 返回值等核心模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ToolErrorCode(StrEnum):
    """工具调用在进入 Runtime 后可能产生的稳定错误分类。"""

    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    TIMEOUT = "timeout"
    EXECUTION_FAILED = "execution_failed"
    INVALID_RESULT = "invalid_result"


class StopReason(StrEnum):
    """Agent Runtime 和工作流对外暴露的明确停止原因。"""

    COMPLETED = "completed"
    TOOL_FAILED = "tool_failed"
    MAX_STEPS = "max_steps"
    HUMAN_REJECTED = "human_rejected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    DUPLICATE_CALL = "duplicate_call"


class ToolCall(BaseModel):
    """
    描述模型请求执行的一次具名工具调用。  
    类变量说明:  
    - call_id: str  
    - name: str  
    - argument: dict[str, Any]  
    """

    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class ToolFailure(BaseModel):
    """保存可序列化的工具失败及其重试属性。  
    部分类变量说明:
    retryable: bool: 说明这次失败后，在“不改参数、不换工具”的前提下，再执行一次有没有合理机会成功？
    retryable的值是由这一种失败本身的性质决定的.
    """
    code: ToolErrorCode
    message: str
    retryable: bool


class ToolResult(BaseModel):
    """保存工具成功输出或结构化失败，两者必须严格互斥。
    部分类变量说明:
    attempts: int, 大于等于零. attempt记录的是: 到产生这个结果为止, 工具实际上被调用了多少次.
    attempts等于零的情况: UNKNOWN_TOOL: 工具名在注册表中找不到, 所以没有执行. INVALID_ARGUMENTS: 参数校验没通过, 所以没有执行.
    """

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
    """表示用户、助手或工具在一次对话中的消息。"""

    role: Literal["user", "assistant", "tool"]
    content: str
    tool_result: ToolResult | None = None


class ModelAction(BaseModel):
    """表示模型下一步选择调用工具或直接给出最终回答。"""

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
    """记录某一步发生的模型动作、工具结果或停止事件。"""

    step: int
    event: Literal["model_action", "tool_result", "stop"]
    data: dict[str, Any]


class RunResult(BaseModel):
    """汇总一次 Agent 运行的回答、停止原因、证据、消息和 Trace。"""

    answer: str | None
    stop_reason: StopReason
    steps: int
    messages: list[Message]
    successful_tool_results: list[ToolResult]
    trace: list[TraceEvent]
