"""运行具有共享请求硬上限的 DeepSeek 真实模型 Smoke。"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from kaiji.adapters.azusa import ChunkResponse, MockAzusaAdapter
from kaiji.azusa_tools import build_azusa_registry
from kaiji.demo import DiscountArgs, DiscountResult, build_registry
from kaiji.eval_runner import AgentEvalCase, AgentEvalRunner
from kaiji.experiment import ExperimentConfig, ExperimentRunner, load_agent_cases
from kaiji.harnesses import HarnessVariant
from kaiji.models import (
    Message,
    ModelCallMetadata,
    ModelCallResult,
    ProviderErrorCode,
    ProviderFailure,
)
from kaiji.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from kaiji.runtime import DecisionProvider
from kaiji.faults import ScriptedToolHandler, ToolFaultStep
from kaiji.tools import ToolDefinition, ToolExecutor, ToolRegistry


BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"
SYSTEM_PROMPTS = {
    "no_tool": ("你正在执行确定性协议测试。不得调用任何工具，只输出 KAIJI_SMOKE_OK。"),
    "single_tool": (
        "你正在执行确定性协议测试。第一回合必须且只能调用 "
        "calculate_discount，参数 price=100、percent=10。收到工具结果后不得再调用"
        "工具，最终回答必须包含 90。"
    ),
    "backfill": (
        "你正在执行确定性协议测试。第一回合必须且只能调用 search_azusa，参数"
        " query='Kaiji'、k=1、mode='hybrid'。收到工具结果后不得再调用工具，"
        "仅依据结果回答，并另起一行写 Citations: kaiji-001。"
    ),
    "invalid_arguments": (
        "你正在执行参数校验测试。必须调用 calculate_discount，且故意严格使用"
        "非法参数 price=-1、percent=200；不要修正参数，不要直接回答。"
    ),
    "transient": (
        "你正在执行重试恢复测试。第一回合必须且只能调用 calculate_discount，"
        "参数 price=100、percent=10。收到工具成功结果后不得再调用工具，最终回答"
        "必须包含 90。工具内部重试由 Runtime 负责。"
    ),
    "duplicate": (
        "你正在执行重复调用保护测试。无论历史中是否已有工具结果，每个回合都"
        "必须调用 calculate_discount，参数始终为 price=100、percent=10；"
        "不得给出最终回答。每回合最多调用一个工具。"
    ),
}


@dataclass
class RequestBudget:
    """在多个 Provider 实例间共享真实 HTTP 请求硬上限。"""

    limit: int
    used: int = 0

    def __post_init__(self) -> None:
        """严格拒绝 bool、非正上限和非法初始计数。"""

        if type(self.limit) is not int or self.limit < 1:
            raise ValueError("请求预算上限必须是真正的正整数")
        if type(self.used) is not int or not 0 <= self.used <= self.limit:
            raise ValueError("已用请求数必须是预算范围内的真正整数")

    def consume(self) -> bool:
        """仅在尚有预算时增加计数并返回真。"""

        if self.used >= self.limit:
            return False
        self.used += 1
        return True


class BudgetedProvider:
    """在每次真实 Provider 调用前原子式消费共享预算。"""

    def __init__(self, delegate: DecisionProvider, budget: RequestBudget) -> None:
        self._delegate = delegate
        self._budget = budget

    def decide(self, messages: list[Message]) -> ModelCallResult:
        """预算耗尽时返回结构化失败，绝不调用下游 Provider。"""

        if not self._budget.consume():
            return ModelCallResult(
                failure=ProviderFailure(
                    code=ProviderErrorCode.REQUEST_BUDGET_EXHAUSTED,
                    message="真实模型请求预算已耗尽",
                    retryable=False,
                ),
                metadata=ModelCallMetadata(model=MODEL, latency_ms=0),
            )
        return self._delegate.decide(messages)


def _provider_factory(budget: RequestBudget, api_key_env: str):
    """为每条 case 创建不同系统指令、共享预算的 DeepSeek Provider。"""

    def create(case: AgentEvalCase) -> DecisionProvider:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url=BASE_URL,
                model=MODEL,
                api_key_env=api_key_env,
                timeout_seconds=30,
                temperature=0,
                system_prompt=SYSTEM_PROMPTS[case.scenario],
                thinking="disabled",
                max_tokens=512,
            ),
            _registry_for(case),
        )
        return BudgetedProvider(provider, budget)

    return create


def _registry_for(case: AgentEvalCase) -> ToolRegistry:
    """按 Smoke 场景创建状态隔离且 Schema 相同的工具注册表。"""

    if case.scenario == "backfill":
        return build_azusa_registry(
            MockAzusaAdapter(
                [
                    ChunkResponse(
                        chunk_id="kaiji-001",
                        text="Kaiji 使用类型化工具调用。",
                        source="mock://real-smoke",
                    )
                ]
            )
        )
    if case.scenario == "transient":
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="calculate_discount",
                description="根据原价和折扣百分比计算最终价格。",
                arguments_model=DiscountArgs,
                result_model=DiscountResult,
                handler=ScriptedToolHandler(
                    [
                        ToolFaultStep(kind="transient"),
                        ToolFaultStep(kind="success", output={"final_price": 90}),
                    ]
                ),
                max_retries=1,
            )
        )
        return registry
    return build_registry()


def run_deepseek_smoke(
    cases_path: Path,
    output_dir: Path,
    *,
    request_limit: int = 19,
    api_key_env: str,
) -> tuple[object, RequestBudget]:
    """运行六条真实 Smoke；默认 19 次加上先前验证请求等于总上限 20。"""

    if not os.environ.get(api_key_env):
        raise RuntimeError("指定的 API Key 环境变量未设置")
    cases = load_agent_cases(cases_path)
    if len(cases) != 6:
        raise ValueError("真实 Smoke 必须恰好包含六条 case")
    budget = RequestBudget(limit=request_limit)
    runner = AgentEvalRunner(
        _provider_factory(budget, api_key_env),
        lambda case: ToolExecutor(_registry_for(case)),
    )
    config = ExperimentConfig(
        provider="deepseek",
        harness=HarnessVariant.FULL_KAIJI,
        suite="real_smoke",
        repeats=1,
        model=MODEL,
        temperature=0,
        input_cost_per_million=0.14,
        output_cost_per_million=0.28,
    )
    report = ExperimentRunner(config, runner).run(cases, output_dir)
    return report, budget


def main() -> None:
    """执行真实 Smoke，并只打印报告摘要和请求计数。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--request-limit", type=int, default=19)
    parser.add_argument("--api-key-env", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/real-smoke"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    report, budget = run_deepseek_smoke(
        root / "eval" / "real_smoke_cases.jsonl",
        args.output_dir,
        request_limit=args.request_limit,
        api_key_env=args.api_key_env,
    )
    print(
        {
            "experiment_id": report.experiment_id,
            "complete": report.complete,
            "episodes": report.actual_episode_count,
            "task_success_rate": report.aggregate.task_success_rate,
            "bad_case_count": len(report.bad_case_run_ids),
            "requests_used": budget.used,
            "request_limit": budget.limit,
            "total_tokens": report.aggregate.total_tokens,
            "estimated_cost_usd": report.estimated_cost_usd,
        }
    )


if __name__ == "__main__":
    main()
