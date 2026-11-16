"""对照评测 Case 对 Agent Episode 逐项评分并聚合为报告。

评分独立于 Runner 自报，引用证据只认成功且校验过的工具结果；
聚合使用固定分母，不因缺失样本缩小。
"""

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
    """单条 Episode 的逐项评分结果，由 Scorer 独立计算，不采用 Runner 自报。"""

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
    """整个 Episode 集合的聚合报告：行为、引用、Token 成本与延迟指标。"""

    episode_count: int                    # 参与聚合统计的 Episode 总数
    task_success_rate: float              # 任务成功的 Episode 占比
    tool_selection_accuracy: float        # 选择正确工具的 Episode 占比
    argument_accuracy: float              # 工具参数正确的 Episode 占比
    no_tool_accuracy: float               # 无需调用工具时正确不调用的 Episode 占比
    retry_recovery_rate: float            # 通过重试最终恢复成功的 Episode 占比
    citation_precision: float | None      # 实际引用中受支持引用的平均比例
    citation_recall: float | None         # 允许引用中被成功覆盖的平均比例
    average_steps: float                  # 每个 Episode 的平均执行步骤数
    total_input_tokens: int               # 所有 Episode 消耗的输入 Token 总数
    total_output_tokens: int              # 所有 Episode 消耗的输出 Token 总数
    total_tokens: int                     # 所有 Episode 消耗的输入和输出 Token 总数
    average_latency_ms: float             # Episode 平均延迟，单位为毫秒
    p50_latency_ms: float                 # Episode 延迟的中位数，单位为毫秒
    p95_latency_ms: float                 # Episode 延迟的 95 分位数，单位为毫秒
    stop_reason_distribution: dict[str, int]  # 各停止原因对应的 Episode 数量
    failure_distribution: dict[str, int]  # 各失败类别对应的 Episode 数量


@dataclass(frozen=True)     # 实例创建后, 字段不可修改.
class _CitationEvaluation:
    """单条 Episode 的引用评分：单条指标与聚合计数一并保存，不进入公开报告。"""
    precision: float | None  # 引用准确率: 在实际引用中, 有多少引用既被允许、又有成功工具证据支持. precision=supported_count/extracted_count
    recall: float | None     # 引用召回率: 在Case允许的引用中, 有多少被Agent成功引用. recall=supported_count/allowed_count
    supported: bool   # 引用是否合法的整体布尔判断. 表示回答中的所有引用是否都属于“允许引用且有成功证据”的集合。
    extracted_count: int    # Agent实际回答中的引用数量.
    allowed_count: int      # Case允许的引用数量.
    supported_count: int    # Agent实际回答, 并且Case允许, 并且来自成功的工具结果的引用数量.


def _contains_subset(actual: Any, expected: Any) -> bool:
    """递归判断 actual 是否包含 expected 子集：字典逐键递归，其余类型按 == 完整比较。"""

    if isinstance(expected, dict):
        # 先判断 actual 是字典，再用 all() 检查每个期望键值。
        return isinstance(actual, dict) and all(
            # key in actual 确保键存在，and 短路后递归比较对应值。
            key in actual and _contains_subset(actual[key], value)
            for key, value in expected.items()
        )
    return actual == expected


def _normalize_fact_text(value: str) -> str:
    """用 NFKC、大小写折叠与空白折叠规范化事实文本，不做语义改写。"""

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
    """提取 ``Citations:`` 行和方括号引用，去重并保持首次出现顺序。"""

    if not answer:
        return []
    candidates: list[str] = []
    for match in re.finditer(r"(?im)^Citations?\s*:\s*(.+)$", answer):  # 查找回答中的 Citations 行
        candidates.extend(re.findall(r"[\w.-]+", match.group(1)))       # 提取引用行中的引用标识
    candidates.extend(re.findall(r"\[([\w.-]+)\]", answer))             # 提取回答中方括号包裹的引用
    return list(dict.fromkeys(candidates))


def _evidence_ids(results: list[ToolResult]) -> set[str]:
    """只从成功工具结果的外壳字段（顶层 chunk_id 与 hits）收集证据 id，不信任 metadata 同名字段。"""

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
    """计算单条 Episode 的引用成绩与聚合所需计数。

    受支持引用集合 = Case 允许的引用 ID ∩ 回答实际引用的 ID
                    ∩ 本次成功工具结果提供的证据 ID。

    返回字段 supported 为 True，需要同时满足：
    所有实际引用均受支持；若 Case 的允许引用集合非空，回答还必须包含引用。
    若允许引用集合和实际引用集合都为空，该字段也为 True。

    supported_count 为受支持引用集合的大小。
    Precision = 受支持引用数 / 实际引用数。
    Recall = 受支持引用数 / Case 允许引用数。
    引用 ID 均按去重后的集合计数，各指标分母为零时返回 None。
    """

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
    """汇总所有模型调用的 Token 用量；未报告的按 0 计入，不缩小聚合分母。"""

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
    """对照 Case 期望逐项评分一次 Episode，并返回聚合引用指标所需计数。

    依次判定六项：停止原因、工具选择、工具参数、目标事实、引用合法性、
    重试恢复；task_success 要求六项全部通过。case 与 episode 的 case_id
    不一致时抛 ValueError。返回 (AgentScore, _CitationEvaluation)，后者
    携带 extracted / allowed / supported 三个计数供聚合复用。
    """

    # 身份守卫：case 提供"期望"，episode 提供"实际"。二者 case_id 不一致说明
    # 数据拼错了，直接拒绝，避免把 A 用例的期望套到 B 运行上打分。
    if case.case_id != episode.case_id:
        raise ValueError("case 与 episode 的 case_id 必须一致")

    # 2.工具选择
    # 取模型在整个运行中的第一个工具调用。当前评测契约只考核首次工具选择，
    # 后续调用（如补充检索）不影响这两项判定。
    call = first_tool_call(episode)
    if case.expects_no_tool:
        # 负向用例：Case 期望 Agent 完全不碰工具。
        # "选对工具" = 没有任何工具调用；
        # "参数正确" = 参数不存在. 因此需要这两个字段都是None.
        tool_selection_correct = call is None
        arguments_correct = call is None
    else:
        # 正向用例：工具名必须与期望完全一致。
        tool_selection_correct = call is not None and call.name == case.expected_tool
        # 3.参数
        # 参数判定：先要求工具已选对（短路），再检查期望参数是实际参数的
        # 子集——Agent 多传参数不算错，缺参或少字段才算错。
        arguments_correct = (
            call is not None
            and tool_selection_correct
            and _contains_subset(call.arguments, case.expected_arguments)
        )

    # 4.回答正确
    if episode.result.answer is None:
        # 回答为空时的"事实正确"定义：只有 Case 不要求任何目标事实、且预期
        # 本来就不是 COMPLETED 停止（例如预期 MAX_STEPS / 超时失败）时，
        # 空回答才可接受；预期 COMPLETED 的 Case 必须产出有效答案。
        answer_facts_correct = (
            not case.required_answer_facts
            and case.expected_stop_reason != StopReason.COMPLETED
        )
    else:
        # 回答非空：Case 要求的每一个目标事实都必须出现在回答中，
        # 用规范化后的字面量匹配判断。
        answer_facts_correct = all(
            _contains_answer_fact(episode.result.answer, fact)
            for fact in case.required_answer_facts
        )

    # 5.引用
    # 一次性完成引用评分：supported 布尔值用于下面的 task_success 判定，
    # 三个计数随返回值带到聚合阶段，聚合时不再重新解析回答。
    citations = _evaluate_citations(case, episode)

    # 1.停止原因
    # 停止判定是严格相等：实际停止原因必须与 Case 期望完全一致，不认"提前完成"。
    stop_correct = case.expected_stop_reason == episode.result.stop_reason


    # 6.重试
    # 默认恒真：Case 不考核重试恢复时，这一项不参与失败判定，不会拉低 task_success。
    retry_recovered = True
    if case.expects_retry_recovery:
        # 要求"先失败、重试后恢复"：必须最终 COMPLETED，且至少有一个成功
        # 工具结果的 attempts > 1——证明真的经历过失败重试，而不是一次成功。
        retry_recovered = (
            episode.result.stop_reason == StopReason.COMPLETED
            and any(
                result.attempts > 1
                for result in episode.result.successful_tool_results
            )
        )

    # 六个维度全部通过才算任务成功；任何一项失败都会进入 failure_categories。
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
    # 把"失败"翻译成人类可读的类别名，聚合报告据此统计 failure_distribution。
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
    # Token 统计：Provider 未报告的数量按 0 计入，聚合分母不缩小。
    input_tokens, output_tokens, total_tokens = _token_totals(episode)
    # 组装公开成绩模型：逐项判定和 Token / 延迟直接抄录，
    # 引用三项指标来自 citations 对象。
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
    # 同时返回引用计数，供 aggregate_scores 聚合使用；单条评分入口
    # score_episode 只取 score，会丢弃这部分。
    return score, citations


def score_episode(case: AgentEvalCase, episode: AgentEpisode) -> AgentScore:
    """单条评分入口：独立计算成绩，只返回 AgentScore，丢弃引用计数。"""

    score, _citations = _score_episode_with_citations(case, episode)
    return score

def _ratio(bool_list: list[bool]) -> float:
    """返回布尔列表中 True 的占比；空列表记为 0.0，避免除零。"""
    if not bool_list:
        return 0.0
    true_cnt = 0
    length = len(bool_list)
    for item in bool_list:
        if item:
            true_cnt += 1
    return true_cnt / length

def aggregate_scores(
    cases: list[AgentEvalCase],
    episodes: list[AgentEpisode],
) -> AgentAggregateReport:
    """先校验身份完整一致，再按各指标适用的 Episode 子集聚合，分母不缩小。"""

    # TODO 14c（L2）：
    # 1. 拒绝重复 case_id、重复 run_id、未知 case_id、suite 不一致，以及没有
    #    任何 Episode 覆盖的 Case；不能通过丢弃数据缩小报告分母。
    # 2. 每条 Episode 只调用一次 _score_episode_with_citations()。它同时返回
    #    AgentScore 与 _CitationEvaluation，后者已经保存 extracted、allowed 和
    #    supported 三个计数；聚合阶段不得再次解析回答或工具证据。
    # 3. task success 使用全部 Episode；工具选择和参数、无工具、重试恢复分别
    #    使用要求该能力的 Episode。没有适用样本时，该项聚合率记为 0.0。
    # 4. Citation Precision 使用全局 supported / extracted，Recall 使用全局
    #    supported / allowed；分母为零时聚合值记为 None，不能平均单条比例。
    # 5. 汇总 Steps、Token、平均延迟、停止原因与失败类别；P50/P95 使用
    #    nearest-rank：ceil(p * N) - 1，并在排序后的延迟列表中取值。


    # 1. 拒绝重复 case_id、重复 run_id、未知 case_id、suite 不一致，以及没有
    #    任何 Episode 覆盖的 Case；不能通过丢弃数据缩小报告分母。
    id_to_case: dict[str, AgentEvalCase] = {}
    for case in cases:
        if case.case_id in id_to_case:
            raise ValueError("cases列表中出现了重复case_id")
        else:
            id_to_case[case.case_id] = case

    seen_run_ids = set()
    seen_case_ids = set()

    for episode in episodes:
        if episode.run_id in seen_run_ids:
            raise ValueError(f"run_id 必须唯一, 但episodes列表中出现了重复run_id={episode.run_id}")
        else:
            seen_run_ids.add(episode.run_id)
        if episode.case_id not in id_to_case:
            raise ValueError(f"episode对应的case_id={episode.case_id}不存在于cases列表中")
        case = id_to_case[episode.case_id]
        if episode.suite != case.suite:
            raise ValueError(f"episode和对应的case的suite不一致. episode的suite是{episode.suite}, case的suite是{case.suite}")
        seen_case_ids.add(case.case_id)

    for case in cases:
        if case.case_id not in seen_case_ids:
            raise ValueError(f"case_id = {case.case_id} 缺少 case 对应的episode覆盖, 一个episode都没有.")


    # 2. 每条 Episode 只调用一次 _score_episode_with_citations()。它同时返回
    #    AgentScore 与 _CitationEvaluation，后者已经保存 extracted、allowed 和
    #    supported 三个计数；聚合阶段不得再次解析回答或工具证据。
    epi_score_eval_list : list[tuple[AgentEpisode, AgentScore, _CitationEvaluation]] = []
    for episode in episodes:
        score, evaluation = _score_episode_with_citations(
            case=id_to_case[episode.case_id],
            episode=episode,
        )
        epi_score_eval_list.append((episode, score, evaluation))

    # 3. task success 使用全部 Episode；工具选择和参数、无工具、重试恢复分别
    #    使用要求该能力的 Episode。没有适用样本时，该项聚合率记为 0.0。

    # 按适用范围分组
    episode_count = len(episodes)
    all_scores: list[AgentScore] = []
    tool_scores: list[AgentScore] = []
    no_tool_scores: list[AgentScore] = []
    retry_scores: list[AgentScore] = []
    for episode, agentscore, citation_evalation in epi_score_eval_list:
        case=id_to_case[episode.case_id]
        all_scores.append(agentscore)
        if case.expects_no_tool:                # case不要求使用工具
            no_tool_scores.append(agentscore)
        else:                                   # case要求使用工具
            tool_scores.append(agentscore)
        if case.expects_retry_recovery:         # case要求重试
            retry_scores.append(agentscore)

    # 提取布尔字段, 统一交给ratio计算.
    task_success_rate = _ratio([
        score.task_success for score in all_scores
    ])
    tool_selection_accuracy = _ratio([
        score.tool_selection_correct for score in tool_scores
    ])
    argument_accuracy = _ratio([
        score.arguments_correct for score in tool_scores
    ])
    no_tool_accuracy = _ratio([
        score.tool_selection_correct for score in no_tool_scores
    ])
    retry_recovery_rate = _ratio([
        score.retry_recovered for score in retry_scores
    ])

    # 4. Citation Precision 使用全局 supported / extracted，Recall 使用全局
    #    supported / allowed；分母为零时聚合值记为 None，不能平均单条比例。
    supported_total = 0
    extracted_total = 0
    allowed_total = 0


    # 统计全局的所有 supported_count, extracted_count, allowed_count 之和.
    for episode, agentscore, citation_evalation in epi_score_eval_list:

        supported_total += citation_evalation.supported_count
        extracted_total += citation_evalation.extracted_count
        allowed_total += citation_evalation.allowed_count

    # 计算全局的precision, recall.
    # 分母为0, 统一使用None.

    # | extracted_total | allowed_total | Precision | Recall |
    # |---:|---:|---:|---:|
    # | 0 | 0 | None | None |       无实际引用,无允许引用
    # | 0 | 非零 | None | 0.0 |      无实际引用,有允许引用. 模型完全漏掉了引用.
    # | 非零 | 0 | 0.0 | None |      有实际引用,无允许引用. 模型出现违规引用.
    # | 非零 | 非零 | supported / extracted | supported / allowed |

    # 区别于单条Episode的precision和recall:
    # | extracted | allowed | Precision | Recall |
    # |---|---|---|---|
    # | 空 | 空 | None | None |
    # | 空 | 非空 | None | 0.0 |
    # | 非空 | 空 | 0.0 | None |    违规引用由citation_supported(bool)判定, precision(float)反映违规程度.
    # | 非空 | 非空 | supported / extracted | supported / allowed |

    if extracted_total == 0:        # 模型完全没做任何引用.
        citation_precision = None
    else:
        citation_precision = supported_total / extracted_total
    if allowed_total == 0:          # 所有的case都不允许任何引用.
        citation_recall = None
    else:
        citation_recall = supported_total / allowed_total

    average_steps: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    average_latency_ms: float = 0.0
    latencies = []
    stop_reason_distribution: dict[str, int] = dict()   # 键值对: (str=停止原因, int=该停止原因出现的次数) 一个Episode只有一个stop_reason.
    failure_distribution: dict[str, int] = dict()       # 键值对: (str=失败原因, int=该失败原因出现的次数) 每个agentscore都可以有多个failure_categories

    for episode, agentscore, citation_evalation in epi_score_eval_list:
        average_steps += agentscore.steps
        total_input_tokens += agentscore.input_tokens
        total_output_tokens += agentscore.output_tokens
        total_tokens += agentscore.total_tokens
        average_latency_ms += agentscore.latency_ms
        latencies.append(agentscore.latency_ms)

        stop_reason = episode.result.stop_reason.value
        stop_reason_distribution[stop_reason] = (
            stop_reason_distribution.get(stop_reason, 0) + 1
        )
        for failure_category in agentscore.failure_categories:
            failure_distribution[failure_category] = (
                failure_distribution.get(failure_category, 0) + 1
            )

    if episode_count == 0:
        p50_latency_ms = 0
        p95_latency_ms = 0
        average_steps = 0
        average_latency_ms = 0

    else:
        N = len(latencies)
        latencies.sort()
        import math
        p50_index = math.ceil(0.50 * N) - 1
        p95_index = math.ceil(0.95 * N) - 1
        p50_latency_ms = latencies[p50_index]
        p95_latency_ms = latencies[p95_index]
        average_steps /= N
        average_latency_ms /= N

    return AgentAggregateReport(
        episode_count=episode_count,
        task_success_rate=task_success_rate,
        tool_selection_accuracy=tool_selection_accuracy,
        argument_accuracy=argument_accuracy,
        no_tool_accuracy=no_tool_accuracy,
        retry_recovery_rate=retry_recovery_rate,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        average_steps=average_steps,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_tokens=total_tokens,
        average_latency_ms=average_latency_ms,
        p50_latency_ms=p50_latency_ms,
        p95_latency_ms=p95_latency_ms,
        stop_reason_distribution=stop_reason_distribution,
        failure_distribution=failure_distribution,
    )
