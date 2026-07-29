from kaiji.demo import ScriptedDecisionProvider, build_registry
from kaiji.models import ModelAction, StopReason, ToolCall
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolExecutor


def action_for(
    tool_name: str = "calculate_discount", *, percent: int = 10
) -> ModelAction:
    return ModelAction(
        kind="tool_call",
        tool_call=ToolCall(
            call_id="call-1",
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
    provider = ScriptedDecisionProvider([action_for(), action_for(percent=20)])
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
    provider = ScriptedDecisionProvider([action_for(), action_for()])
    runtime = AgentRuntime(provider, ToolExecutor(build_registry()), max_steps=3)

    result = runtime.run("Repeat forever")

    assert result.stop_reason == StopReason.DUPLICATE_CALL
    assert len(result.successful_tool_results) == 1
