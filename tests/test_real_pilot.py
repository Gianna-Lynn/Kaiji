"""离线验证真实 Pilot 的请求、费用、异常和完整性闸门。"""

from pathlib import Path

import pytest

from kaiji.eval_runner import AgentEvalCase
from kaiji.experiment import load_agent_cases
from kaiji.formal_runner import PersistentRequestLedger
from kaiji.models import (
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ModelUsage,
    ProviderErrorCode,
    ProviderFailure,
)
from kaiji.models import StopReason
from kaiji.real_pilot import (
    BEHAVIOR_SYSTEM_PROMPT,
    GuardedProvider,
    PilotGuard,
    _normalized_case,
    build_behavior_registry,
)


class SequenceProvider:
    """按顺序返回模型调用结果并记录实际触达次数。"""

    def __init__(self, results: list[ModelCallResult]) -> None:
        self.results = list(results)
        self.calls = 0

    def decide(self, messages):
        del messages
        self.calls += 1
        return self.results.pop(0)


def _success(*, input_tokens: int = 10, output_tokens: int = 5):
    return ModelCallResult(
        action=ModelAction(kind="final_answer", final_answer="ok"),
        metadata=ModelCallMetadata(
            model="fake",
            latency_ms=0,
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        ),
    )


def _abnormal():
    return ModelCallResult(
        failure=ProviderFailure(
            code=ProviderErrorCode.INVALID_RESPONSE,
            message="非法协议",
            retryable=False,
        ),
        metadata=ModelCallMetadata(model="fake", latency_ms=0),
    )


def _transport_failure():
    return ModelCallResult(
        failure=ProviderFailure(
            code=ProviderErrorCode.TRANSPORT_ERROR,
            message="网络传输失败",
            retryable=True,
        ),
        metadata=ModelCallMetadata(model="fake", latency_ms=12),
    )


def test_request_limit_never_reaches_delegate_after_hard_cap() -> None:
    delegate = SequenceProvider([_success()])
    guard = PilotGuard(request_limit=1, cost_limit_usd=1)
    provider = GuardedProvider(delegate, guard)

    assert provider.decide([]).action is not None
    exhausted = provider.decide([])

    assert exhausted.failure.code == ProviderErrorCode.REQUEST_BUDGET_EXHAUSTED
    assert delegate.calls == guard.requests_used == 1
    assert guard.stop_reason == "request_limit"


def test_cost_limit_closes_before_next_request() -> None:
    delegate = SequenceProvider([_success(input_tokens=1_000_000)])
    guard = PilotGuard(request_limit=2, cost_limit_usd=0.02)
    provider = GuardedProvider(delegate, guard)

    provider.decide([])
    exhausted = provider.decide([])

    assert guard.estimated_cost_usd >= 0.02
    assert guard.stop_reason == "cost_limit"
    assert exhausted.failure.code == ProviderErrorCode.REQUEST_BUDGET_EXHAUSTED
    assert delegate.calls == 1


def test_two_consecutive_protocol_failures_close_guard() -> None:
    delegate = SequenceProvider([_abnormal(), _abnormal()])
    guard = PilotGuard(request_limit=3, cost_limit_usd=1, consecutive_abnormal_limit=2)
    provider = GuardedProvider(delegate, guard)

    provider.decide([])
    provider.decide([])
    exhausted = provider.decide([])

    assert guard.stop_reason == "consecutive_abnormal"
    assert exhausted.failure.code == ProviderErrorCode.REQUEST_BUDGET_EXHAUSTED
    assert delegate.calls == 2


def test_network_failure_retries_once_and_preserves_first_attempt() -> None:
    """网络恢复不能隐藏第一次失败，且两次真实尝试都必须消耗请求预算。"""

    delegate = SequenceProvider([_transport_failure(), _success()])
    guard = PilotGuard(request_limit=2, cost_limit_usd=1)
    result = GuardedProvider(delegate, guard).decide([])

    assert result.action is not None
    assert delegate.calls == guard.requests_used == 2
    assert len(result.prior_attempt_failures) == 1
    assert (
        result.prior_attempt_failures[0].failure.code
        == ProviderErrorCode.TRANSPORT_ERROR
    )
    assert result.prior_attempt_failures[0].latency_ms == 12


def test_guarded_provider_persists_each_attempt_in_shared_ledger(tmp_path) -> None:
    """真实 Provider 的调用闸门必须实际驱动 Formal 请求账本。"""

    delegate = SequenceProvider([_success(input_tokens=10, output_tokens=5)])
    guard = PilotGuard(request_limit=2, cost_limit_usd=1)
    ledger = PersistentRequestLedger(tmp_path / "request-ledger.json")

    result = GuardedProvider(
        delegate,
        guard,
        attempt_recorder=ledger,
    ).decide([])

    assert result.action is not None
    assert ledger.state.requests_started == ledger.state.responses_finished == 1
    assert ledger.state.input_tokens == 10
    assert ledger.state.output_tokens == 5


def test_network_retry_cannot_cross_request_budget() -> None:
    """第一次网络失败后若预算耗尽，不得以重试名义触达下游。"""

    delegate = SequenceProvider([_transport_failure()])
    guard = PilotGuard(request_limit=1, cost_limit_usd=1)
    result = GuardedProvider(delegate, guard).decide([])

    assert result.failure is not None
    assert result.failure.code == ProviderErrorCode.REQUEST_BUDGET_EXHAUSTED
    assert delegate.calls == guard.requests_used == 1
    assert len(result.prior_attempt_failures) == 1


@pytest.mark.parametrize("limit", [True, 0, -1])
def test_pilot_guard_rejects_invalid_request_limit(limit) -> None:
    with pytest.raises(ValueError, match="正整数"):
        PilotGuard(request_limit=limit, cost_limit_usd=0.02)


def test_no_tool_pilot_does_not_invent_smoke_marker_requirement() -> None:
    """行为评测的自然问候不得被窄化为协议连通性的固定标记。"""

    case = AgentEvalCase(
        case_id="greeting",
        semantic_group="no-tool",
        suite="behavior",
        prompt="直接问候",
        scenario="no_tool",
        expected_stop_reason=StopReason.COMPLETED,
        expects_no_tool=True,
        required_answer_facts=["无需调用工具"],
    )

    assert _normalized_case(case).required_answer_facts == []


@pytest.mark.parametrize("limit", [True, 0, -1, float("inf"), float("nan")])
def test_pilot_guard_requires_finite_positive_cost_limit(limit) -> None:
    """费用安全闸门不得接受 bool、非正数或非有限数。"""

    with pytest.raises(ValueError, match="有限正数"):
        PilotGuard(request_limit=40, cost_limit_usd=limit)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requests_used", True),
        ("requests_used", -1),
        ("input_tokens", "1"),
        ("output_tokens", -1),
        ("consecutive_abnormal", False),
        ("input_cost_per_million", float("nan")),
        ("output_cost_per_million", True),
    ],
)
def test_pilot_guard_rejects_invalid_restored_accounting(field, value) -> None:
    values = {"request_limit": 40, "cost_limit_usd": 0.02, field: value}
    with pytest.raises(ValueError):
        PilotGuard(**values)


def test_behavior_pilot_uses_one_prompt_and_complete_tool_set() -> None:
    """真实 Behavior Pilot 不得按 case 泄露预期工具或隐藏竞争工具。"""

    assert "calculate_discount" not in BEHAVIOR_SYSTEM_PROMPT
    assert "search_azusa" not in BEHAVIOR_SYSTEM_PROMPT
    assert {item.name for item in build_behavior_registry().definitions()} == {
        "calculate_discount",
        "local_search",
        "get_profile",
        "search_azusa",
        "get_chunk_by_id",
    }


def test_real_behavior_suite_has_five_clear_intent_groups() -> None:
    """真实行为题不得混入要求模型故意失控的 Scripted 故障场景。"""

    root = Path(__file__).resolve().parents[1]
    cases = load_agent_cases(root / "eval" / "real_behavior_cases.jsonl")

    assert len(cases) == 20
    assert {case.scenario for case in cases} == {
        "no_tool",
        "discount",
        "local_search",
        "profile",
        "citation",
    }
    assert all(
        case.scenario not in {"duplicate", "max_steps", "invalid_arguments"}
        for case in cases
    )
    assert all(case.suite == "behavior" for case in cases)
