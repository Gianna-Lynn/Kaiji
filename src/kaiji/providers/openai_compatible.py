"""通过 OpenAI-compatible Chat Completions 调用单工具回合模型。"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from kaiji.models import (
    Message,
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ProviderErrorCode,
    ProviderFailure,
)
from kaiji.tools import ToolRegistry


class OpenAICompatibleConfig(BaseModel):
    """保存 Provider 配置；API Key 只通过环境变量名间接引用。"""

    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0)
    temperature: float = Field(default=0.0, ge=0)
    system_prompt: str | None = None
    thinking: Literal["disabled"] | None = None
    max_tokens: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def normalize_base_url(self) -> OpenAICompatibleConfig:
        """拒绝明显错误的 URL，同时保留兼容服务的自定义路径前缀。"""

        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须使用 http 或 https")
        self.base_url = self.base_url.rstrip("/")
        return self

    @property
    def endpoint(self) -> str:
        """返回 Chat Completions 完整端点。"""

        return f"{self.base_url}/chat/completions"


class _FunctionCall(BaseModel):
    """校验兼容响应中的函数名称与 JSON 参数字符串。"""

    # 只消费声明字段；兼容服务新增的非语义扩展字段可安全忽略。
    model_config = ConfigDict(extra="ignore", strict=True)

    name: str = Field(min_length=1)
    arguments: str


class _ResponseToolCall(BaseModel):
    """校验单个 OpenAI-compatible 工具调用。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(min_length=1)
    type: str
    function: _FunctionCall


class _ResponseMessage(BaseModel):
    """校验响应消息中最终文本或工具调用的载荷。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    role: str
    content: str | None = None
    tool_calls: list[_ResponseToolCall] | None = None


class _Choice(BaseModel):
    """校验 Chat Completions 的单个候选。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    message: _ResponseMessage
    finish_reason: str | None = None


class _Usage(BaseModel):
    """读取 OpenAI-compatible Token 字段。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class _ChatResponse(BaseModel):
    """只校验 Kaiji 决策所需的最小响应结构。"""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    choices: list[_Choice]
    usage: _Usage | None = None


class OpenAICompatibleProvider:
    """发送完整工具历史，并把 HTTP/协议失败转换为稳定模型。"""

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        registry: ToolRegistry,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._transport = transport

    def decide(self, messages: list[Message]) -> ModelCallResult:
        """执行一次非流式调用；本方法不在内部重试，避免隐藏调用预算。"""
        # TODO 13d（L1）：完成一次显式、无隐藏重试的 HTTP 调用。API Key 只能
        # 从 config 指定的环境变量读取；timeout/transport/429/5xx/其他 4xx、
        # 非 JSON 与协议错误必须映射为稳定 ProviderFailure；成功时保存模型、
        # 请求 ID、延迟和 Token。错误结果不得包含鉴权头或响应正文。
        raise NotImplementedError("TODO 13d：实现 Provider 请求与失败分类")

    def _request_payload(self, messages: list[Message]) -> dict[str, Any]:
        """构造不含 API Key 的请求 JSON。"""
        # TODO 13a（L1）：转换 System Prompt、完整历史与 Registry Schema，并
        # 固定非流式、自动工具选择和温度。thinking/max_tokens 只在配置非空时
        # 出现；任何凭据都不得进入 payload。
        raise NotImplementedError("TODO 13a：构造 OpenAI-compatible 请求载荷")

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """转换完整历史并验证工具调用与结果的严格顺序。"""
        # TODO 13b（L1）：在单条转换之上维护 pending tool_call，要求其后紧跟
        # 同 call_id、同工具名的 tool result；拒绝孤立/缺失/错配/插入其他角色，
        # 并拒绝同一历史复用 call_id。
        raise NotImplementedError("TODO 13b：校验并转换完整消息历史")

    @staticmethod
    def _convert_message(message: Message) -> dict[str, Any]:
        """无损转换 Kaiji 消息，并使用同一 ``tool_call_id`` 配对结果。"""
        # TODO 13b（L1）：分别转换 user、普通 assistant、带单个 tool_call 的
        # assistant 与 tool 消息。参数必须是稳定 JSON 字符串，tool 结果必须
        # 使用与 assistant 调用相同的 tool_call_id。完整历史中工具调用后必须
        # 紧跟同 ID、同工具名的唯一 result；孤立/缺失/错配/插入其他角色和
        # 复用 ID 均在发送前拒绝。
        raise NotImplementedError("TODO 13b：转换并配对消息历史")

    @staticmethod
    def _parse_action(response: _ChatResponse) -> ModelAction:
        """把唯一候选解析为最终回答或至多一个工具调用。"""
        # TODO 13c（L1）：只接受一个 assistant choice；工具路径至多一个
        # function call，arguments 必须解析为有限 JSON 对象且 finish_reason 配对；
        # 无工具路径必须有非空白文本并正常 stop。未消费的兼容扩展字段应被忽略；
        # 保留 assistant_content。
        raise NotImplementedError("TODO 13c：解析单工具回合响应")

    def _failure(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool,
        started: float,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> ModelCallResult:
        """构造不包含请求正文、响应正文或鉴权头的失败结果。"""

        return ModelCallResult(
            failure=ProviderFailure(
                code=code,
                message=message,
                retryable=retryable,
                status_code=status_code,
            ),
            metadata=ModelCallMetadata(
                model=self._config.model,
                latency_ms=self._elapsed_ms(started),
                request_id=request_id,
            ),
        )

    @staticmethod
    def _request_id(response: httpx.Response) -> str | None:
        """读取常见请求标识响应头，不读取可能含敏感内容的正文。"""

        return response.headers.get("x-request-id") or response.headers.get(
            "request-id"
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        """返回非负毫秒延迟。"""

        return max(0.0, (perf_counter() - started) * 1000)
