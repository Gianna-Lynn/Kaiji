"""通过 OpenAI-compatible Chat Completions 调用单工具回合模型。"""

from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kaiji.models import (
    Message,
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ModelUsage,
    ProviderErrorCode,
    ProviderFailure,
    ToolCall,
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

        started = perf_counter()
        api_key = os.environ.get(self._config.api_key_env)
        if not api_key:
            return self._failure(
                ProviderErrorCode.HTTP_ERROR,
                f"环境变量 {self._config.api_key_env} 未设置",
                retryable=False,
                started=started,
            )

        response: httpx.Response | None = None
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=self._config.timeout_seconds,
            ) as client:
                response = client.post(
                    self._config.endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=self._request_payload(messages),
                )
        except httpx.TimeoutException:
            return self._failure(
                ProviderErrorCode.TIMEOUT,
                "模型请求超时",
                retryable=True,
                started=started,
            )
        except httpx.TransportError:
            return self._failure(
                ProviderErrorCode.TRANSPORT_ERROR,
                "模型网络传输失败",
                retryable=True,
                started=started,
            )

        request_id = self._request_id(response)
        if response.status_code == 429:
            return self._failure(
                ProviderErrorCode.RATE_LIMITED,
                "模型服务触发限流",
                retryable=True,
                started=started,
                status_code=429,
                request_id=request_id,
            )
        if response.status_code >= 500:
            return self._failure(
                ProviderErrorCode.SERVER_ERROR,
                "模型服务端失败",
                retryable=True,
                started=started,
                status_code=response.status_code,
                request_id=request_id,
            )
        if response.status_code >= 400:
            return self._failure(
                ProviderErrorCode.HTTP_ERROR,
                "模型服务拒绝请求",
                retryable=False,
                started=started,
                status_code=response.status_code,
                request_id=request_id,
            )

        try:
            raw_payload = response.json()
        except ValueError:
            return self._failure(
                ProviderErrorCode.INVALID_JSON,
                "模型响应不是合法 JSON",
                retryable=False,
                started=started,
                request_id=request_id,
            )

        try:
            parsed = _ChatResponse.model_validate(raw_payload)
            action = self._parse_action(parsed)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            return self._failure(
                ProviderErrorCode.INVALID_RESPONSE,
                "模型响应不符合单工具回合协议",
                retryable=False,
                started=started,
                request_id=request_id,
            )

        usage = parsed.usage or _Usage()
        return ModelCallResult(
            action=action,
            metadata=ModelCallMetadata(
                model=parsed.model or self._config.model,
                latency_ms=self._elapsed_ms(started),
                request_id=request_id or parsed.id,
                usage=ModelUsage(
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                ),
            ),
        )

    def _request_payload(self, messages: list[Message]) -> dict[str, Any]:
        """构造不含 API Key 的请求 JSON。"""

        converted: list[dict[str, Any]] = []
        if self._config.system_prompt is not None:
            converted.append({"role": "system", "content": self._config.system_prompt})
        converted.extend(self._convert_messages(messages))
        tools = [
            {"type": "function", "function": schema}
            for schema in self._registry.schemas()
        ]
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": converted,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": self._config.temperature,
            "stream": False,
        }
        if self._config.thinking is not None:
            # DeepSeek V4 默认启用思考；本协议不保存 reasoning_content，因此
            # 必须显式关闭，避免后续工具回填违反其多轮请求契约。
            payload["thinking"] = {"type": self._config.thinking}
        if self._config.max_tokens is not None:
            payload["max_tokens"] = self._config.max_tokens
        return payload

    @staticmethod
    def _convert_message(message: Message) -> dict[str, Any]:
        """无损转换 Kaiji 消息，并使用同一 ``tool_call_id`` 配对结果。"""

        if message.role == "user":
            return {"role": "user", "content": message.content}
        if message.role == "assistant" and message.tool_call is None:
            return {"role": "assistant", "content": message.content}
        if message.role == "assistant":
            call = message.tool_call
            assert call is not None
            return {
                "role": "assistant",
                "content": message.content or None,
                "tool_calls": [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                call.arguments,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        },
                    }
                ],
            }
        result = message.tool_result
        assert result is not None
        return {
            "role": "tool",
            "tool_call_id": result.call_id,
            "content": message.content,
        }

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """转换完整历史，并验证每个 assistant/tool 回合的严格配对。

        OpenAI-compatible 的 tool 消息没有独立的回合身份，唯一可靠的关联键是
        ``tool_call_id``。因此本实现限定一个 assistant 工具调用后必须紧跟一个
        同 ID、同工具名的 tool 结果；孤立、错配、缺失结果和插入其他角色都在
        发请求前拒绝。调用 ID 在一份历史中也不得复用，避免把旧结果误配到新回合。
        """

        converted: list[dict[str, Any]] = []
        seen_call_ids: set[str] = set()
        pending: ToolCall | None = None
        for message in messages:
            if pending is not None:
                if message.role != "tool" or message.tool_result is None:
                    raise ValueError("assistant 工具调用后必须紧跟对应 tool 结果")
                result = message.tool_result
                if (
                    result.call_id != pending.call_id
                    or result.tool_name != pending.name
                ):
                    raise ValueError("tool 结果必须与前一个 assistant 工具调用配对")
                converted.append(self._convert_message(message))
                pending = None
                continue

            if message.role == "tool":
                raise ValueError("tool 结果不能没有前一个 assistant 工具调用")
            converted.append(self._convert_message(message))
            if message.role == "assistant" and message.tool_call is not None:
                call = message.tool_call
                if call.call_id in seen_call_ids:
                    raise ValueError("同一历史不得复用 tool_call_id")
                seen_call_ids.add(call.call_id)
                pending = call

        if pending is not None:
            raise ValueError("assistant 工具调用缺少对应 tool 结果")
        return converted

    @staticmethod
    def _parse_action(response: _ChatResponse) -> ModelAction:
        """把唯一候选解析为最终回答或至多一个工具调用。"""

        if len(response.choices) != 1:
            raise ValueError("响应必须恰好包含一个 choice")
        message = response.choices[0].message
        if message.role != "assistant":
            raise ValueError("响应角色必须是 assistant")
        calls = message.tool_calls or []
        if len(calls) > 1:
            raise ValueError("每个模型回合最多允许一个工具调用")
        if calls:
            if response.choices[0].finish_reason not in {None, "tool_calls"}:
                raise ValueError("工具调用的 finish_reason 不一致")
            call = calls[0]
            if call.type != "function":
                raise ValueError("只支持 function 工具调用")
            # Python 的 json.loads 默认接受 NaN/Infinity；这些值不能进入 ToolCall
            # 或后续 Trace，因此在协议边界显式拒绝，而不是依赖序列化时才失败。
            def reject_nonfinite(value: str) -> None:
                """拒绝 Python JSON 解码器默认放行的非有限常量。"""

                raise ValueError(f"不允许非有限 JSON 常量：{value}")

            arguments = json.loads(
                call.function.arguments,
                parse_constant=reject_nonfinite,
            )
            if not isinstance(arguments, dict):
                raise TypeError("工具参数必须是 JSON 对象")
            return ModelAction(
                kind="tool_call",
                tool_call=ToolCall(
                    call_id=call.id,
                    name=call.function.name,
                    arguments=arguments,
                ),
                assistant_content=message.content or "",
            )
        if not message.content or not message.content.strip():
            raise ValueError("无工具调用时必须包含非空白最终回答")
        if response.choices[0].finish_reason not in {None, "stop"}:
            raise ValueError("最终回答未正常停止")
        return ModelAction(kind="final_answer", final_answer=message.content)

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
