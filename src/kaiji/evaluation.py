"""定义 Kaiji 固定评测用例、观测结果、指标聚合与参考运行器。"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from kaiji.models import StopReason


class EvalCase(BaseModel):
    """定义一条固定评测用例及其期望行为。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    prompt: str
    category: str
    expected_stop_reason: StopReason
    expected_tool: str | None = None
    expects_retry_recovery: StrictBool = False
    expects_citation: StrictBool = False
    max_tool_calls: StrictInt = Field(default=1, ge=0)

    @field_validator("case_id", "prompt", "category")
    @classmethod
    def text_boundaries_are_not_blank(cls, value: str) -> str:
        """用例身份、提示与分类不得为空白。"""

        if not value.strip():
            raise ValueError("EvalCase 文本边界不得为空白")
        return value

    @field_validator("expected_tool")
    @classmethod
    def expected_tool_is_not_blank(cls, value: str | None) -> str | None:
        """空值表示不要求工具；空白字符串不承担第三种语义。"""

        if value is not None and not value.strip():
            raise ValueError("expected_tool 不得为空白")
        return value


class EvalObservation(BaseModel):
    """记录被测 Agent 在一条用例上的实际表现。

    所有字段都必须由真实 runner 按同一口径产生：

    - ``task_success`` 只表示回答或业务结果本身是否正确；聚合器还会检查
      停止原因和工具调用上限，三者全部满足才计为最终任务成功。
    - ``tool_calls`` 统一表示实际进入工具 handler 的执行尝试数；Native
      runner 可累加 ``ToolResult.attempts``。参数在执行前被拒或工具不存在时
      没有实际尝试，因此可以是 0。
    - ``successful_tool_calls`` 是这些实际尝试中成功完成且输出校验通过的
      次数。一次先失败、后成功的重试贡献两次调用和一次成功。
    - ``selected_tool`` 是该 case 的主要工具选择；存在多次选择时固定记录模型
      第一次工具选择。``arguments_correct`` 只评价这次选择的参数，不评价
      后续恢复调用。
    - ``retry_recovered`` 表示 Executor retry 或 query rewrite 之后最终恢复到
      预期结果；Provider 的 transport retry 属于请求可靠性，不计入该业务指标；
      不适用时为 ``None``。
    - ``steps`` 表示本次 case 的循环轮数。它统计的是 Agent 从开始执行到
        结束所经历的决策/执行轮次，不等于工具调用次数。Native runner 直接用
        ``RunResult.steps``，图式 runner 使用 ``step_count``；不经过检索的短流程
        也至少记 1。
    - ``latency_ms`` 从 runner 开始执行该 case 到产生终止结果，采用墙钟时间。
    - ``unnecessary_tool_calls`` 只统计上述实际尝试中，按 case 判据本不需要的
      次数；它不能超过 ``tool_calls``。错误的约定: successful_tool_calls + 
      unnecessary_tool_calls == tool_calls. 因为可能重叠: 一次工具调用既可能成功，
      又可能是不必要的。
    - ``citation_supported`` 只有在引用均来自成功且经过校验的工具证据，并且
      证据支持回答中的相关表述时才为真；不要求引用的 case 使用 ``None``。
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    task_success: StrictBool
    selected_tool: str | None
    arguments_correct: StrictBool | None
    tool_calls: StrictInt = Field(default=0, ge=0)
    successful_tool_calls: StrictInt = Field(default=0, ge=0)
    retry_recovered: StrictBool | None = None
    unnecessary_tool_calls: StrictInt = Field(default=0, ge=0)
    steps: StrictInt = Field(ge=1)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    stop_reason: StopReason
    citation_supported: StrictBool | None = None

    @field_validator("case_id")
    @classmethod
    def case_id_is_not_blank(cls, value: str) -> str:
        """Observation 必须具有可对齐的非空身份。"""

        if not value.strip():
            raise ValueError("case_id 不得为空白")
        return value

    @field_validator("selected_tool")
    @classmethod
    def selected_tool_is_not_blank(cls, value: str | None) -> str | None:
        """没有选择用 None 表示，不能使用空白字符串。"""

        if value is not None and not value.strip():
            raise ValueError("selected_tool 不得为空白")
        return value

    @model_validator(mode="after")
    def call_counts_are_consistent(self) -> EvalObservation:
        """确保成功调用和无用调用都不会超过总调用数。"""
        # TODO 11a：在多个字段之间进行联合校验。Field 只负责单字段下界；这里必须
        # 直接拒绝成功/无用调用数超过总尝试、没有工具却评价参数、一次尝试却
        # 声称重试恢复，以及没有成功证据却声称引用受支持。合法时返回 self。

        # 拒绝成功/无用调用数超过总尝试
        if self.successful_tool_calls > self.tool_calls:
            raise ValueError("成功调用数超过总尝试")
        if self.unnecessary_tool_calls > self.tool_calls:
            raise ValueError("无用调用数超过总尝试")

        # 拒绝没有工具却评价参数
        if self.selected_tool is None and self.arguments_correct is not None:
            raise ValueError("没有选择工具时不能评价参数")

        # 拒绝一次尝试却声称重试恢复
        if self.tool_calls <= 1 and self.retry_recovered is True:
            raise ValueError("一次尝试却声称重试恢复")

        # 拒绝没有成功证据却声称引用受支持
        if self.successful_tool_calls == 0 and self.citation_supported:
            # 没有任何成功且通过输出校验的工具调用 -> 没有成功证据.
            raise ValueError("没有成功证据却声称引用受支持")

        return self


class EvalReport(BaseModel):
    """汇总整套评测的可靠性、效率、延迟和引用指标。

    报告中的比例字段均为 ``0.0`` 到 ``1.0`` 之间的小数，而不是百分数；
    例如 ``0.8`` 表示百分之八十。所有比例都基于已经通过 case/observation
    身份对齐的完整数据计算，不能通过省略观测来缩小分母。

    字段含义如下：

    - ``case_count``：本次评测包含的用例总数，也是任务成功率和停止原因
        分布使用的基础总数。
    - ``task_success_rate``：任务结果正确，且停止原因和工具调用上限也符合
        用例要求的用例占全部用例的比例。
    - ``tool_selection_accuracy``：在声明了 ``expected_tool`` 的用例中，
        第一次选择的工具名与期望工具一致的比例。不要求工具的用例不进入分母。
    - ``argument_accuracy``：在声明了 ``expected_tool`` 的用例中，工具参数
        被判定为正确的比例；参数为 ``None`` 按失败计入分母。
    - ``tool_success_rate``：实际工具执行尝试中，成功完成且输出通过校验的
        调用占全部实际尝试的比例。没有实际尝试时为 ``0.0``。
    - ``retry_recovery_rate``：明确要求重试恢复的用例中，最终通过重试或
        query rewrite 恢复到预期结果的比例；不要求该能力的用例不进入分母。
    - ``unnecessary_tool_call_rate``：实际工具调用中被判定为不必要的调用
        所占比例；不必要调用与成功调用可以同时计入，不能将两者视为互斥分类。
    - ``average_steps``：每条用例 Agent 循环轮数的算术平均值；它表示决策
        或执行轮次，不等于工具调用次数。
    - ``p50_latency_ms``：延迟的第 50 百分位，即中位数附近的耗时，单位为
        毫秒。
    - ``p95_latency_ms``：延迟的第 95 百分位，单位为毫秒，用于观察较慢的
        长尾请求。
    - ``stop_reason_distribution``：按停止原因统计用例数量的映射；键是
        ``StopReason`` 的字符串值，值是对应数量。各数量之和应等于
        ``case_count``。
    - ``citation_supported_rate``：明确要求引用支持的用例中，引用确实来自
        成功且通过校验的工具证据、并且证据支持相关回答的比例；不要求引用的
        用例不进入分母，声明支持为 ``None`` 或 ``False`` 按失败计入分母。
    """
    case_count: int
    task_success_rate: float
    tool_selection_accuracy: float
    argument_accuracy: float
    tool_success_rate: float
    retry_recovery_rate: float
    unnecessary_tool_call_rate: float
    average_steps: float
    p50_latency_ms: float
    p95_latency_ms: float
    stop_reason_distribution: dict[str, int]
    citation_supported_rate: float


EvalItem = TypeVar("EvalItem", EvalCase, EvalObservation)


def _index_by_unique_case_id(
    items: list[EvalItem],
    label: str,
) -> dict[str, EvalItem]:
    """
    把一组 EvalCase 或 EvalObservation 按 case_id 建成一个查找表。
    如果存在重复的 case_id，收集并稳定报告全部重复项，避免新的
    EvalCase/EvalObservation 覆盖旧值。
    """

    # TODO 11b：建立唯一索引并显式报告排序后的重复 ID。不能使用会让后值
    # 静默覆盖前值的字典推导。
    seen_case_ids: set[str] = set()
    duplicate_case_ids: set[str] = set()
    caseid_evalitem_dict: dict[str, EvalItem] = {}
    for item in items:
        if item.case_id in seen_case_ids:
            duplicate_case_ids.add(item.case_id)
        else:
            seen_case_ids.add(item.case_id)
            caseid_evalitem_dict[item.case_id] = item
    if duplicate_case_ids:
        duplicate_ids = ", ".join(sorted(duplicate_case_ids))
        raise ValueError(f"{label}的 case_id 必须唯一. 重复项: {duplicate_ids}")
    return caseid_evalitem_dict


def load_cases(path: Path) -> list[EvalCase]:
    """从 JSONL 文件加载用例，并拒绝重复的 ``case_id``。"""

    cases = [
        EvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # TODO 11c：调用统一唯一性检查；成功后仍按文件顺序返回 cases。
    _index_by_unique_case_id(cases, "评测用例")
    return cases


def _rate(values: list[bool]) -> float:
    """计算一组布尔值的成功率"""
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    """计算一组数的百分位数. percentile=0.5时, 表示求P50; percentile=0.95时, 表示求P95"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _align_observations(
    cases: list[EvalCase],
    observations: list[EvalObservation],
) -> list[EvalObservation]:
    """严格检查两侧唯一覆盖，并按 case 文件顺序返回观测。"""

    # TODO 11d：分别建立唯一索引，检查两侧 ID 集合完全相等，并按 cases
    # 的原始顺序返回观测。缺失与额外 ID 必须分别进入错误信息。

    case_index = _index_by_unique_case_id(cases, "评测用例")
    observation_index = _index_by_unique_case_id(observations, "评测观测")

    missing  = sorted(set(case_index) - set(observation_index)) # 站在case_index上, 看observation_index中存在哪些缺失
    extra = sorted(set(observation_index) - set(case_index))    # 站在case_index上, 看observation_index中存在哪些多余

    if missing or extra:
        details=[]
        if missing:
            details.append(f"缺失: {','.join(missing)}")
        if extra:
            details.append(f"额外: {','.join(extra)}")
        raise ValueError("; ".join(details))
    return [observation_index[case.case_id] for case in cases]


def _aggregate_aligned(
    cases: list[EvalCase],
    ordered_observations: list[EvalObservation],
) -> EvalReport:
    """在 11d 已完成身份对齐的前提下计算固定分母指标。

    工具选择和参数准确率只以 ``expected_tool`` 非空的用例为分母；重试与
    引用指标分别以显式要求该能力的用例为分母。适用字段为 ``None`` 时按
    失败计入既定分母，不能通过缺失观测缩小分母。
    """

    # TODO 11e：只接收 11d 已按 case 顺序对齐的观测，按书面固定分母计算
    # 全部指标。必须拒绝长度不等；不适用和失败不得混为同一语义。
    case_cnt = len(cases)
    observation_cnt = len(ordered_observations)
    if case_cnt != observation_cnt:
        raise ValueError("case长度和observation长度不等")

    task_success_values: list[bool] = []
    tool_selection_values: list[bool] = []
    argument_values: list[bool] = []
    retry_recovery_values: list[bool] = []
    citation_values: list[bool] = []
    step_cnt = 0
    stop_reason_distribution: dict[str, int] = dict()
    tool_call_cnt = 0
    successful_tool_calls_cnt = 0
    unnecessary_tool_calls_cnt = 0
    latencies: list[float] = []

    for case, observation in zip(cases, ordered_observations, strict=True):
        task_success_values.append(
            observation.task_success 
            and observation.stop_reason == case.expected_stop_reason 
            and observation.tool_calls <= case.max_tool_calls
        )

        if case.expected_tool is not None:
            tool_selection_values.append(
                observation.selected_tool == case.expected_tool
            )
            argument_values.append(observation.arguments_correct is True)

        if case.expects_retry_recovery:
            retry_recovery_values.append(observation.retry_recovered is True)

        if case.expects_citation:
            citation_values.append(observation.citation_supported is True)

        step_cnt += observation.steps
        stop_reason = observation.stop_reason.value
        stop_reason_distribution[stop_reason] = (
            stop_reason_distribution.get(stop_reason, 0) + 1
        )
        tool_call_cnt += observation.tool_calls
        successful_tool_calls_cnt += observation.successful_tool_calls
        unnecessary_tool_calls_cnt += observation.unnecessary_tool_calls
        latencies.append(observation.latency_ms)

    return EvalReport(
        case_count=case_cnt,
        task_success_rate=_rate(task_success_values),
        tool_selection_accuracy=_rate(tool_selection_values),
        argument_accuracy=_rate(argument_values),
        tool_success_rate=(
            successful_tool_calls_cnt / tool_call_cnt if tool_call_cnt else 0.0
        ),
        retry_recovery_rate=_rate(retry_recovery_values),
        unnecessary_tool_call_rate=(
            unnecessary_tool_calls_cnt / tool_call_cnt if tool_call_cnt else 0.0
        ),
        average_steps=step_cnt / case_cnt if case_cnt else 0.0,
        p50_latency_ms=_percentile(latencies, 0.5),
        p95_latency_ms=_percentile(latencies, 0.95),
        stop_reason_distribution=stop_reason_distribution,
        citation_supported_rate=_rate(citation_values),
    )


def aggregate(cases: list[EvalCase], observations: list[EvalObservation]) -> EvalReport:
    """先严格对齐完整观测，再把有序输入交给固定指标公式。"""

    return _aggregate_aligned(cases, _align_observations(cases, observations))


def run_eval(
    cases: list[EvalCase],
    runner: Callable[[EvalCase], EvalObservation],
) -> EvalReport:
    """逐条运行固定用例，并把观测结果交给聚合器。"""

    return aggregate(cases, [runner(case) for case in cases])


def _reference_call_counts(case: EvalCase) -> tuple[int, int]:
    """为聚合管线金标构造与用例停止方式一致的尝试数和成功数。"""

    if case.expected_tool is None:
        return 0, 0
    if case.category in {"argument_error", "unknown_tool"}:
        # 两类失败都发生在 handler 之前，不产生实际工具执行尝试。
        return 0, 0
    if case.expected_stop_reason == StopReason.TOOL_FAILED:
        attempts = (
            case.max_tool_calls
            if case.category == "timeout_failure"
            else min(1, case.max_tool_calls)
        )
        return attempts, 0
    if case.expected_stop_reason in {
        StopReason.MAX_STEPS,
        StopReason.INSUFFICIENT_EVIDENCE,
    }:
        # 工具本身可以成功返回，但 Agent 仍可能因循环上限或空证据而失败。
        return case.max_tool_calls, case.max_tool_calls
    if case.category == "query_rewrite":
        # 第一次空 SearchResponse 与第二次有证据响应都通过了输出 Schema；
        # “空证据”是工作流恢复信号，不是工具执行失败。
        attempts = min(2, case.max_tool_calls)
        return attempts, attempts
    if case.expects_retry_recovery:
        attempts = min(2, case.max_tool_calls)
        return attempts, 1 if attempts else 0

    attempts = min(1, case.max_tool_calls)
    return attempts, attempts


def reference_observation(case: EvalCase) -> EvalObservation:
    """生成聚合管线的确定性金标输入，不执行或冒充真实 Agent 回归。

    该函数只提供一组与 case 元数据内部一致的观测，用来验证聚合公式和报告
    管线。它不会调用 Runtime、Workflow 或 Adapter，不能作为功能验收证据。
    """

    tool_calls, successful_tool_calls = _reference_call_counts(case)
    # Executor 内部 retry 会增加 handler 尝试数，但不会增加 Runtime 决策轮；
    # query rewrite 和循环终止场景才会消费多个显式状态机步骤。
    has_multi_step_flow = case.category == "query_rewrite" or (
        case.expected_stop_reason
        in {StopReason.MAX_STEPS, StopReason.INSUFFICIENT_EVIDENCE}
    )
    logical_steps = max(1, tool_calls) if has_multi_step_flow else 1
    return EvalObservation(
        case_id=case.case_id,
        task_success=True,
        selected_tool=case.expected_tool,
        arguments_correct=(
            case.category != "argument_error" if case.expected_tool else None
        ),
        tool_calls=tool_calls,
        successful_tool_calls=successful_tool_calls,
        retry_recovered=(
            case.expected_stop_reason == StopReason.COMPLETED
            if case.expects_retry_recovery
            else None
        ),
        unnecessary_tool_calls=0,
        steps=logical_steps,
        latency_ms=10.0,
        stop_reason=case.expected_stop_reason,
        citation_supported=True if case.expects_citation else None,
    )


def report_to_json(report: EvalReport) -> str:
    """把评测报告格式化为便于阅读的 JSON 文本。"""

    return json.dumps(report.model_dump(), ensure_ascii=False, indent=2)
