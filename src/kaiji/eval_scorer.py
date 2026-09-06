"""使用固定分母和成功证据确定性评分 Agent Episode。"""

from __future__ import annotations

import re
import unicodedata
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
    citation_precision: float                         # 实际引用中受支持引用的比例
    citation_recall: float                            # 成功证据被引用的覆盖比例
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


@dataclass(frozen=True)
class _CitationEvaluation:
    """保存单条引用评分及聚合所需计数，不进入公开报告格式。"""

    precision: float
    recall: float
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

    precision = (
        len(supported) / len(extracted)
        if extracted
        else (1.0 if not allowed else 0.0)
    )
    recall = len(supported) / len(allowed) if allowed else 1.0
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

    if not episode.result.answer:
        answer_facts_correct = False
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
    """要求身份完整一致，并按各指标真正适用的 Episode 聚合。"""

    # TODO 14c（L2）：
    # 1. 拒绝重复 case_id、重复 run_id、未知 case_id、suite 不一致，以及没有
    #    任何 Episode 覆盖的 Case；不能通过丢弃数据缩小报告分母。
    # 2. 每条 Episode 只调用一次 _score_episode_with_citations()。它同时返回
    #    AgentScore 与 _CitationEvaluation，后者已经保存 extracted、allowed 和
    #    supported 三个计数；聚合阶段不得再次解析回答或工具证据。
    # 3. task success 使用全部 Episode；工具选择和参数、无工具、重试恢复分别
    #    使用要求该能力的 Episode。没有适用样本时，该项聚合率记为 0.0。
    # 4. Citation Precision 使用全局 supported / extracted，Recall 使用全局
    #    supported / allowed；分母为零时聚合值记为 0.0，不能平均单条比例。
    # 5. 汇总 Steps、Token、平均延迟、停止原因与失败类别；P50/P95 使用
    #    nearest-rank：ceil(p * N) - 1，并在排序后的延迟列表中取值。
    raise NotImplementedError("TODO 14c：聚合完整 Episode 集")
