"""运行 20 条 DeepSeek 真实模型 Pilot，并执行请求、费用和异常三重闸门。"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from kaiji.eval_runner import AgentEpisode, AgentEvalCase, AgentEvalRunner
from kaiji.eval_scorer import AgentAggregateReport, aggregate_scores, score_episode
from kaiji.experiment import load_agent_cases
from kaiji.models import (
    Message,
    ModelCallMetadata,
    ModelCallResult,
    ProviderErrorCode,
    ProviderAttemptFailure,
    ProviderFailure,
)
from kaiji.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from kaiji.adapters.azusa import ChunkResponse, MockAzusaAdapter
from kaiji.azusa_tools import build_azusa_registry
from kaiji.demo import build_registry
from kaiji.real_smoke import BASE_URL, MODEL
from kaiji.runtime import DecisionProvider
from kaiji.tools import ToolExecutor, ToolRegistry


INPUT_COST_PER_MILLION = 0.14
OUTPUT_COST_PER_MILLION = 0.28
ABNORMAL_CODES = {
    ProviderErrorCode.INVALID_JSON,
    ProviderErrorCode.INVALID_RESPONSE,
    ProviderErrorCode.HTTP_ERROR,
}
RETRYABLE_NETWORK_CODES = {
    ProviderErrorCode.TIMEOUT,
    ProviderErrorCode.TRANSPORT_ERROR,
    ProviderErrorCode.RATE_LIMITED,
    ProviderErrorCode.SERVER_ERROR,
}


@dataclass
class PilotGuard:
    """跨 Episode 追踪请求、已报告 Token、估算费用与连续异常。"""

    request_limit: int
    cost_limit_usd: float
    consecutive_abnormal_limit: int = 2
    input_cost_per_million: float = INPUT_COST_PER_MILLION
    output_cost_per_million: float = OUTPUT_COST_PER_MILLION
    requests_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    consecutive_abnormal: int = 0
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        """严格拒绝 bool、非正上限和非法费用边界。"""

        if type(self.request_limit) is not int or self.request_limit < 1:
            raise ValueError("请求上限必须是真正的正整数")
        if (
            isinstance(self.cost_limit_usd, bool)
            or not isinstance(self.cost_limit_usd, (int, float))
            or not math.isfinite(self.cost_limit_usd)
            or self.cost_limit_usd <= 0
        ):
            raise ValueError("费用上限必须是有限正数")
        if (
            type(self.consecutive_abnormal_limit) is not int
            or self.consecutive_abnormal_limit < 1
        ):
            raise ValueError("连续异常上限必须是真正的正整数")
        for label, value in (
            ("requests_used", self.requests_used),
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("consecutive_abnormal", self.consecutive_abnormal),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{label} 必须是真正的非负整数")
        for label, value in (
            ("input_cost_per_million", self.input_cost_per_million),
            ("output_cost_per_million", self.output_cost_per_million),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{label} 必须是有限非负数")

    @property
    def estimated_cost_usd(self) -> float:
        """按缓存未命中的保守输入价格计算已报告 Token 费用。"""

        return (
            self.input_tokens * self.input_cost_per_million
            + self.output_tokens * self.output_cost_per_million
        ) / 1_000_000

    def allow_request(self) -> bool:
        """在发送前检查硬请求上限、软费用与连续异常闸门。"""

        if self.stop_reason is not None:
            return False
        if self.requests_used >= self.request_limit:
            self.stop_reason = "request_limit"
            return False
        if self.estimated_cost_usd >= self.cost_limit_usd:
            self.stop_reason = "cost_limit"
            return False
        if self.consecutive_abnormal >= self.consecutive_abnormal_limit:
            self.stop_reason = "consecutive_abnormal"
            return False
        self.requests_used += 1
        return True

    def observe(self, result: ModelCallResult) -> None:
        """在响应后累计 Token，并更新连续异常计数。"""

        usage = result.metadata.usage
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        if result.failure is not None and result.failure.code in ABNORMAL_CODES:
            self.consecutive_abnormal += 1
        else:
            self.consecutive_abnormal = 0
        if self.estimated_cost_usd >= self.cost_limit_usd:
            self.stop_reason = "cost_limit"
        elif self.consecutive_abnormal >= self.consecutive_abnormal_limit:
            self.stop_reason = "consecutive_abnormal"


class AttemptRecorder(Protocol):
    """定义真实请求发送前后可持久化的最小账本接口。"""

    def record_started(self) -> None: ...

    def record_finished(self, result: ModelCallResult) -> None: ...


class GuardedProvider:
    """应用共享闸门，并对网络类失败执行至多一次可审计重试。"""

    def __init__(
        self,
        delegate: DecisionProvider,
        guard: PilotGuard,
        *,
        attempt_recorder: AttemptRecorder | None = None,
    ) -> None:
        self._delegate = delegate
        self._guard = guard
        self._attempt_recorder = attempt_recorder

    def decide(self, messages: list[Message]) -> ModelCallResult:
        """每次真实尝试都计入预算；第一次网络失败被无损保存在最终结果中。"""

        prior_failures: list[ProviderAttemptFailure] = []
        for attempt in range(2):
            if not self._guard.allow_request():
                return ModelCallResult(
                    failure=ProviderFailure(
                        code=ProviderErrorCode.REQUEST_BUDGET_EXHAUSTED,
                        message="真实 Pilot 闸门已关闭",
                        retryable=False,
                    ),
                    metadata=ModelCallMetadata(model=MODEL, latency_ms=0),
                    prior_attempt_failures=prior_failures,
                )
            if self._attempt_recorder is not None:
                self._attempt_recorder.record_started()
            result = self._delegate.decide(messages)
            self._guard.observe(result)
            if self._attempt_recorder is not None:
                self._attempt_recorder.record_finished(result)
            if (
                attempt == 0
                and result.failure is not None
                and result.failure.retryable
                and result.failure.code in RETRYABLE_NETWORK_CODES
            ):
                prior_failures.append(
                    ProviderAttemptFailure(
                        failure=result.failure,
                        latency_ms=result.metadata.latency_ms,
                        request_id=result.metadata.request_id,
                    )
                )
                continue
            return result.model_copy(update={"prior_attempt_failures": prior_failures})
        raise AssertionError("网络重试循环必须在两次尝试内返回")


class RealPilotReport(BaseModel):
    """明确区分完整与部分 Pilot，避免提前停止被包装成完整成绩。"""

    pilot_id: str
    methodology: Literal["shared_prompt_all_tools"] | None = None
    complete: bool
    expected_episode_count: int
    actual_episode_count: int
    request_limit: int
    requests_used: int
    cost_limit_usd: float
    estimated_cost_usd: float
    input_tokens: int
    output_tokens: int
    retried_request_count: int = 0
    retry_failure_distribution: dict[str, int] = Field(default_factory=dict)
    stop_reason: str | None
    aggregate: AgentAggregateReport | None = None
    bad_case_run_ids: list[str] = Field(default_factory=list)


BEHAVIOR_SYSTEM_PROMPT = (
    "你是 Kaiji 单 Agent。根据用户问题自行判断是否需要工具；不得因为工具存在而"
    "无条件调用。每个模型回合最多调用一个工具。工具成功后只依据已校验结果回答；"
    "若回答使用了带 chunk_id 的检索证据，末行使用 `Citations: <chunk_id>`。"
    "不得伪造工具结果、引用或参数，工具失败时不得把失败内容当作成功证据。"
)


def build_behavior_registry() -> ToolRegistry:
    """为每条 Behavior Case 构造包含相同工具集合的全新 Registry。"""

    registry = ToolRegistry()
    azusa = build_azusa_registry(
        MockAzusaAdapter(
            [
                ChunkResponse(
                    chunk_id="kaiji-001",
                    text="Kaiji 使用类型化工具调用。",
                    source="mock://real-pilot",
                )
            ]
        )
    )
    for source in (build_registry(), azusa):
        for definition in source.definitions():
            registry.register(definition)
    return registry


def _normalized_case(case: AgentEvalCase) -> AgentEvalCase:
    """清理仅属于 Scripted Smoke 的固定回答标记。"""

    if case.scenario == "no_tool":
        # Scripted 数据用固定短语检查替身输出；真实行为只要求模型正确不调用工具。
        return case.model_copy(update={"required_answer_facts": []})
    return case


def rescore_pilot(cases_path: Path, pilot_dir: Path) -> RealPilotReport:
    """使用当前评分契约重评已保存 Episode，不产生任何模型请求。"""

    raw_cases = [
        case for case in load_agent_cases(cases_path) if case.suite == "behavior"
    ]
    cases = [_normalized_case(case) for case in raw_cases]
    episodes = [
        AgentEpisode.model_validate_json(line)
        for line in (pilot_dir / "episodes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    previous = RealPilotReport.model_validate_json(
        (pilot_dir / "report.json").read_text(encoding="utf-8")
    )
    if len(episodes) != len(cases):
        raise ValueError("只有完整 20 条 Pilot 才能离线重评分")
    aggregate = aggregate_scores(cases, episodes)
    scores = {
        episode.run_id: score_episode(
            next(case for case in cases if case.case_id == episode.case_id), episode
        )
        for episode in episodes
    }
    bad_ids = [run_id for run_id, score in scores.items() if not score.task_success]
    report = previous.model_copy(
        update={
            "complete": True,
            "aggregate": aggregate,
            "bad_case_run_ids": bad_ids,
        }
    )
    (pilot_dir / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    (pilot_dir / "bad_cases.json").write_text(
        json.dumps(
            [
                {"run_id": run_id, "score": scores[run_id].model_dump(mode="json")}
                for run_id in bad_ids
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def run_real_pilot(
    cases_path: Path,
    output_dir: Path,
    *,
    api_key_env: str,
    request_limit: int = 40,
    cost_limit_usd: float = 0.02,
) -> RealPilotReport:
    """顺序运行 20 条 Pilot；任何闸门关闭后不再启动新 Episode。"""

    if not os.environ.get(api_key_env):
        raise RuntimeError("指定的 API Key 环境变量未设置")
    raw_cases = [
        case for case in load_agent_cases(cases_path) if case.suite == "behavior"
    ]
    cases = [_normalized_case(case) for case in raw_cases]
    if len(cases) != 20:
        raise ValueError("真实 Pilot 必须恰好包含 20 条 behavior case")
    guard = PilotGuard(request_limit=request_limit, cost_limit_usd=cost_limit_usd)

    def provider_factory(case: AgentEvalCase) -> DecisionProvider:
        del case
        registry = build_behavior_registry()
        delegate = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url=BASE_URL,
                model=MODEL,
                api_key_env=api_key_env,
                timeout_seconds=30,
                temperature=0,
                system_prompt=BEHAVIOR_SYSTEM_PROMPT,
                thinking="disabled",
                max_tokens=512,
            ),
            registry,
        )
        return GuardedProvider(delegate, guard)

    def executor_factory(case: AgentEvalCase) -> ToolExecutor:
        del case
        return ToolExecutor(build_behavior_registry())

    runner = AgentEvalRunner(provider_factory, executor_factory)
    episodes: list[AgentEpisode] = []
    for case in cases:
        if guard.stop_reason is not None:
            break
        episodes.append(runner.run(case))

    complete = len(episodes) == len(cases) and guard.stop_reason is None
    aggregate = aggregate_scores(cases, episodes) if complete else None
    scores = {
        episode.run_id: score_episode(
            next(case for case in cases if case.case_id == episode.case_id), episode
        )
        for episode in episodes
    }
    bad_ids = [run_id for run_id, score in scores.items() if not score.task_success]
    retry_distribution: dict[str, int] = {}
    for episode in episodes:
        for call in episode.result.model_calls:
            for attempt in call.prior_attempt_failures:
                code = attempt.failure.code.value
                retry_distribution[code] = retry_distribution.get(code, 0) + 1
    report = RealPilotReport(
        pilot_id=uuid4().hex,
        methodology="shared_prompt_all_tools",
        complete=complete,
        expected_episode_count=len(cases),
        actual_episode_count=len(episodes),
        request_limit=guard.request_limit,
        requests_used=guard.requests_used,
        cost_limit_usd=guard.cost_limit_usd,
        estimated_cost_usd=guard.estimated_cost_usd,
        input_tokens=guard.input_tokens,
        output_tokens=guard.output_tokens,
        retried_request_count=sum(retry_distribution.values()),
        retry_failure_distribution=retry_distribution,
        stop_reason=guard.stop_reason,
        aggregate=aggregate,
        bad_case_run_ids=bad_ids,
    )
    target = output_dir / report.pilot_id
    target.mkdir(parents=True, exist_ok=False)
    (target / "episodes.jsonl").write_text(
        "".join(episode.model_dump_json() + "\n" for episode in episodes),
        encoding="utf-8",
    )
    (target / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    (target / "bad_cases.json").write_text(
        json.dumps(
            [
                {"run_id": run_id, "score": scores[run_id].model_dump(mode="json")}
                for run_id in bad_ids
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def main() -> None:
    """执行真实 Pilot，并仅输出无回答正文、无凭据的摘要。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key-env")
    parser.add_argument("--request-limit", type=int, default=40)
    parser.add_argument("--cost-limit-usd", type=float, default=0.02)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/real-pilot"))
    parser.add_argument("--rescore-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.rescore_dir is not None:
        report = rescore_pilot(
            root / "eval" / "real_behavior_cases.jsonl", args.rescore_dir
        )
    else:
        if args.api_key_env is None:
            parser.error("联网运行必须提供 --api-key-env")
        report = run_real_pilot(
            root / "eval" / "real_behavior_cases.jsonl",
            args.output_dir,
            api_key_env=args.api_key_env,
            request_limit=args.request_limit,
            cost_limit_usd=args.cost_limit_usd,
        )
    print(
        {
            "pilot_id": report.pilot_id,
            "complete": report.complete,
            "episodes": report.actual_episode_count,
            "requests_used": report.requests_used,
            "estimated_cost_usd": report.estimated_cost_usd,
            "stop_reason": report.stop_reason,
            "task_success_rate": (
                report.aggregate.task_success_rate if report.aggregate else None
            ),
            "bad_case_count": len(report.bad_case_run_ids),
        }
    )


if __name__ == "__main__":
    main()
