"""离线验证 OpenAI-compatible Provider 的消息、Schema 与失败分类。"""

from __future__ import annotations

import json

import httpx
import pytest

from kaiji.demo import build_registry
from kaiji.models import (
    Message,
    ProviderErrorCode,
    ToolCall,
    ToolResult,
)
from kaiji.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)


def _provider(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> OpenAICompatibleProvider:
    """构造只访问 MockTransport 的 Provider。"""

    monkeypatch.setenv("KAIJI_TEST_API_KEY", "test-secret")
    return OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.invalid/v1",
            model="qwen-test",
            api_key_env="KAIJI_TEST_API_KEY",
            system_prompt="只根据工具证据回答。",
            thinking="disabled",
            max_tokens=512,
        ),
        build_registry(),
        transport=httpx.MockTransport(handler),
    )


def test_provider_sends_schema_and_round_trips_tool_call_id(monkeypatch) -> None:
    """完整 assistant tool_call 与 tool 结果必须使用同一调用标识。"""

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(
            200,
            headers={"x-request-id": "request-7"},
            json={
                "id": "completion-7",
                "model": "qwen-response-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "我来计算。",
                            "tool_calls": [
                                {
                                    "id": "call-7",
                                    "type": "function",
                                    "function": {
                                        "name": "calculate_discount",
                                        "arguments": '{"percent":25,"price":120}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 5,
                    "total_tokens": 16,
                },
            },
        )

    result = _provider(monkeypatch, handler).decide(
        [
            Message(role="user", content="计算折扣"),
            Message(
                role="assistant",
                content="",
                tool_call=ToolCall(
                    call_id="previous-call",
                    name="calculate_discount",
                    arguments={"price": 100, "percent": 10},
                ),
            ),
            Message(
                role="tool",
                content='{"final_price":"90"}',
                tool_result=ToolResult(
                    call_id="previous-call",
                    tool_name="calculate_discount",
                    ok=True,
                    attempts=1,
                    output={"final_price": "90"},
                ),
            ),
        ]
    )

    assert result.failure is None
    assert result.action.tool_call.call_id == "call-7"
    assert result.action.assistant_content == "我来计算。"
    assert result.metadata.request_id == "request-7"
    assert result.metadata.usage.total_tokens == 16
    assert captured["messages"][0] == {
        "role": "system",
        "content": "只根据工具证据回答。",
    }
    assert captured["messages"][-2]["tool_calls"][0]["id"] == "previous-call"
    assert captured["messages"][-1]["tool_call_id"] == "previous-call"
    assert captured["tools"][0]["type"] == "function"
    assert "parameters" in captured["tools"][0]["function"]
    assert captured["stream"] is False
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["max_tokens"] == 512


def test_provider_parses_final_answer(monkeypatch) -> None:
    """无工具调用时，非空 assistant content 必须成为最终回答。"""

    provider = _provider(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "答案"}}]},
        ),
    )

    result = provider.decide([Message(role="user", content="直接回答")])

    assert result.action.final_answer == "答案"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, ProviderErrorCode.RATE_LIMITED, True),
        (503, ProviderErrorCode.SERVER_ERROR, True),
        (401, ProviderErrorCode.HTTP_ERROR, False),
    ],
)
def test_provider_classifies_http_failures(
    monkeypatch, status: int, code: ProviderErrorCode, retryable: bool
) -> None:
    """HTTP 状态只能映射到明确且稳定的失败类别。"""

    provider = _provider(
        monkeypatch,
        lambda request: httpx.Response(status, text="sensitive response body"),
    )

    result = provider.decide([Message(role="user", content="问题")])

    assert result.failure.code == code
    assert result.failure.retryable is retryable
    assert result.failure.status_code == status
    assert "sensitive response body" not in result.model_dump_json()


def test_provider_classifies_timeout(monkeypatch) -> None:
    """网络超时必须可重试且不得抛出到 Runtime。"""

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret timeout detail", request=request)

    result = _provider(monkeypatch, timeout).decide(
        [Message(role="user", content="问题")]
    )

    assert result.failure.code == ProviderErrorCode.TIMEOUT
    assert result.failure.retryable is True
    assert "secret timeout detail" not in result.model_dump_json()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "a",
                                    "type": "function",
                                    "function": {"name": "x", "arguments": "{}"},
                                },
                                {
                                    "id": "b",
                                    "type": "function",
                                    "function": {"name": "y", "arguments": "{}"},
                                },
                            ],
                        }
                    }
                ]
            },
        ),
    ],
)
def test_provider_rejects_invalid_json_protocol_and_multiple_calls(
    monkeypatch, response: httpx.Response
) -> None:
    """非法 JSON、空候选和多工具回合都必须结构化失败。"""

    result = _provider(monkeypatch, lambda request: response).decide(
        [Message(role="user", content="问题")]
    )

    assert result.failure.code in {
        ProviderErrorCode.INVALID_JSON,
        ProviderErrorCode.INVALID_RESPONSE,
    }
    assert result.failure.retryable is False


def test_provider_reads_key_only_from_environment(monkeypatch) -> None:
    """缺失环境变量时不得发送请求或把其他来源当作密钥。"""

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    monkeypatch.delenv("KAIJI_TEST_API_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url="https://model.invalid/v1",
            model="qwen-test",
            api_key_env="KAIJI_TEST_API_KEY",
        ),
        build_registry(),
        transport=httpx.MockTransport(handler),
    )

    result = provider.decide([Message(role="user", content="问题")])

    assert result.failure.code == ProviderErrorCode.HTTP_ERROR
    assert called is False
    assert "test-secret" not in result.model_dump_json()


def test_provider_rejects_truncated_final_answer(monkeypatch) -> None:
    """命中输出上限的部分文本不得被当作完整最终回答。"""

    provider = _provider(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "截断文本"},
                        "finish_reason": "length",
                    }
                ]
            },
        ),
    )

    result = provider.decide([Message(role="user", content="问题")])

    assert result.failure.code == ProviderErrorCode.INVALID_RESPONSE
