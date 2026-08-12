"""使用固定分母和成功证据确定性评分 Agent Episode。"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from kaiji.eval_runner import (
    AgentEpisode,
    AgentEvalCase,
)
from kaiji.models import ToolResult


class AgentScore(BaseModel):
    """保存单条 Episode 的逐项判定，不能由 Runner 自报成功。"""

    run_id: str
    case_id: str
    task_success: bool
    stop_correct: bool
    tool_selection_correct: bool
    arguments_correct: bool
    answer_facts_correct: bool
    citation_precision: float
    citation_recall: float
    citation_supported: bool
    retry_recovered: bool
    steps: int
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    failure_categories: list[str] = Field(default_factory=list)


class AgentAggregateReport(BaseModel):
    """按完整 Episode 集合聚合行为、引用、成本代理和延迟指标。"""

    episode_count: int
    task_success_rate: float
    tool_selection_accuracy: float
    argument_accuracy: float
    no_tool_accuracy: float
    retry_recovery_rate: float
    citation_precision: float
    citation_recall: float
    average_steps: float
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    average_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    stop_reason_distribution: dict[str, int]
    failure_distribution: dict[str, int]


def _contains_subset(actual: Any, expected: Any) -> bool:
    """递归判断实际 JSON 是否包含期望子集，列表按完整值比较。"""

    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_subset(actual[key], value)
            for key, value in expected.items()
        )
    return actual == expected


def _extract_citations(answer: str | None) -> list[str]:
    """提取 ``Citations:`` 行和方括号引用，保持首次出现顺序。"""

    if not answer:
        return []
    candidates: list[str] = []
    for match in re.finditer(r"(?im)^Citations?\s*:\s*(.+)$", answer):
        candidates.extend(re.findall(r"[\w.-]+", match.group(1)))
    candidates.extend(re.findall(r"\[([\w.-]+)\]", answer))
    return list(dict.fromkeys(candidates))


def _evidence_ids(results: list[ToolResult]) -> set[str]:
    """只从已知证据外壳收集 chunk_id，不信任 metadata 同名字段。"""

    found: set[str] = set()
    for result in results:
        if not result.ok or result.output is None:
            continue
        output = result.output
        direct_id = output.get("chunk_id")
        if isinstance(direct_id, str) and direct_id:
            found.add(direct_id)
        hits = output.get("hits")
        if isinstance(hits, list):
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                chunk_id = hit.get("chunk_id")
                if isinstance(chunk_id, str) and chunk_id:
                    found.add(chunk_id)
    return found


def _token_totals(episode: AgentEpisode) -> tuple[int, int, int]:
    """把 Provider 未报告的 Token 按零计入，不缩小聚合分母。"""

    input_tokens = sum(
        call.metadata.usage.input_tokens or 0 for call in episode.result.model_calls
    )
    output_tokens = sum(
        call.metadata.usage.output_tokens or 0 for call in episode.result.model_calls
    )
    total_tokens = sum(
        call.metadata.usage.total_tokens or 0 for call in episode.result.model_calls
    )
    return input_tokens, output_tokens, total_tokens


def score_episode(case: AgentEvalCase, episode: AgentEpisode) -> AgentScore:
    """从原始 RunResult 独立计算单条成绩和失败类别。"""
    # TODO 14b（L2）：只从原始 RunResult、case 期望与成功证据计算成绩。
    # 参数使用 JSON 子集语义；引用必须同时属于实际引用、允许集合和成功证据；
    # 不接受 Runner 自报成功。逐项失败类别和 Token 也必须保留。
    raise NotImplementedError("TODO 14b：确定性评分单条 Episode")


def aggregate_scores(
    cases: list[AgentEvalCase],
    episodes: list[AgentEpisode],
) -> AgentAggregateReport:
    """要求每条 Episode 都有已知 case，并按全部 Episode 固定分母聚合。"""

    # TODO 14c（L2）：先验证 case/run 身份、suite 与完整覆盖，再调用 14b。
    # 工具、无工具、重试和引用使用各自适用分母；延迟使用 nearest-rank；
    # 缺失 Episode、未知 case、重复 run_id 都必须拒绝，不能缩小分母。
    raise NotImplementedError("TODO 14c：固定分母聚合完整 Episode 集")
