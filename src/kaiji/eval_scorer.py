"""使用固定分母和成功证据确定性评分 Agent Episode。"""

from __future__ import annotations

import re
from collections import Counter
import math
from typing import Any
import unicodedata

from pydantic import BaseModel, Field

from kaiji.eval_runner import (
    AgentEpisode,
    AgentEvalCase,
    first_tool_call,
    tool_messages,
)
from kaiji.models import StopReason, ToolResult


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


def _normalize_fact_text(value: str) -> str:
    """按固定 Unicode 规则规范化事实文本，不进行语义改写。"""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _contains_answer_fact(answer: str, fact: str) -> bool:
    """判断回答是否包含事实字面量，并避免字母数字片段误匹配。

    事实匹配是 NFKC、大小写折叠和空白折叠后的连续字面量匹配；若事实两端
    是 ASCII 字母、数字或下划线，则相邻同类 ASCII 字符不会被当作边界。
    CJK 字符不参与这个西文标识边界，因此 ``Kaiji使用工具`` 可以匹配
    ``使用工具``。该规则能拒绝 ``90`` 匹配 ``190``，但不尝试自然语言蕴含、
    否定识别、同义词或数值等价（例如 ``90`` 与 ``90.0``）判断。
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

    answer = episode.result.answer or ""
    answer_facts_correct = all(
        _contains_answer_fact(answer, fact) for fact in case.required_answer_facts
    )
    cited = set(_extract_citations(episode.result.answer))
    allowed = set(case.allowed_citation_ids)
    evidence = _evidence_ids(episode.result.successful_tool_results)
    supported = cited & evidence & allowed
    citation_precision = (
        len(supported) / len(cited) if cited else (1.0 if not allowed else 0.0)
    )
    citation_recall = len(supported) / len(allowed) if allowed else 1.0
    citation_supported = (
        cited <= evidence and cited <= allowed and (bool(cited) or not allowed)
    )
    stop_correct = episode.result.stop_reason == case.expected_stop_reason
    # TODO14 的原生 RunResult 只携带 ToolResult.attempts，因此这里只统计
    # Executor 在一次工具调用内的有界重试。TODO11 LangGraph 的 query rewrite
    # 由 retry_count/step_count 单独记录，不应凭“多条消息”在这里猜测；Provider
    # 的 transport retry 同样属于请求可靠性，不属于业务重试恢复指标。
    retry_recovered = episode.result.stop_reason == StopReason.COMPLETED and any(
        message.tool_result is not None and message.tool_result.attempts > 1
        for message in tool_messages(episode)
    )
    if not case.expects_retry_recovery:
        retry_recovered = True

    task_success = all(
        (
            stop_correct,
            tool_selection_correct,
            arguments_correct,
            answer_facts_correct,
            citation_supported,
            retry_recovered,
        )
    )
    failures = []
    for name, passed in (
        ("stop", stop_correct),
        ("tool_selection", tool_selection_correct),
        ("arguments", arguments_correct),
        ("answer_facts", answer_facts_correct),
        ("citation", citation_supported),
        ("retry_recovery", retry_recovered),
    ):
        if not passed:
            failures.append(name)
    input_tokens, output_tokens, total_tokens = _token_totals(episode)
    return AgentScore(
        run_id=episode.run_id,
        case_id=case.case_id,
        task_success=task_success,
        stop_correct=stop_correct,
        tool_selection_correct=tool_selection_correct,
        arguments_correct=arguments_correct,
        answer_facts_correct=answer_facts_correct,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        citation_supported=citation_supported,
        retry_recovered=retry_recovered,
        steps=episode.result.steps,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=episode.latency_ms,
        failure_categories=failures,
    )


def aggregate_scores(
    cases: list[AgentEvalCase],
    episodes: list[AgentEpisode],
) -> AgentAggregateReport:
    """要求每条 Episode 都有已知 case，并按全部 Episode 固定分母聚合。"""

    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("Agent Case 的 case_id 必须唯一")
    run_ids = [episode.run_id for episode in episodes]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("Episode 的 run_id 必须唯一")
    unknown = {episode.case_id for episode in episodes} - set(case_by_id)
    if unknown:
        raise ValueError(f"Episode 包含未知 case_id: {sorted(unknown)}")
    mismatched_suites = {
        episode.case_id
        for episode in episodes
        if episode.suite != case_by_id[episode.case_id].suite
    }
    if mismatched_suites:
        raise ValueError(f"Episode suite 与 case 不一致: {sorted(mismatched_suites)}")
    covered = {episode.case_id for episode in episodes}
    missing = set(case_by_id) - covered
    if missing:
        raise ValueError(f"报告缺少 case: {sorted(missing)}")

    scores = [score_episode(case_by_id[item.case_id], item) for item in episodes]
    count = len(scores)

    def rate(values: list[bool]) -> float:
        """按完整 Episode 数计算全局固定分母比率。"""

        return sum(values) / count if count else 0.0

    failures = Counter(
        category for score in scores for category in score.failure_categories
    )
    ordered_latency = sorted(score.latency_ms for score in scores)
    scored_episodes = list(zip(episodes, scores, strict=True))
    tool_scores = [
        score
        for episode, score in zip(episodes, scores, strict=True)
        if case_by_id[episode.case_id].expected_tool is not None
    ]
    no_tool_scores = [
        score
        for episode, score in zip(episodes, scores, strict=True)
        if case_by_id[episode.case_id].expects_no_tool
    ]
    retry_scores = [
        score
        for episode, score in zip(episodes, scores, strict=True)
        if case_by_id[episode.case_id].expects_retry_recovery
    ]
    cited_count = 0
    allowed_count = 0
    supported_count = 0
    for episode, _score in scored_episodes:
        case = case_by_id[episode.case_id]
        cited = set(_extract_citations(episode.result.answer))
        allowed = set(case.allowed_citation_ids)
        evidence = _evidence_ids(episode.result.successful_tool_results)
        cited_count += len(cited)
        allowed_count += len(allowed)
        supported_count += len(cited & allowed & evidence)

    def percentile(value: float) -> float:
        """使用 nearest-rank 计算固定 Episode 集上的延迟分位数。"""

        if not ordered_latency:
            return 0.0
        index = max(0, math.ceil(value * len(ordered_latency)) - 1)
        return ordered_latency[index]

    return AgentAggregateReport(
        episode_count=count,
        task_success_rate=rate([score.task_success for score in scores]),
        tool_selection_accuracy=(
            sum(score.tool_selection_correct for score in tool_scores)
            / len(tool_scores)
            if tool_scores
            else 0.0
        ),
        argument_accuracy=(
            sum(score.arguments_correct for score in tool_scores) / len(tool_scores)
            if tool_scores
            else 0.0
        ),
        no_tool_accuracy=(
            sum(score.tool_selection_correct for score in no_tool_scores)
            / len(no_tool_scores)
            if no_tool_scores
            else 0.0
        ),
        retry_recovery_rate=(
            sum(score.retry_recovered for score in retry_scores) / len(retry_scores)
            if retry_scores
            else 0.0
        ),
        citation_precision=(supported_count / cited_count if cited_count else 0.0),
        citation_recall=(supported_count / allowed_count if allowed_count else 0.0),
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
            Counter(item.result.stop_reason.value for item in episodes)
        ),
        failure_distribution=dict(failures),
    )
