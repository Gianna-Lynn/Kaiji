"""验证 Agent Runner 真实执行 Runtime 且隔离每条用例状态。"""

import pytest
from pydantic import ValidationError

from kaiji.demo import ScriptedDecisionProvider, build_registry
from kaiji.eval_runner import AgentEvalCase, AgentEvalRunner
from kaiji.models import ModelAction, StopReason, ToolCall
from kaiji.tools import ToolExecutor


def _case(case_id: str) -> AgentEvalCase:
    return AgentEvalCase(
        case_id=case_id,
        semantic_group="discount",
        suite="behavior",
        prompt="100 元打九折是多少？",
        expected_stop_reason=StopReason.COMPLETED,
        expected_tool="calculate_discount",
        expected_arguments={"price": 100, "percent": 10},
        required_answer_facts=["90"],
    )


def test_todo_14a_runner_executes_runtime_and_preserves_raw_result() -> None:
    """Episode 必须保存 Runtime 的消息、Trace 和模型调用元数据。"""

    def provider_factory(case):
        return ScriptedDecisionProvider(
            [
                ModelAction(
                    kind="tool_call",
                    tool_call=ToolCall(
                        call_id=f"call-{case.case_id}",
                        name="calculate_discount",
                        arguments={"price": 100, "percent": 10},
                    ),
                ),
                ModelAction(kind="final_answer", final_answer="结果是 90。"),
            ]
        )

    runner = AgentEvalRunner(
        provider_factory,
        lambda case: ToolExecutor(build_registry()),
    )

    episode = runner.run(_case("case-1"), repeat_index=2)

    assert episode.case_id == "case-1"
    assert episode.repeat_index == 2
    assert episode.result.stop_reason == StopReason.COMPLETED
    assert episode.result.messages[-2].tool_call.call_id == "call-case-1"
    assert episode.result.trace[-1].event == "stop"
    assert len(episode.result.model_calls) == 2


def test_todo_14a_runner_calls_factories_for_each_case_without_state_leak() -> None:
    """共享 Runner 不得复用已消费 Provider 或有状态 Executor。"""

    created: list[str] = []

    def provider_factory(case):
        created.append(case.case_id)
        return ScriptedDecisionProvider(
            [ModelAction(kind="final_answer", final_answer="结果是 90。")]
        )

    runner = AgentEvalRunner(
        provider_factory,
        lambda case: ToolExecutor(build_registry()),
    )
    first = _case("case-a").model_copy(
        update={
            "expected_tool": None,
            "expected_arguments": {},
            "expects_no_tool": True,
        }
    )
    second = first.model_copy(update={"case_id": "case-b"})

    runner.run(first)
    runner.run(second)

    assert created == ["case-a", "case-b"]


@pytest.mark.parametrize(
    "updates",
    [
        {"case_id": "   "},
        {"expects_no_tool": "false"},
        {"max_steps": True},
        {"expected_tool": None, "expected_arguments": {}},
        {"required_answer_facts": [""]},
        {"required_answer_facts": ["90", "90"]},
        {"expected_arguments": {"value": float("nan")}},
    ],
)
def test_case_contract_rejects_ambiguous_or_unscorable_values(updates) -> None:
    values = _case("strict-case").model_dump()
    values.update(updates)
    with pytest.raises(ValidationError):
        AgentEvalCase.model_validate(values)


@pytest.mark.parametrize("bad", [True, -1, "1"])
def test_todo_14a_runner_repeat_index_is_a_strict_nonnegative_integer(bad) -> None:
    runner = AgentEvalRunner(
        lambda case: ScriptedDecisionProvider(
            [ModelAction(kind="final_answer", final_answer="结果是 90。")]
        ),
        lambda case: ToolExecutor(build_registry()),
    )
    with pytest.raises(ValueError, match="repeat_index"):
        runner.run(_case("strict-repeat"), repeat_index=bad)
