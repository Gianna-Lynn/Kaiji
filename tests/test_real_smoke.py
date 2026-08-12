"""离线验证真实 Smoke 的六条契约和共享请求硬预算。"""

from pathlib import Path

import pytest

from kaiji.eval_runner import AgentEvalCase
from kaiji.experiment import load_agent_cases
from kaiji.models import (
    Message,
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ProviderErrorCode,
)
from kaiji.real_smoke import BudgetedProvider, RequestBudget


class CountingProvider:
    """记录是否真正进入下游 Provider。"""

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, messages: list[Message]) -> ModelCallResult:
        del messages
        self.calls += 1
        return ModelCallResult(
            action=ModelAction(kind="final_answer", final_answer="ok"),
            metadata=ModelCallMetadata(model="fake", latency_ms=0),
        )


def test_budget_never_calls_delegate_after_limit() -> None:
    delegate = CountingProvider()
    budget = RequestBudget(limit=1)
    provider = BudgetedProvider(delegate, budget)

    first = provider.decide([Message(role="user", content="第一次")])
    second = provider.decide([Message(role="user", content="第二次")])

    assert first.action is not None
    assert second.failure.code == ProviderErrorCode.REQUEST_BUDGET_EXHAUSTED
    assert delegate.calls == budget.used == 1


def test_real_smoke_suite_has_exact_required_six_cases() -> None:
    root = Path(__file__).resolve().parents[1]
    cases = load_agent_cases(root / "eval" / "real_smoke_cases.jsonl")

    assert len(cases) == 6
    assert {case.scenario for case in cases} == {
        "no_tool",
        "single_tool",
        "backfill",
        "invalid_arguments",
        "transient",
        "duplicate",
    }
    assert all(isinstance(case, AgentEvalCase) for case in cases)


@pytest.mark.parametrize("limit", [True, 0, -1])
def test_request_budget_requires_real_positive_integer(limit) -> None:
    """真实调用上限不得接受 bool、零或负数。"""

    with pytest.raises(ValueError, match="正整数"):
        RequestBudget(limit=limit)
