"""运行可复现的离线 Reliability Eval 并保存原始与聚合报告。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from kaiji.adapters.azusa import AzusaHttpAdapter, ChunkResponse, MockAzusaAdapter
from kaiji.azusa_tools import build_azusa_registry
from kaiji.demo import ScriptedDecisionProvider, build_registry
from kaiji.eval_runner import AgentEvalCase, AgentEvalRunner
from kaiji.eval_scorer import AgentAggregateReport, aggregate_scores, score_episode
from kaiji.faults import (
    HttpFaultStep,
    ScriptedHttpTransport,
    ScriptedToolHandler,
    ToolFaultStep,
)
from kaiji.harnesses import HarnessVariant
from kaiji.models import ModelAction, ToolCall
from kaiji.tools import ToolDefinition, ToolExecutor, ToolRegistry
from kaiji.demo import DiscountArgs, DiscountResult


class ExperimentConfig(BaseModel):
    """定义一次实验的 Provider、Harness、Suite、重复数和成本口径。"""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="scripted", min_length=1)
    harness: HarnessVariant = HarnessVariant.FULL_KAIJI
    suite: str = Field(min_length=1)
    repeats: int = Field(default=1, ge=1)
    model: str = "scripted"
    temperature: float = 0.0
    max_steps: int = Field(default=4, ge=1)
    input_cost_per_million: float = Field(default=0.0, ge=0)
    output_cost_per_million: float = Field(default=0.0, ge=0)


class ExperimentReport(BaseModel):
    """保存可验证完整性的实验配置、数据指纹和聚合结果。"""

    experiment_id: str
    config: ExperimentConfig
    config_fingerprint: str
    dataset_hash: str
    expected_episode_count: int
    actual_episode_count: int
    complete: bool
    estimated_cost_usd: float
    aggregate: AgentAggregateReport
    bad_case_run_ids: list[str]
    scenario_distribution: dict[str, int]


class AblationReport(BaseModel):
    """汇总同数据、同模型配置下四种 Harness 的受控结果。"""

    dataset_hash: str
    suite: str
    reports: dict[HarnessVariant, ExperimentReport]


def load_agent_cases(path: Path) -> list[AgentEvalCase]:
    """加载 Agent JSONL，并拒绝重复 case ID。"""

    cases = [
        AgentEvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Agent Case 的 case_id 必须唯一")
    return cases


def _fingerprint(value: Any) -> str:
    """对规范化 JSON 计算稳定 SHA-256。"""

    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _scripted_actions(case: AgentEvalCase) -> list[ModelAction]:
    """按场景生成可复现模型动作，供离线验证 Runner 与 Harness。"""

    if case.scenario == "no_tool":
        answer = "；".join(case.required_answer_facts) or "无需调用工具。"
        return [ModelAction(kind="final_answer", final_answer=answer)]
    if case.scenario == "local_search":
        return [
            ModelAction(
                kind="tool_call",
                tool_call=ToolCall(
                    call_id=f"{case.case_id}-local",
                    name="local_search",
                    arguments=case.expected_arguments,
                ),
            ),
            ModelAction(
                kind="final_answer",
                final_answer="；".join(case.required_answer_facts),
            ),
        ]
    if case.scenario == "profile":
        return [
            ModelAction(
                kind="tool_call",
                tool_call=ToolCall(
                    call_id=f"{case.case_id}-profile",
                    name="get_profile",
                    arguments=case.expected_arguments,
                ),
            ),
            ModelAction(
                kind="final_answer",
                final_answer="；".join(case.required_answer_facts),
            ),
        ]
    if case.scenario in {"duplicate", "max_steps"}:
        actions = []
        for index in range(case.max_steps):
            percent = 10 if case.scenario == "duplicate" else index + 1
            actions.append(
                ModelAction(
                    kind="tool_call",
                    tool_call=ToolCall(
                        call_id=f"{case.case_id}-call-{index}",
                        name="calculate_discount",
                        arguments={"price": 100, "percent": percent},
                    ),
                )
            )
        return actions
    if case.scenario in {
        "citation",
        "empty_evidence",
        "http_429",
        "http_503",
        "network",
        "invalid_json",
    }:
        answer_fact = "；".join(case.required_answer_facts) or "Kaiji 使用工具。"
        citation = (
            case.allowed_citation_ids[0] if case.allowed_citation_ids else "kaiji-001"
        )
        return [
            ModelAction(
                kind="tool_call",
                tool_call=ToolCall(
                    call_id=f"{case.case_id}-search",
                    name="search_azusa",
                    arguments=case.expected_arguments
                    or {"query": "Kaiji", "k": 1, "mode": "hybrid"},
                ),
            ),
            ModelAction(
                kind="final_answer",
                final_answer=f"{answer_fact}\nCitations: {citation}",
            ),
        ]
    if case.scenario == "chunk_mismatch":
        return [
            ModelAction(
                kind="tool_call",
                tool_call=ToolCall(
                    call_id=f"{case.case_id}-chunk",
                    name="get_chunk_by_id",
                    arguments={"chunk_id": "expected-id"},
                ),
            )
        ]
    arguments = case.expected_arguments or {"price": 100, "percent": 10}
    if case.scenario == "invalid_arguments":
        arguments = {"price": -1, "percent": 200}
    return [
        ModelAction(
            kind="tool_call",
            tool_call=ToolCall(
                call_id=f"{case.case_id}-discount",
                name="calculate_discount",
                arguments=arguments,
            ),
        ),
        ModelAction(
            kind="final_answer",
            final_answer="；".join(case.required_answer_facts) or "工具执行完成。",
        ),
    ]


def _scripted_executor(case: AgentEvalCase, variant: HarnessVariant) -> ToolExecutor:
    """按场景构建全新 Registry，保证故障序列不跨 Episode 共享。"""

    if case.scenario in {"citation", "empty_evidence"}:
        chunks = (
            []
            if case.scenario == "empty_evidence"
            else [
                ChunkResponse(
                    chunk_id="kaiji-001",
                    text="Kaiji 使用工具。",
                    source="mock://kaiji",
                )
            ]
        )
        return ToolExecutor(
            build_azusa_registry(MockAzusaAdapter(chunks)),
            retry_enabled=variant.retry_enabled,
        )
    if case.scenario in {
        "http_429",
        "http_503",
        "network",
        "invalid_json",
        "chunk_mismatch",
    }:
        success = HttpFaultStep(
            kind="json",
            payload={
                "hits": [{"chunk_id": "kaiji-001", "text": "Kaiji 使用工具。"}],
                "mode": "hybrid",
            },
        )
        sequences = {
            "http_429": [
                HttpFaultStep(kind="status", status_code=429, payload={}),
                success,
            ],
            "http_503": [
                HttpFaultStep(kind="status", status_code=503, payload={}),
                success,
            ],
            "network": [
                HttpFaultStep(kind="network"),
                HttpFaultStep(kind="network"),
            ],
            "invalid_json": [
                HttpFaultStep(kind="invalid_json"),
                HttpFaultStep(kind="invalid_json"),
            ],
            "chunk_mismatch": [
                HttpFaultStep(
                    kind="json",
                    payload={"chunk_id": "wrong-id", "text": "错误证据"},
                ),
                HttpFaultStep(
                    kind="json",
                    payload={"chunk_id": "wrong-id", "text": "错误证据"},
                ),
            ],
        }
        adapter = AzusaHttpAdapter(
            "https://azusa.invalid",
            transport=ScriptedHttpTransport(sequences[case.scenario]).transport,
        )
        return ToolExecutor(
            build_azusa_registry(adapter), retry_enabled=variant.retry_enabled
        )
    if case.scenario in {
        "transient",
        "persistent",
        "permanent",
        "schema_drift",
        "timeout",
        "failed_evidence",
    }:
        sequences = {
            "transient": [
                ToolFaultStep(kind="transient"),
                ToolFaultStep(kind="success", output={"final_price": 90}),
            ],
            "persistent": [
                ToolFaultStep(kind="transient"),
                ToolFaultStep(kind="transient"),
            ],
            "permanent": [ToolFaultStep(kind="permanent")],
            "schema_drift": [ToolFaultStep(kind="invalid_result")],
            "timeout": [
                ToolFaultStep(kind="delay", delay_seconds=0.03),
                ToolFaultStep(kind="delay", delay_seconds=0.03),
            ],
            "failed_evidence": [ToolFaultStep(kind="permanent")],
        }
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="calculate_discount",
                description="故障注入折扣工具",
                arguments_model=DiscountArgs,
                result_model=DiscountResult,
                handler=ScriptedToolHandler(sequences[case.scenario]),
                timeout_seconds=(0.001 if case.scenario == "timeout" else 2.0),
                max_retries=1,
            )
        )
    else:
        registry = build_registry()
    return ToolExecutor(registry, retry_enabled=variant.retry_enabled)


def build_scripted_runner(variant: HarnessVariant) -> AgentEvalRunner:
    """构造离线 Scripted Provider 的受控 Harness。"""

    return AgentEvalRunner(
        lambda case: ScriptedDecisionProvider(_scripted_actions(case)),
        lambda case: _scripted_executor(case, variant),
        duplicate_guard=variant.duplicate_guard,
    )


class ExperimentRunner:
    """执行完整 Episode 矩阵，并拒绝部分结果伪装成完整报告。"""

    def __init__(self, config: ExperimentConfig, runner: AgentEvalRunner) -> None:
        self._config = config
        self._runner = runner

    def run(self, cases: list[AgentEvalCase], output_dir: Path) -> ExperimentReport:
        """运行、评分并写入 JSONL、JSON、Markdown 和 Bad Case。"""

        if not cases:
            raise ValueError("实验至少需要一条 case")
        experiment_id = uuid4().hex
        episodes = [
            self._runner.run(case, repeat_index=repeat)
            for repeat in range(self._config.repeats)
            for case in cases
        ]
        expected = len(cases) * self._config.repeats
        if len(episodes) != expected:
            raise RuntimeError("Episode 数量不完整，拒绝生成报告")
        run_ids = [episode.run_id for episode in episodes]
        if len(run_ids) != len(set(run_ids)):
            raise RuntimeError("出现重复 run_id，拒绝生成报告")

        aggregate = aggregate_scores(cases, episodes)
        scores = {
            item.run_id: score_episode(
                next(case for case in cases if case.case_id == item.case_id), item
            )
            for item in episodes
        }
        bad_ids = [run_id for run_id, score in scores.items() if not score.task_success]
        dataset_hash = _fingerprint([case.model_dump(mode="json") for case in cases])
        config_fingerprint = _fingerprint(self._config.model_dump(mode="json"))
        cost = (
            aggregate.total_input_tokens * self._config.input_cost_per_million
            + aggregate.total_output_tokens * self._config.output_cost_per_million
        ) / 1_000_000
        report = ExperimentReport(
            experiment_id=experiment_id,
            config=self._config,
            config_fingerprint=config_fingerprint,
            dataset_hash=dataset_hash,
            expected_episode_count=expected,
            actual_episode_count=len(episodes),
            complete=True,
            estimated_cost_usd=cost,
            aggregate=aggregate,
            bad_case_run_ids=bad_ids,
            scenario_distribution=dict(Counter(case.scenario for case in cases)),
        )
        self._write(output_dir, report, episodes, scores)
        return report

    @staticmethod
    def _write(output_dir, report, episodes, scores) -> None:
        """写入新实验目录，已有目录不覆盖。"""

        target = output_dir / report.experiment_id
        target.mkdir(parents=True, exist_ok=False)
        (target / "episodes.jsonl").write_text(
            "".join(item.model_dump_json() + "\n" for item in episodes),
            encoding="utf-8",
        )
        (target / "report.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
        bad = [
            {
                "episode": item.model_dump(mode="json"),
                "score": scores[item.run_id].model_dump(mode="json"),
            }
            for item in episodes
            if item.run_id in report.bad_case_run_ids
        ]
        (target / "bad_cases.json").write_text(
            json.dumps(bad, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        aggregate = report.aggregate
        markdown = (
            f"# Kaiji Reliability Eval\n\n"
            f"- 实验 ID：`{report.experiment_id}`\n"
            f"- 完整：`{report.complete}`\n"
            f"- Episode：{report.actual_episode_count}/{report.expected_episode_count}\n"
            f"- Task Success：{aggregate.task_success_rate:.3f}\n"
            f"- Tool Selection：{aggregate.tool_selection_accuracy:.3f}\n"
            f"- Citation Precision / Recall：{aggregate.citation_precision:.3f} / {aggregate.citation_recall:.3f}\n"
            f"- Token：{aggregate.total_tokens}\n"
            f"- 估算成本（USD）：{report.estimated_cost_usd:.6f}\n"
        )
        (target / "report.md").write_text(markdown, encoding="utf-8")


def run_ablation(
    cases: list[AgentEvalCase],
    *,
    suite: str,
    repeats: int,
    output_dir: Path,
) -> AblationReport:
    """在相同 case 与 Scripted 模型动作下运行四种 Harness。"""

    reports = {}
    for variant in HarnessVariant:
        config = ExperimentConfig(
            provider="scripted",
            model="scripted",
            temperature=0,
            harness=variant,
            suite=suite,
            repeats=repeats,
        )
        reports[variant] = ExperimentRunner(config, build_scripted_runner(variant)).run(
            cases, output_dir / variant.value
        )
    dataset_hashes = {report.dataset_hash for report in reports.values()}
    if len(dataset_hashes) != 1:
        raise RuntimeError("Ablation 各 Harness 的数据集 hash 不一致")
    result = AblationReport(
        dataset_hash=dataset_hashes.pop(), suite=suite, reports=reports
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ablation.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    lines = [
        "# Harness Ablation",
        "",
        "| Harness | Task Success | Retry Recovery | Avg Steps | Avg Latency ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for variant, report in reports.items():
        aggregate = report.aggregate
        lines.append(
            f"| {variant.value} | {aggregate.task_success_rate:.3f} | "
            f"{aggregate.retry_recovery_rate:.3f} | {aggregate.average_steps:.3f} | "
            f"{aggregate.average_latency_ms:.3f} |"
        )
    (output_dir / "ablation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> None:
    """提供默认不联网、不消费额度的 Scripted 实验入口。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["scripted"], default="scripted")
    parser.add_argument(
        "--suite",
        choices=["smoke", "behavior", "paraphrase", "reliability", "formal"],
        required=True,
    )
    parser.add_argument(
        "--harness",
        choices=[item.value for item in HarnessVariant],
        default=HarnessVariant.FULL_KAIJI.value,
    )
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/eval"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.suite == "formal":
        cases = load_agent_cases(root / "eval" / "agent_cases.jsonl")
        cases += load_agent_cases(root / "eval" / "fault_cases.jsonl")
    else:
        path = (
            root
            / "eval"
            / (
                "fault_cases.jsonl"
                if args.suite == "reliability"
                else "agent_cases.jsonl"
            )
        )
        cases = load_agent_cases(path)
    if args.suite == "smoke":
        cases = [case for case in cases if case.suite == "behavior"][:6]
    elif args.suite != "formal":
        cases = [case for case in cases if case.suite == args.suite]
    config = ExperimentConfig(
        provider=args.provider,
        harness=HarnessVariant(args.harness),
        suite=args.suite,
        repeats=args.repeats,
    )
    if args.ablation:
        report = run_ablation(
            cases,
            suite=args.suite,
            repeats=args.repeats,
            output_dir=args.output_dir,
        )
    else:
        report = ExperimentRunner(config, build_scripted_runner(config.harness)).run(
            cases, args.output_dir
        )
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
