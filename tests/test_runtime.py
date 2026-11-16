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
    """构造一个调用 calculate_discount 的工具动作。

    Args:
        tool_name: 要调用的工具名,默认 calculate_discount。
        percent: 折扣百分比,默认 10。
        call_id: 协议内的调用标识,默认 "call-1"。

    Returns:
        携带固定价格 100 与给定折扣的工具调用动作。
    """

    return ModelAction(
        kind="tool_call",
        tool_call=ToolCall(
            call_id=call_id,
            name=tool_name,
            arguments={"price": 100, "percent": percent},
        ),
    )


def test_runtime_backfills_valid_result_then_finishes() -> None:
    """成功工具结果必须回填消息历史,模型随后给出最终回答即正常完成。"""

    # 预设两轮决策：先计算 100 减免 10% 的价格，再返回写好的最终答案。
    # 此 Provider 忽略消息内容，因此本测试不验证模型是否根据工具结果生成答案。
    provider = ScriptedDecisionProvider(
        [
            action_for(),
            ModelAction(kind="final_answer", final_answer="The price is 90."),
        ]
    )
    runtime = AgentRuntime(provider, ToolExecutor(build_registry()))  # 使用真实工具执行器执行折扣计算。

    result = runtime.run("Calculate the price")

    assert result.stop_reason == StopReason.COMPLETED  # 收到最终答案后，应以正常完成结束。
    assert result.answer == "The price is 90."  # 最终答案应保存在 RunResult.answer 中。
    # 成功结果列表应保留计算输出；Decimal 在 JSON 模式下序列化为字符串 "90"。
    assert result.successful_tool_results[0].output == {"final_price": "90"}
    # 当前实现没有追加最终回答消息，所以最后一条消息仍应是工具结果。
    assert result.messages[-1].role == "tool"
    # 倒数第二条消息应保留预期的工具调用；这里比较模型字段值，不是对象身份。
    assert result.messages[-2].tool_call == action_for().tool_call
    # 工具结果与调用请求的 call_id 必须相同，防止结果被回填到错误的调用上。
    assert (
        result.messages[-1].tool_result.call_id == result.messages[-2].tool_call.call_id
    )
    # 提取事件名称，检查顺序及数量：两轮决策、一次工具执行，最后正常停止。
    assert [event.event for event in result.trace] == [
        "model_action",  # 第 1 轮：Provider 请求调用工具。
        "tool_result",   # 第 1 轮：Executor 返回工具结果。
        "model_action",  # 第 2 轮：Provider 给出最终答案。
        "stop",          # Runtime 记录停止事件。
    ]


def test_runtime_stops_on_tool_failure() -> None:
    """
    模型请求不存在的 ``missing`` 工具时,工具执行以失败结束。
    运行时应立即停止并将停止原因标记为 ``TOOL_FAILED``;由于没有工具成功
    产出,结果不应包含答案,失败的工具结果也不得进入成功证据列表。
    """

    provider = ScriptedDecisionProvider([action_for("missing")])
    runtime = AgentRuntime(provider, ToolExecutor(build_registry()))
    # 触发失败: build_registry() 没有注册名字为"missing"的工具.
    result = runtime.run("Use a missing tool")

    assert result.stop_reason == StopReason.TOOL_FAILED
    assert result.answer is None
    assert result.successful_tool_results == []


def test_runtime_has_hard_maximum_steps() -> None:
    """循环达到 max_steps 上限即停止,即使模型还想继续调用工具。"""

    provider = ScriptedDecisionProvider(
        [action_for(), action_for(percent=20, call_id="call-2")]   
        # Runtime最多做两轮决策,两轮都是调用工具,第二轮的时候循环耗尽
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
    """duplicate_guard=True 时,与已执行调用完全相同(名称+参数)的调用即停止,与 call_id 无关。"""

    provider = ScriptedDecisionProvider([action_for(), action_for(call_id="call-2")])
    # 第二轮动作与第一轮完全相同,但 call_id 不同,仍应被 duplicate_guard 检测到重复。
    runtime = AgentRuntime(provider, ToolExecutor(build_registry()), max_steps=3)

    result = runtime.run("Repeat forever")

    assert result.stop_reason == StopReason.DUPLICATE_CALL
    assert len(result.successful_tool_results) == 1


def test_runtime_rejects_reused_tool_call_id_before_second_execution() -> None:
    """模型复用已用过的 tool_call_id 时,第二次执行发生前即按无效响应停止。"""

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
    # 最后三条事件:第二次动作只产生 model_failure 与 stop,从未真正执行。
    assert [event.event for event in result.trace[-3:]] == [
        "model_action",
        "model_failure",
        "stop",
    ]
    assert result.trace[-2].data["code"] == ProviderErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize("question", ["", "   ", None, 7])
def test_runtime_rejects_blank_or_non_string_question(question) -> None:
    """空白或非字符串问题必须在进入循环前被拒绝。"""

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
    """RunResult 各字段与停止原因互相矛盾时,模型校验必须拒绝。"""

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
    """核心协议模型必须拒绝未声明字段,不得静默丢弃。"""

    payload = action_for().tool_call.model_dump()
    payload["unexpected"] = "would otherwise be dropped"
    with pytest.raises(ValidationError):
        ToolCall.model_validate(payload)
