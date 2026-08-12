"""验证原生 Agent Runtime 的消息配对、失败边界和停止保护。"""

import pytest
from pydantic import ValidationError

from kaiji.demo import ScriptedDecisionProvider, build_registry
from kaiji.models import (
    Message,
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ProviderErrorCode,
    ProviderFailure,
    RunResult,
    StopReason,
    ToolCall,
)
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolExecutor


def action_for(
    tool_name: str = "calculate_discount",
    *,
    percent: int = 10,
    call_id: str = "call-1",
) -> ModelAction:
    return ModelAction(
        kind="tool_call",
        tool_call=ToolCall(
            call_id=call_id,
            name=tool_name,
            arguments={"price": 100, "percent": percent},
        ),
    )


def test_runtime_backfills_valid_result_then_finishes() -> None:
    provider = ScriptedDecisionProvider(
        [
            action_for(),
            ModelAction(kind="final_answer", final_answer="The price is 90."),
        ]
    )
    runtime = AgentRuntime(provider, ToolExecutor(build_registry()))

    result = runtime.run("Calculate the price")

    assert result.stop_reason == StopReason.COMPLETED
    assert result.answer == "The price is 90."
    assert result.successful_tool_results[0].output == {"final_price": "90"}
    assert result.messages[-1].role == "tool"
    assert result.messages[-2].tool_call == action_for().tool_call
    assert (
        result.messages[-1].tool_result.call_id == result.messages[-2].tool_call.call_id
    )
    assert [event.event for event in result.trace] == [
        "model_action",
        "tool_result",
        "model_action",
        "stop",
    ]


def test_runtime_stops_on_tool_failure() -> None:
    provider = ScriptedDecisionProvider([action_for("missing")])
    runtime = AgentRuntime(provider, ToolExecutor(build_registry()))

    result = runtime.run("Use a missing tool")

    assert result.stop_reason == StopReason.TOOL_FAILED
    assert result.answer is None
    assert result.successful_tool_results == []


def test_runtime_has_hard_maximum_steps() -> None:
    provider = ScriptedDecisionProvider(
        [action_for(), action_for(percent=20, call_id="call-2")]
    )
    runtime = AgentRuntime(
        provider,
        ToolExecutor(build_registry()),
        max_steps=2,
    )

    result = runtime.run("Never finish")

    assert result.stop_reason == StopReason.MAX_STEPS
    assert result.steps == 2
    assert result.answer is None


def test_runtime_stops_repeated_identical_calls() -> None:
    provider = ScriptedDecisionProvider([action_for(), action_for(call_id="call-2")])
    runtime = AgentRuntime(provider, ToolExecutor(build_registry()), max_steps=3)

    result = runtime.run("Repeat forever")

    assert result.stop_reason == StopReason.DUPLICATE_CALL
    assert len(result.successful_tool_results) == 1


def test_runtime_rejects_reused_tool_call_id_before_second_execution() -> None:
    provider = ScriptedDecisionProvider(
        [action_for(), action_for(percent=20, call_id="call-1")]
    )
    result = AgentRuntime(
        provider,
        ToolExecutor(build_registry()),
        max_steps=3,
    ).run("Reuse protocol identity")

    assert result.stop_reason == StopReason.MODEL_FAILED
    assert len(result.successful_tool_results) == 1
    assert [event.event for event in result.trace[-3:]] == [
        "model_action",
        "model_failure",
        "stop",
    ]
    assert result.trace[-2].data["code"] == ProviderErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize("question", ["", "   ", None, 7])
def test_runtime_rejects_blank_or_non_string_question(question) -> None:
    provider = ScriptedDecisionProvider(
        [ModelAction(kind="final_answer", final_answer="ok")]
    )
    with pytest.raises(ValueError, match="question"):
        AgentRuntime(provider, ToolExecutor(build_registry())).run(question)


def test_message_rejects_illegal_role_payload_combinations() -> None:
    """角色不变量必须在进入 Provider 之前直接拒绝非法历史。"""

    with pytest.raises(ValidationError, match="user 消息不能"):
        Message(role="user", content="问题", tool_call=action_for().tool_call)
    with pytest.raises(ValidationError, match="tool 消息必须"):
        Message(role="tool", content="缺少结构化结果")
    with pytest.raises(ValidationError, match="assistant 普通消息"):
        Message(role="assistant", content="")


def test_runtime_structures_provider_failure_without_completing() -> None:
    """Provider 已分类失败必须安全停止，不能被记为正常回答。"""

    class FailingProvider:
        def decide(self, messages: list[Message]) -> ModelCallResult:
            del messages
            return ModelCallResult(
                failure=ProviderFailure(
                    code=ProviderErrorCode.RATE_LIMITED,
                    message="请求过多",
                    retryable=True,
                    status_code=429,
                ),
                metadata=ModelCallMetadata(
                    model="mock-model", latency_ms=12, request_id="request-1"
                ),
            )

    result = AgentRuntime(FailingProvider(), ToolExecutor(build_registry())).run(
        "测试模型失败"
    )

    assert result.stop_reason == StopReason.MODEL_FAILED
    assert result.answer is None
    assert result.model_calls[0].failure.code == ProviderErrorCode.RATE_LIMITED
    assert [event.event for event in result.trace] == ["model_failure", "stop"]


def test_runtime_hides_unexpected_provider_exception_text() -> None:
    """未知异常只记录类型，潜在 Authorization 内容不得进入 Trace。"""

    class ExplodingProvider:
        def decide(self, messages: list[Message]) -> ModelCallResult:
            del messages
            raise RuntimeError("Authorization: Bearer secret-value")

    result = AgentRuntime(ExplodingProvider(), ToolExecutor(build_registry())).run(
        "测试异常净化"
    )

    serialized = result.model_dump_json()
    assert result.stop_reason == StopReason.MODEL_FAILED
    assert "RuntimeError" in serialized
    assert "secret-value" not in serialized


def test_runtime_rejects_truthy_safety_configuration() -> None:
    """循环上限与重复保护不得接受 bool 或其他含糊真值。"""

    provider = ScriptedDecisionProvider(
        [ModelAction(kind="final_answer", final_answer="ok")]
    )
    with pytest.raises(ValueError, match="正整数"):
        AgentRuntime(provider, ToolExecutor(build_registry()), max_steps=True)
    with pytest.raises(TypeError, match="duplicate_guard"):
        AgentRuntime(
            provider,
            ToolExecutor(build_registry()),
            duplicate_guard=1,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"answer": None},
        {"stop_reason": StopReason.TOOL_FAILED, "answer": None},
        {"steps": 3},
        {"model_calls": []},
        {"successful_tool_results": []},
    ],
)
def test_run_result_rejects_cross_artifact_contradictions(changes) -> None:
    result = AgentRuntime(
        ScriptedDecisionProvider(
            [
                action_for(),
                ModelAction(kind="final_answer", final_answer="90"),
            ]
        ),
        ToolExecutor(build_registry()),
    ).run("calculate")
    payload = result.model_dump()
    payload.update(changes)
    with pytest.raises(ValidationError):
        RunResult.model_validate(payload)


def test_core_protocol_models_reject_unknown_fields() -> None:
    payload = action_for().tool_call.model_dump()
    payload["unexpected"] = "would otherwise be dropped"
    with pytest.raises(ValidationError):
        ToolCall.model_validate(payload)
