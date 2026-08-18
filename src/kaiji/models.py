"""定义消息、工具调用、结构化失败、Trace 与 Runtime 返回值等核心模型。"""

from __future__ import annotations

from enum import StrEnum
import json
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


class ToolErrorCode(StrEnum):
    """工具调用在进入 Runtime 后可能产生的稳定错误分类。"""

    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    TIMEOUT = "timeout"
    EXECUTION_FAILED = "execution_failed"
    INVALID_RESULT = "invalid_result"


class StopReason(StrEnum):
    """表示 Agent Runtime 和工作流对外暴露的明确停止原因。"""

    COMPLETED = "completed"
    TOOL_FAILED = "tool_failed"
    MAX_STEPS = "max_steps"
    HUMAN_REJECTED = "human_rejected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    DUPLICATE_CALL = "duplicate_call"
    MODEL_FAILED = "model_failed"


class ProviderErrorCode(StrEnum):
    """模型 Provider 在 HTTP 或协议边界产生的稳定失败分类。"""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TRANSPORT_ERROR = "transport_error"
    INVALID_JSON = "invalid_json"
    INVALID_RESPONSE = "invalid_response"
    HTTP_ERROR = "http_error"
    INTERNAL_ERROR = "internal_error"
    REQUEST_BUDGET_EXHAUSTED = "request_budget_exhausted"


class ToolCall(BaseModel):
    """描述模型请求执行的一次具名工具调用。"""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]

    @field_validator("call_id", "name")
    @classmethod
    def identifiers_are_not_blank(cls, value: str) -> str:
        """调用身份和工具名称不得使用纯空白字符串。"""

        if not value.strip():
            raise ValueError("工具调用标识不得为空白")
        return value

    @model_validator(mode="after")
    def arguments_are_finite_json(self) -> ToolCall:
        """工具参数必须能进入稳定签名、消息和 Trace。"""

        try:
            json.dumps(self.arguments, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("工具参数必须是有限 JSON 值") from exc
        return self


class ToolFailure(BaseModel):
    """保存可序列化的工具失败及其重试属性。"""

    model_config = ConfigDict(extra="forbid")

    code: ToolErrorCode
    message: str = Field(min_length=1)
    retryable: StrictBool


class ToolResult(BaseModel):
    """保存工具成功输出或结构化失败，两者必须严格互斥。"""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    ok: StrictBool
    attempts: StrictInt = Field(ge=0)
    output: dict[str, Any] | None = None
    error: ToolFailure | None = None

    @model_validator(mode="after")
    def result_has_exactly_one_payload(self) -> ToolResult:
        if self.ok == (self.output is None):
            raise ValueError("a successful result needs output; a failure needs error")
        if self.ok == (self.error is not None):
            raise ValueError("output and error must match the ok flag")
        if self.ok and self.attempts < 1:
            raise ValueError("成功工具结果必须至少执行一次 handler")
        if not self.ok and self.error is not None:
            before_handler = self.error.code in {
                ToolErrorCode.UNKNOWN_TOOL,
                ToolErrorCode.INVALID_ARGUMENTS,
            }
            if before_handler != (self.attempts == 0):
                raise ValueError("工具失败类别必须与 handler 尝试次数一致")
        if self.output is not None:
            try:
                json.dumps(self.output, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("工具输出必须是有限 JSON 值") from exc
        return self


class Message(BaseModel):
    """表示一次对话中的消息，并维护角色与结构化载荷不变量。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "tool"]
    content: str
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None

    @model_validator(mode="after")
    def role_has_matching_payload(self) -> Message:
        """拒绝无法无损映射到模型工具调用协议的角色/载荷组合。"""

        if self.role == "user":
            if self.tool_call is not None or self.tool_result is not None:
                raise ValueError("user 消息不能携带工具调用或工具结果")
        elif self.role == "assistant":
            if self.tool_result is not None:
                raise ValueError("assistant 消息不能携带工具结果")
            if self.tool_call is None and not self.content:
                raise ValueError("assistant 普通消息必须包含非空文本")
        else:
            if self.tool_call is not None:
                raise ValueError("tool 消息不能携带 assistant 工具调用")
            if self.tool_result is None:
                raise ValueError("tool 消息必须携带结构化工具结果")
        return self


class ModelAction(BaseModel):
    """表示模型下一步选择调用工具或直接给出最终回答。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool_call", "final_answer"]
    tool_call: ToolCall | None = None
    final_answer: str | None = None
    assistant_content: str = ""

    @model_validator(mode="after")
    def action_has_matching_payload(self) -> ModelAction:
        if self.kind == "tool_call":
            if self.tool_call is None or self.final_answer is not None:
                raise ValueError("tool_call action 只能包含工具调用")
        elif (
            not self.final_answer
            or self.tool_call is not None
            or self.assistant_content
        ):
            raise ValueError("final_answer action 只能包含非空最终回答")
        return self


class ModelUsage(BaseModel):
    """记录一次模型调用的 Token 用量；未知字段保持为空而非伪造为零。"""

    model_config = ConfigDict(extra="forbid")

    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)


class ModelCallMetadata(BaseModel):
    """记录不含鉴权信息的模型调用元数据。"""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    request_id: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)

    @field_validator("model")
    @classmethod
    def model_is_not_blank(cls, value: str) -> str:
        """模型身份必须可用于实验追踪。"""

        if not value.strip():
            raise ValueError("model 不得为空白")
        return value


class ProviderFailure(BaseModel):
    """描述 Provider 的可序列化失败及其重试属性。"""

    model_config = ConfigDict(extra="forbid")

    code: ProviderErrorCode
    message: str = Field(min_length=1)
    retryable: StrictBool
    status_code: StrictInt | None = Field(default=None, ge=100, le=599)


class ProviderAttemptFailure(BaseModel):
    """记录逻辑模型回合中一次已失败且随后重试的真实请求。"""

    model_config = ConfigDict(extra="forbid")

    failure: ProviderFailure
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    request_id: str | None = None

    @model_validator(mode="after")
    def prior_failure_must_have_been_retryable(self) -> ProviderAttemptFailure:
        """只有明确可重试的失败才能出现在已发生重试的历史中。"""

        if not self.failure.retryable:
            raise ValueError("prior_attempt_failure 必须可重试")
        return self


class ModelCallResult(BaseModel):
    """保存一次模型调用的动作或失败，两种载荷严格互斥。"""

    model_config = ConfigDict(extra="forbid")

    action: ModelAction | None = None
    failure: ProviderFailure | None = None
    metadata: ModelCallMetadata
    prior_attempt_failures: list[ProviderAttemptFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_exactly_one_outcome(self) -> ModelCallResult:
        """要求调用恰好产生动作或结构化失败。"""

        if (self.action is None) == (self.failure is None):
            raise ValueError("模型调用必须且只能包含 action 或 failure")
        return self


class TraceEvent(BaseModel):
    """记录某一步发生的模型动作、工具结果或停止事件。"""

    model_config = ConfigDict(extra="forbid")

    step: StrictInt = Field(ge=1)   # ge=1 是greater or equal的缩写, 表示这个值必须满足大于等于 1 的约束
    event: Literal["model_action", "model_failure", "tool_result", "stop"]
    data: dict[str, Any]

    @model_validator(mode="after")
    def data_is_limited_json(self) -> TraceEvent:
        try:
            json.dumps(self.data, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("不满足有限JSON, 存在nan/infinity/object")
        return self

class RunResult(BaseModel):
    """汇总一次 Agent 运行的回答、停止原因、证据、消息和 Trace。"""

    model_config = ConfigDict(extra="forbid")

    answer: str | None
    stop_reason: StopReason
    steps: StrictInt = Field(ge=1)
    messages: list[Message]
    successful_tool_results: list[ToolResult]
    trace: list[TraceEvent]
    model_calls: list[ModelCallResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def runtime_artifacts_are_consistent(self) -> RunResult:
        """校验回答、停止事件、步骤、模型回合和成功证据属于同一次运行。"""

        if self.stop_reason == StopReason.COMPLETED:
            if self.answer is None or not self.answer.strip():
                raise ValueError("completed 运行必须包含非空回答")
        elif self.answer is not None:
            raise ValueError("非 completed 运行不得携带最终回答")
        if not self.trace:
            raise ValueError("RunResult 必须包含完整 Trace")
        stop_events = [event for event in self.trace if event.event == "stop"]
        if len(stop_events) != 1 or self.trace[-1].event != "stop":
            raise ValueError("RunResult Trace 必须以唯一 stop 事件结束")
        try:
            trace_reason = StopReason(stop_events[0].data.get("reason"))
        except (TypeError, ValueError) as exc:
            raise ValueError("RunResult stop Trace 必须包含合法 reason") from exc
        if trace_reason != self.stop_reason:
            raise ValueError("RunResult stop_reason 与 Trace 不一致")
        trace_steps = [event.step for event in self.trace]
        if trace_steps != sorted(trace_steps) or max(trace_steps) != self.steps:
            raise ValueError("RunResult steps 必须等于 Trace 的最后逻辑步骤")
        if len(self.model_calls) != self.steps:
            raise ValueError("RunResult 每个逻辑步骤必须对应一次模型调用")
        tool_results = [
            message.tool_result
            for message in self.messages
            if message.role == "tool" and message.tool_result is not None
        ]
        successful_from_messages = [result for result in tool_results if result.ok]
        if successful_from_messages != self.successful_tool_results:
            raise ValueError("RunResult 成功证据必须与工具消息中的成功结果一致")
        return self
