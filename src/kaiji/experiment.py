"""运行可复现的离线 Reliability Eval 并保存原始与聚合报告。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import numbers
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from kaiji.adapters.azusa import AzusaHttpAdapter, ChunkResponse, MockAzusaAdapter
from kaiji.azusa_tools import build_azusa_registry
from kaiji.demo import ScriptedDecisionProvider, build_registry
from kaiji.eval_runner import AgentEvalCase, AgentEvalRunner
from kaiji.eval_scorer import AgentAggregateReport
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
    repeats: StrictInt = Field(default=1, ge=1)
    model: str = "scripted"
    temperature: float = 0.0
    max_steps: StrictInt = Field(default=4, ge=1)
    input_cost_per_million: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    output_cost_per_million: float = Field(default=0.0, ge=0, allow_inf_nan=False)

    @field_validator("repeats", "max_steps", mode="before")
    @classmethod
    def reject_bool_integer_boundaries(cls, value: object) -> object:
        """矩阵和运行安全边界不得接受 bool 或数字字符串。"""

        if type(value) is bool:
            raise ValueError("Experiment 整数配置不得为 bool")
        return value

    @field_validator(
        "temperature",
        "input_cost_per_million",
        "output_cost_per_million",
        mode="before",
    )
    @classmethod
    def require_finite_numeric_boundaries(cls, value: object) -> object:
        """成本与温度必须是有限的真实数值，拒绝 bool 和字符串强制转换。"""

        if type(value) is bool or not isinstance(value, numbers.Real):
            raise ValueError("Experiment 数值配置必须是有限数值且不得为 bool")
        if not math.isfinite(float(value)):
            raise ValueError("Experiment 数值配置必须是有限数值")
        return value


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

    @model_validator(mode="after")
    def require_complete_formal_report(self) -> "ExperimentReport":
        """普通 Experiment 只有完整成功矩阵才产出正式报告。"""

        if not self.complete:
            raise ValueError("普通 Experiment 失败不产出 incomplete 正式报告")
        if self.actual_episode_count != self.expected_episode_count:
            raise ValueError("正式 Experiment 报告的 Episode 数量必须完整")
        return self


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
        # TODO 15b（L3）：执行 case×repeat 完整矩阵，拒绝空数据、数量不全和
        # 重复 run_id；独立评分后计算数据/config hash、成本、Bad Case 与场景
        # 分布，最后写入不可覆盖的新实验目录。部分结果不得标为 complete。
        raise NotImplementedError("TODO 15b：实现可复现实验矩阵与报告")

    @staticmethod
    def _write(output_dir, report, episodes, scores) -> None:
        """写入新实验目录，已有目录不覆盖。"""
        # TODO 15b（L3）：落盘 episodes.jsonl、report.json、report.md 与
        # bad_cases.json。目录必须新建且不可覆盖；Bad Case 同时保留原始 Episode
        # 与确定性 Score，Markdown 不得夸大为公开 Benchmark。
        raise NotImplementedError("TODO 15b：写入完整且可审计的实验产物")


def run_ablation(
    cases: list[AgentEvalCase],
    *,
    suite: str,
    repeats: int,
    output_dir: Path,
) -> AblationReport:
    """在相同 case 与 Scripted 模型动作下运行四种 Harness。"""

    # TODO 15c（L3）：四种 Harness 必须使用同一 case、模型动作、温度、
    # 最大步骤和重复次数，只切换 retry/duplicate guard。校验 dataset hash 一致，
    # 写入 JSON 与包含成功率、恢复率、Steps、延迟的 Markdown 对照表。
    raise NotImplementedError("TODO 15c：运行受控 Harness Ablation")


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
