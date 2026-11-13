"""使用固定分母和成功证据确定性评分 Agent Episode。"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from kaiji.eval_runner import (
    AgentEpisode,
    AgentEvalCase,
    first_tool_call,
)
from kaiji.models import ToolResult, StopReason


class AgentScore(BaseModel):
    """保存单条 Episode 的逐项判定，不能由 Runner 自报成功。"""

    run_id: str                                      # 单次运行的唯一标识
    case_id: str                                     # 评测用例的唯一标识
    task_success: bool                               # 任务是否完成并成功
    stop_correct: bool                               # 停止时机和停止原因是否正确
    tool_selection_correct: bool                     # 是否选择了期望的工具
    arguments_correct: bool                           # 工具参数是否符合期望
    answer_facts_correct: bool                        # 回答是否包含全部目标事实
    citation_precision: float | None                  # 实际引用中受支持引用的比例；无实际引用时未定义
    citation_recall: float | None                     # 成功证据被引用的覆盖比例；无允许引用时未定义
    citation_supported: bool                          # 回答中出现的所有引用，是否都来自本次运行成功且经过校验的工具证据。
    retry_recovered: bool                             # 重试后是否恢复成功
    steps: int                                        # Episode 执行的步骤数
    input_tokens: int = Field(ge=0)                  # 输入 Token 总数
    output_tokens: int = Field(ge=0)                 # 输出 Token 总数
    total_tokens: int = Field(ge=0)                  # 输入和输出 Token 总数
    latency_ms: float = Field(ge=0)                  # Episode 延迟，单位为毫秒
    failure_categories: list[str] = Field(default_factory=list)  # 失败类别列表


class AgentAggregateReport(BaseModel):
    """按完整 Episode 集合聚合行为、引用、成本代理和延迟指标。"""

    episode_count: int
    task_success_rate: float
    tool_selection_accuracy: float
    argument_accuracy: float
    no_tool_accuracy: float
    retry_recovery_rate: float
    citation_precision: float | None
    citation_recall: float | None
    average_steps: float
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    average_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    stop_reason_distribution: dict[str, int]
    failure_distribution: dict[str, int]


@dataclass(frozen=True)
class _CitationEvaluation:
    """保存单条引用评分及聚合所需计数，不进入公开报告格式。"""

    precision: float | None
    recall: float | None
    supported: bool
    extracted_count: int
    allowed_count: int
    supported_count: int


def _contains_subset(actual: Any, expected: Any) -> bool:
    """递归判断实际 JSON 是否包含期望子集，列表按完整值比较。"""

    if isinstance(expected, dict):
        # 先判断 actual 是字典，再用 all() 检查每个期望键值。
        return isinstance(actual, dict) and all(
            # key in actual 确保键存在，and 短路后递归比较对应值。
            key in actual and _contains_subset(actual[key], value)
            for key, value in expected.items()
        )
    return actual == expected


def _normalize_fact_text(value: str) -> str:
    """按固定 Unicode 规则规范化事实文本，不进行语义改写。"""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _contains_answer_fact(answer: str, fact: str) -> bool:
    """判断回答是否包含事实字面量，并避免字母数字片段误匹配。

    事实匹配是 NFKC、大小写折叠和空白折叠后的连续字面量匹配；若事实两端
    是 ASCII 字母、数字或下划线，则相邻同类 ASCII 字符不会被当作边界。
    CJK 字符不参与这个西文标识边界，因此 ``Kaiji使用工具`` 可以匹配
    ``使用工具``。该规则能拒绝 ``90`` 匹配 ``190``，但不尝试自然语言蕴含、
    否定识别、同义词或数值等价判断。
    """

    normalized_answer = _normalize_fact_text(answer)
    normalized_fact = _normalize_fact_text(fact)
    if not normalized_fact:
        return False

    def is_ascii_word_character(value: str) -> bool:
        return (
            value == "_"
            or "A" <= value <= "Z"
            or "a" <= value <= "z"
            or ("0" <= value <= "9")
        )

    start = 0
    while True:
        position = normalized_answer.find(normalized_fact, start)
        if position < 0:
            return False
        end = position + len(normalized_fact)
        left_ok = position == 0 or not (
            is_ascii_word_character(normalized_fact[0])
            and is_ascii_word_character(normalized_answer[position - 1])
        )
        right_ok = end == len(normalized_answer) or not (
            is_ascii_word_character(normalized_fact[-1])
            and is_ascii_word_character(normalized_answer[end])
        )
        if left_ok and right_ok:
            return True
        start = position + 1


def _extract_citations(answer: str | None) -> list[str]:
    """提取 ``Citations:`` 行和方括号引用，保持首次出现顺序。"""

    if not answer:
        return []
    candidates: list[str] = []
    for match in re.finditer(r"(?im)^Citations?\s*:\s*(.+)$", answer):  # 查找回答中的 Citations 行
        candidates.extend(re.findall(r"[\w.-]+", match.group(1)))       # 提取引用行中的引用标识
    candidates.extend(re.findall(r"\[([\w.-]+)\]", answer))             # 提取回答中方括号包裹的引用
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


def _evaluate_citations(
    case: AgentEvalCase,
    episode: AgentEpisode,
) -> _CitationEvaluation:
    """一次性计算单条引用成绩和聚合所需的充分统计量。"""

    allowed = set(case.allowed_citation_ids)
    extracted = set(_extract_citations(episode.result.answer))
    evidence = _evidence_ids(episode.result.successful_tool_results)
    supported = allowed & extracted & evidence

    precision = len(supported) / len(extracted) if extracted else None
    recall = len(supported) / len(allowed) if allowed else None
    citations_supported = (
        extracted.issubset(allowed & evidence)
        and (bool(extracted) or not allowed)
    )
    return _CitationEvaluation(
        precision=precision,
        recall=recall,
        supported=citations_supported,
        extracted_count=len(extracted),
        allowed_count=len(allowed),
        supported_count=len(supported),
    )


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


def _score_episode_with_citations(
    case: AgentEvalCase,
    episode: AgentEpisode,
) -> tuple[AgentScore, _CitationEvaluation]:
    """评分一次 Episode，并同时返回聚合引用指标所需的计数。"""

    if case.case_id != episode.case_id:
        raise ValueError("case 与 episode 的 case_id 必须一致")

    call = first_tool_call(episode)
    if case.expects_no_tool:
        tool_selection_correct = call is None
        arguments_correct = call is None
    else:
        tool_selection_correct = call is not None and call.name == case.expected_tool
        arguments_correct = (
            call is not None
            and tool_selection_correct
            and _contains_subset(call.arguments, case.expected_arguments)
        )

    if episode.result.answer is None:
        # 与人的主线保持一致：预期失败且未要求回答事实时，不伪造最终回答。
        answer_facts_correct = (
            not case.required_answer_facts
            and case.expected_stop_reason != StopReason.COMPLETED
        )
    else:
        answer_facts_correct = all(
            _contains_answer_fact(episode.result.answer, fact)
            for fact in case.required_answer_facts
        )

    citations = _evaluate_citations(case, episode)
    stop_correct = case.expected_stop_reason == episode.result.stop_reason
    retry_recovered = True
    if case.expects_retry_recovery:
        retry_recovered = (
            episode.result.stop_reason == StopReason.COMPLETED
            and any(
                result.attempts > 1
                for result in episode.result.successful_tool_results
            )
        )

    task_success = all(
        (
            stop_correct,
            tool_selection_correct,
            arguments_correct,
            answer_facts_correct,
            citations.supported,
            retry_recovered,
        )
    )
    failure_categories = [
        category
        for category, passed in (
            ("stop", stop_correct),
            ("tool_selection", tool_selection_correct),
            ("arguments", arguments_correct),
            ("answer_facts", answer_facts_correct),
            ("citation", citations.supported),
            ("retry_recovery", retry_recovered),
        )
        if not passed
    ]
    input_tokens, output_tokens, total_tokens = _token_totals(episode)
    score = AgentScore(
        run_id=episode.run_id,
        case_id=episode.case_id,
        task_success=task_success,
        stop_correct=stop_correct,
        tool_selection_correct=tool_selection_correct,
        arguments_correct=arguments_correct,
        answer_facts_correct=answer_facts_correct,
        citation_precision=citations.precision,
        citation_recall=citations.recall,
        citation_supported=citations.supported,
        retry_recovered=retry_recovered,
        steps=episode.result.steps,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=episode.latency_ms,
        failure_categories=failure_categories,
    )
    return score, citations


def score_episode(case: AgentEvalCase, episode: AgentEpisode) -> AgentScore:
    """从原始 RunResult 独立计算单条成绩和失败类别。"""

    score, _citations = _score_episode_with_citations(case, episode)
    return score


def aggregate_scores(
    cases: list[AgentEvalCase],
    episodes: list[AgentEpisode],
) -> AgentAggregateReport:
    """验证完整 Episode 集合，并按各指标的固定适用分母聚合。"""

    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("Agent Case 的 case_id 必须唯一")

    run_ids = [episode.run_id for episode in episodes]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("Episode 的 run_id 必须唯一")

    unknown_case_ids = {episode.case_id for episode in episodes} - set(case_by_id)
    if unknown_case_ids:
        raise ValueError(f"Episode 包含未知 case_id: {sorted(unknown_case_ids)}")

    mismatched_suites = {
        episode.case_id
        for episode in episodes
        if episode.suite != case_by_id[episode.case_id].suite
    }
    if mismatched_suites:
        raise ValueError(f"Episode suite 与 case 不一致: {sorted(mismatched_suites)}")

    covered_case_ids = {episode.case_id for episode in episodes}
    missing_case_ids = set(case_by_id) - covered_case_ids
    if missing_case_ids:
        raise ValueError(f"报告缺少 case: {sorted(missing_case_ids)}")

    evaluated = [
        (
            episode,
            *_score_episode_with_citations(
                case_by_id[episode.case_id],
                episode,
            ),
        )
        for episode in episodes
    ]
    scores = [score for _episode, score, _citations in evaluated]
    count = len(scores)

    def rate(values: list[bool]) -> float:
        """计算适用样本的通过率；没有适用样本时返回零。"""

        return sum(values) / len(values) if values else 0.0

    tool_scores = [
        score
        for episode, score, _citations in evaluated
        if not case_by_id[episode.case_id].expects_no_tool
    ]
    no_tool_scores = [
        score
        for episode, score, _citations in evaluated
        if case_by_id[episode.case_id].expects_no_tool
    ]
    retry_scores = [
        score
        for episode, score, _citations in evaluated
        if case_by_id[episode.case_id].expects_retry_recovery
    ]

    supported_count = sum(
        citations.supported_count for _episode, _score, citations in evaluated
    )
    extracted_count = sum(
        citations.extracted_count for _episode, _score, citations in evaluated
    )
    allowed_count = sum(
        citations.allowed_count for _episode, _score, citations in evaluated
    )

    ordered_latency = sorted(score.latency_ms for score in scores)

    def percentile(value: float) -> float:
        """使用 nearest-rank 计算固定 Episode 集上的延迟分位数。"""

        if not ordered_latency:
            return 0.0
        index = max(0, math.ceil(value * len(ordered_latency)) - 1)
        return ordered_latency[index]

    failure_distribution = Counter(
        category for score in scores for category in score.failure_categories
    )
    return AgentAggregateReport(
        episode_count=count,
        task_success_rate=rate([score.task_success for score in scores]),
        tool_selection_accuracy=rate(
            [score.tool_selection_correct for score in tool_scores]
        ),
        argument_accuracy=rate([score.arguments_correct for score in tool_scores]),
        no_tool_accuracy=rate(
            [score.tool_selection_correct for score in no_tool_scores]
        ),
        retry_recovery_rate=rate(
            [score.retry_recovered for score in retry_scores]
        ),
        citation_precision=(
            supported_count / extracted_count if extracted_count else None
        ),
        citation_recall=(
            supported_count / allowed_count if allowed_count else None
        ),
        average_steps=(sum(score.steps for score in scores) / count if count else 0.0),
        total_input_tokens=sum(score.input_tokens for score in scores),
        total_output_tokens=sum(score.output_tokens for score in scores),
        total_tokens=sum(score.total_tokens for score in scores),
        average_latency_ms=(
            sum(score.latency_ms for score in scores) / count if count else 0.0
        ),
        p50_latency_ms=percentile(0.50),
        p95_latency_ms=percentile(0.95),
        stop_reason_distribution=dict(
            Counter(episode.result.stop_reason.value for episode in episodes)
        ),
        failure_distribution=dict(failure_distribution),
    )
