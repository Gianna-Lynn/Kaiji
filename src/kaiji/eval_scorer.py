"""使用固定分母和成功证据确定性评分 Agent Episode。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from time import perf_counter

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
    # TODO 14b（L2）：只从原始 RunResult(Episode里保存的原始运行结果)、
    # case 期望与成功证据计算成绩。
    # 参数使用 JSON 子集语义；
    # 事实使用 _contains_answer_fact 的确定性字面量规则；
    # 引用必须同时属于实际引用、允许集合和成功证据；不接受 Runner 自报成功。
    # 逐项失败类别和 Token 也必须保留。
    # retry_recovered 只认 Executor的 ToolResult.attempts>1；TODO11 query rewrite 有独立状态字段，Provider
    # transport retry 不是此业务指标。
    if case.case_id != episode.case_id:
        raise ValueError("把错误 Case 和 Episode 配对评分，不得继续比较")

    tool_call_first = first_tool_call(episode)


    allowed_set = set(case.allowed_citation_ids)                          # 允许引用集合：用例case规定可以引用的证据 ID。
    extracted_set = set(_extract_citations(episode.result.answer))        # 实际引用集合：从模型的最终回答中提取出的证据 ID。
    success_set = _evidence_ids(episode.result.successful_tool_results)   # 成功证据集合：Runtime中工具成功返回, 确实存在的证据 ID。

    # 受支持引用集合：同时被 Agent 引用、被用例允许、且由工具成功返回的证据 ID。
    #                模型引用了某个ID, 而且这个ID是题目允许的, 并且工具真的成功返回过.
    supported_set = allowed_set & extracted_set & success_set

    # citation_percision: 受支持引用/实际引用, supported / extracted. (惩罚胡乱引用/越权引用/没有证据的引用)
    # citation_recall: 受支持引用/允许引用. supported / allowed (惩罚漏引用)
    # citation_supported: 模型写出的每一个引用, 是否都同时属于允许集合和成功证据集合? extracted 是否是 allowed ∩ success 的子集?
    
    # | extracted | allowed | Precision | Recall |
    # |---|---|---|---|
    # | 空 | 空 | 1.0 | 1.0 |   不要求引用, 模型也没有进行任何引用.
    # | 空 | 非空 | 0.0 | 0.0 |     要求引用, 但模型完全漏掉.
    # | 非空 | 空 | 0.0 | 1.0 |    不要求引用, 但模型违规引用.  allowed为空时, 不存在召回目标, 本项目约定记为1.0. 违规引用由percision和citation_supported识别.
    # | 非空 | 非空 | supported / extracted | supported / allowed |
    
    if not extracted_set and not allowed_set:
        citation_precision = 1.0
        citation_recall = 1.0
    elif not extracted_set and allowed_set:
        citation_precision = 0.0
        citation_recall = 0.0
    elif extracted_set and not allowed_set:
        citation_precision = 0.0
        citation_recall = 1.0
    else: 
        citation_precision = len(supported_set) / len(extracted_set)
        citation_recall = len(supported_set) / len(allowed_set)

    if not extracted_set:
        # 没有实际引用时, 只有当题目也不要求引用的时候, 才算满足citation_supported.
        citation_supported = not allowed_set
    else:
        # 具有实际引用时, 每个实际引用都必须来自允许集合和成功证据.
        citation_supported = extracted_set.issubset(allowed_set & success_set)

    stop_correct = case.expected_stop_reason == episode.result.stop_reason

    if case.expects_no_tool:
        # case不期待有工具.
        # 如果没有工具, 算作正确.
        if tool_call_first is None:
            tool_selection_correct = True
            arguments_correct = True
        else:
            tool_selection_correct = False
            arguments_correct = False
    else:
        # case期待有工具.
        # 如果有工具:
        if tool_call_first is not None:
            tool_selection_correct = tool_call_first.name == case.expected_tool
            if tool_selection_correct is True:
                arguments_correct = _contains_subset(tool_call_first.arguments, case.expected_arguments)
            else:
                arguments_correct = False
        # 如果没有工具: 应当算选择失败和参数失败.
        else:
            tool_selection_correct = False
            arguments_correct = False

    if not episode.result.answer:
        answer_facts_correct = False
    else:
        answer_facts_correct= all(
            _contains_answer_fact(episode.result.answer, required_answer_fact)
            for required_answer_fact in case.required_answer_facts
        )

    # retry_recovered 只认 Executor的 ToolResult.attempts>1；   # 普通Case不要求重试恢复时, 当前结果会是: retry_recovered = False, failure_categories包含retry_recovery
    retry_recovered = True
    if case.expects_retry_recovery:
        retry_recovered =  (
            episode.result.stop_reason == StopReason.COMPLETED
                and any (
                    result.attempts > 1
                    for result in episode.result.successful_tool_results
                )
        )

    task_success = (
        stop_correct and
        tool_selection_correct and 
        arguments_correct and 
        answer_facts_correct and
        citation_supported and
        retry_recovered
    )

    failure_categories = [
        category
        for category, passed in(
            ("stop", stop_correct),
            ("tool_selection", tool_selection_correct),
            ("arguments", arguments_correct),
            ("answer_facts", answer_facts_correct),
            ("citation", citation_supported),
            ("retry_recovery", retry_recovered),
        )
        if not passed
    ]

    input_tokens, output_tokens, total_tokens = _token_totals(episode)

    return AgentScore(
        run_id=episode.run_id,
        case_id=episode.case_id,
        task_success= task_success,
        stop_correct= stop_correct,
        tool_selection_correct= tool_selection_correct,
        arguments_correct= arguments_correct,
        answer_facts_correct= answer_facts_correct,
        citation_precision= citation_precision,
        citation_recall=citation_recall,
        citation_supported=citation_supported,
        retry_recovered=retry_recovered,
        steps=episode.result.steps,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms= episode.latency_ms,
        failure_categories=failure_categories,
    )

def aggregate_scores(
    cases: list[AgentEvalCase],
    episodes: list[AgentEpisode],
) -> AgentAggregateReport:
    """要求每条 Episode 都有已知 case，并按全部 Episode 固定分母聚合。"""

    # TODO 14c（L2）：先验证 case/run 身份、suite 与完整覆盖，再调用 14b。
    # 工具、无工具、重试和引用使用各自适用分母；延迟使用 nearest-rank；
    # 缺失 Episode、未知 case、重复 run_id 都必须拒绝，不能缩小分母。
    raise NotImplementedError("TODO 14c：固定分母聚合完整 Episode 集")
