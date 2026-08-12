"""验证四种 Harness 只切换重试与重复保护且始终保持步数上限。"""

import pytest

from kaiji.demo import ScriptedDecisionProvider
from kaiji.eval_runner import AgentEvalCase, AgentEvalRunner
from kaiji.faults import ScriptedToolHandler, ToolFaultStep
from kaiji.harnesses import HarnessVariant
from kaiji.models import ModelAction, StopReason, ToolCall
from kaiji.tools import ToolDefinition, ToolExecutor, ToolRegistry
from kaiji.demo import DiscountArgs, DiscountResult


def _case(case_id: str, *, max_steps: int = 3) -> AgentEvalCase:
    return AgentEvalCase(
        case_id=case_id,
        semantic_group="harness",
        suite="reliability",
        prompt="测试",
        expected_stop_reason=StopReason.COMPLETED,
        expected_tool="calculate_discount",
        max_steps=max_steps,
    )


def _runner(variant: HarnessVariant, actions, steps):
    def executor_factory(case):
        handler = ScriptedToolHandler(steps.copy())
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="calculate_discount",
                description="测试",
                arguments_model=DiscountArgs,
                result_model=DiscountResult,
                handler=handler,
                max_retries=1,
            )
        )
        return ToolExecutor(registry, retry_enabled=variant.retry_enabled)

    return AgentEvalRunner(
        lambda case: ScriptedDecisionProvider(actions.copy()),
        executor_factory,
        duplicate_guard=variant.duplicate_guard,
    )


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        (HarnessVariant.BOUNDED_BASELINE, StopReason.TOOL_FAILED),
        (HarnessVariant.NO_RETRY, StopReason.TOOL_FAILED),
        (HarnessVariant.NO_DUPLICATE_GUARD, StopReason.COMPLETED),
        (HarnessVariant.FULL_KAIJI, StopReason.COMPLETED),
    ],
)
def test_retry_ablation(variant, expected) -> None:
    actions = [
        ModelAction(
            kind="tool_call",
            tool_call=ToolCall(
                call_id="call-1",
                name="calculate_discount",
                arguments={"price": 100, "percent": 10},
            ),
        ),
        ModelAction(kind="final_answer", final_answer="90"),
    ]
    episode = _runner(
        variant,
        actions,
        [
            ToolFaultStep(kind="transient"),
            ToolFaultStep(kind="success", output={"final_price": 90}),
        ],
    ).run(_case(variant.value))
    assert episode.result.stop_reason == expected


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        (HarnessVariant.BOUNDED_BASELINE, StopReason.MAX_STEPS),
        (HarnessVariant.NO_RETRY, StopReason.DUPLICATE_CALL),
        (HarnessVariant.NO_DUPLICATE_GUARD, StopReason.MAX_STEPS),
        (HarnessVariant.FULL_KAIJI, StopReason.DUPLICATE_CALL),
    ],
)
def test_duplicate_guard_ablation_remains_bounded(variant, expected) -> None:
    repeated = ModelAction(
        kind="tool_call",
        tool_call=ToolCall(
            call_id="different-id",
            name="calculate_discount",
            arguments={"price": 100, "percent": 10},
        ),
    )
    episode = _runner(
        variant,
        [
            repeated,
            repeated.model_copy(
                update={
                    "tool_call": repeated.tool_call.model_copy(
                        update={"call_id": "other-id"}
                    )
                }
            ),
            repeated.model_copy(
                update={
                    "tool_call": repeated.tool_call.model_copy(
                        update={"call_id": "third-id"}
                    )
                }
            ),
        ],
        [ToolFaultStep(kind="success", output={"final_price": 90})] * 3,
    ).run(_case(variant.value))
    assert episode.result.stop_reason == expected
    assert episode.result.steps <= 3
