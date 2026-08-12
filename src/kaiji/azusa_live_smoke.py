"""运行 DeepSeek、Kaiji Runtime 与真实 Azusa HTTP 网关的 Live Pilot。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from kaiji.adapters.azusa import AzusaHttpAdapter
from kaiji.azusa_tools import build_azusa_registry
from kaiji.eval_runner import AgentEvalCase, AgentEvalRunner
from kaiji.experiment import ExperimentConfig, ExperimentRunner, load_agent_cases
from kaiji.harnesses import HarnessVariant
from kaiji.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from kaiji.real_smoke import (
    BASE_URL,
    MODEL,
    BudgetedProvider,
    RequestBudget,
)
from kaiji.tools import ToolExecutor


def _system_prompt(case: AgentEvalCase) -> str:
    """生成只含公开 fixture 契约的 case 专用指令。"""

    citation = case.allowed_citation_ids[0]
    query = case.expected_arguments["query"]
    return (
        "你正在执行真实 Azusa HTTP 闭环测试。第一回合必须且只能调用 "
        f"search_azusa，参数 query={query!r}、k=1、mode='bm25'。收到工具结果后"
        "不得再调用工具，只依据该成功结果用中文回答。回答必须包含证据中的核心"
        f"事实，并另起一行严格写 Citations: {citation}。每回合最多一个工具调用。"
    )


def run_live_pilot(
    cases_path: Path,
    output_dir: Path,
    *,
    gateway_url: str,
    request_limit: int,
    api_key_env: str,
    case_id: str | None = None,
) -> tuple[object, RequestBudget]:
    """运行完整 Pilot 或指定单条 case，并共享真实请求硬预算。"""

    if not os.environ.get(api_key_env):
        raise RuntimeError("指定的 API Key 环境变量未设置")
    cases = load_agent_cases(cases_path)
    if case_id is not None:
        cases = [case for case in cases if case.case_id == case_id]
        if len(cases) != 1:
            raise ValueError(f"找不到唯一 Live case: {case_id}")
    elif len(cases) != 5:
        raise ValueError("完整 Live Pilot 必须恰好包含五条 case")
    budget = RequestBudget(limit=request_limit)

    def provider_factory(case: AgentEvalCase):
        registry = build_azusa_registry(AzusaHttpAdapter(gateway_url))
        return BudgetedProvider(
            OpenAICompatibleProvider(
                OpenAICompatibleConfig(
                    base_url=BASE_URL,
                    model=MODEL,
                    api_key_env=api_key_env,
                    timeout_seconds=30,
                    temperature=0,
                    system_prompt=_system_prompt(case),
                    thinking="disabled",
                    max_tokens=512,
                ),
                registry,
            ),
            budget,
        )

    def executor_factory(case: AgentEvalCase):
        del case
        return ToolExecutor(
            build_azusa_registry(AzusaHttpAdapter(gateway_url, timeout_seconds=10))
        )

    config = ExperimentConfig(
        provider="deepseek+azusa-live",
        harness=HarnessVariant.FULL_KAIJI,
        suite="azusa_live_pilot",
        repeats=1,
        model=MODEL,
        temperature=0,
        input_cost_per_million=0.14,
        output_cost_per_million=0.28,
    )
    report = ExperimentRunner(
        config, AgentEvalRunner(provider_factory, executor_factory)
    ).run(cases, output_dir)
    return report, budget


def main() -> None:
    """执行 Live Pilot 并输出不含回答正文和密钥的摘要。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-url", default="http://127.0.0.1:3010")
    parser.add_argument("--request-limit", type=int, default=9)
    parser.add_argument("--api-key-env", required=True)
    parser.add_argument("--case-id")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/azusa-live-pilot")
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    report, budget = run_live_pilot(
        root / "eval" / "azusa_live_cases.jsonl",
        args.output_dir,
        gateway_url=args.gateway_url,
        request_limit=args.request_limit,
        api_key_env=args.api_key_env,
        case_id=args.case_id,
    )
    print(
        {
            "experiment_id": report.experiment_id,
            "complete": report.complete,
            "episodes": report.actual_episode_count,
            "task_success_rate": report.aggregate.task_success_rate,
            "citation_precision": report.aggregate.citation_precision,
            "citation_recall": report.aggregate.citation_recall,
            "bad_case_count": len(report.bad_case_run_ids),
            "requests_used": budget.used,
            "request_limit": budget.limit,
            "total_tokens": report.aggregate.total_tokens,
            "estimated_cost_usd": report.estimated_cost_usd,
        }
    )


if __name__ == "__main__":
    main()
