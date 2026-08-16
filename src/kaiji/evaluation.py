"""定义 Kaiji 固定评测用例、观测结果、指标聚合与参考运行器。"""

from __future__ import annotations

import json
import math
from collections import Counter
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
    - ``steps`` 是状态机消耗的逻辑迭代：Native 使用 ``RunResult.steps``，
      检索图使用 ``step_count``，不经过检索的终止流程也至少记 1 步。
    - ``latency_ms`` 从 runner 开始执行该 case 到产生终止结果，采用墙钟时间。
    - ``unnecessary_tool_calls`` 只统计上述实际尝试中，按 case 判据本不需要的
      次数；它不能超过 ``tool_calls``。
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

        if self.successful_tool_calls > self.tool_calls:
            raise ValueError("successful_tool_calls 不能超过 tool_calls")
        if self.unnecessary_tool_calls > self.tool_calls:
            raise ValueError("unnecessary_tool_calls 不能超过 tool_calls")
        if self.selected_tool is None and self.arguments_correct is not None:
            raise ValueError("没有工具选择时 arguments_correct 必须为 None")
        if self.retry_recovered is True and self.tool_calls < 2:
            raise ValueError("重试恢复至少需要两次实际工具尝试")
        if self.citation_supported is True and self.successful_tool_calls == 0:
            raise ValueError("受支持引用必须来自至少一次成功工具结果")
        return self


class EvalReport(BaseModel):
    """汇总整套评测的可靠性、效率、延迟和引用指标。"""

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
    """按 ``case_id`` 建立索引，并显式报告重复标识。"""

    result: dict[str, EvalItem] = {}
    duplicates: set[str] = set()
    for item in items:
        if item.case_id in result:
            duplicates.add(item.case_id)
        result[item.case_id] = item
    if duplicates:
        duplicate_text = ", ".join(sorted(duplicates))
        raise ValueError(f"{label}的 case_id 必须唯一，重复项: {duplicate_text}")
    return result


def load_cases(path: Path) -> list[EvalCase]:
    """从 JSONL 文件加载用例，并拒绝重复的 ``case_id``。"""

    cases = [
        EvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    _index_by_unique_case_id(cases, "评测用例")
    return cases


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
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

    case_by_id = _index_by_unique_case_id(cases, "评测用例")
    observation_by_id = _index_by_unique_case_id(observations, "评测观测")
    case_ids = set(case_by_id)
    observation_ids = set(observation_by_id)
    if case_ids != observation_ids:
        missing = ", ".join(sorted(case_ids - observation_ids)) or "无"
        unexpected = ", ".join(sorted(observation_ids - case_ids)) or "无"
        raise ValueError(
            f"评测观测必须恰好覆盖每条用例一次；缺失: {missing}；额外: {unexpected}"
        )
    return [observation_by_id[case.case_id] for case in cases]


def _aggregate_aligned(
    cases: list[EvalCase],
    ordered_observations: list[EvalObservation],
) -> EvalReport:
    """在 11d 已完成身份对齐的前提下计算固定分母指标。

    工具选择和参数准确率只以 ``expected_tool`` 非空的用例为分母；重试与
    引用指标分别以显式要求该能力的用例为分母。适用字段为 ``None`` 时按
    失败计入既定分母，不能通过缺失观测缩小分母。
    """

    if len(cases) != len(ordered_observations):
        raise ValueError("已对齐观测数量必须等于 case 数量")

    selection_scores = []
    argument_scores = []
    retry_scores = []
    citation_scores = []
    total_calls = 0
    successful_calls = 0
    unnecessary_calls = 0

    task_success_scores = []
    for case, item in zip(cases, ordered_observations, strict=True):
        task_success_scores.append(
            item.task_success
            and item.stop_reason == case.expected_stop_reason
            and item.tool_calls <= case.max_tool_calls
        )
        if case.expected_tool is not None:
            selection_scores.append(item.selected_tool == case.expected_tool)
            argument_scores.append(bool(item.arguments_correct))
        if case.expects_retry_recovery:
            retry_scores.append(bool(item.retry_recovered))
        if case.expects_citation:
            citation_scores.append(bool(item.citation_supported))
        total_calls += item.tool_calls
        successful_calls += item.successful_tool_calls
        unnecessary_calls += item.unnecessary_tool_calls

    return EvalReport(
        case_count=len(cases),
        task_success_rate=_rate(task_success_scores),
        tool_selection_accuracy=_rate(selection_scores),
        argument_accuracy=_rate(argument_scores),
        tool_success_rate=successful_calls / total_calls if total_calls else 0.0,
        retry_recovery_rate=_rate(retry_scores),
        unnecessary_tool_call_rate=(
            unnecessary_calls / total_calls if total_calls else 0.0
        ),
        average_steps=(
            sum(item.steps for item in ordered_observations) / len(ordered_observations)
            if ordered_observations
            else 0.0
        ),
        p50_latency_ms=_percentile(
            [item.latency_ms for item in ordered_observations], 0.50
        ),
        p95_latency_ms=_percentile(
            [item.latency_ms for item in ordered_observations], 0.95
        ),
        stop_reason_distribution=dict(
            Counter(item.stop_reason.value for item in ordered_observations)
        ),
        citation_supported_rate=_rate(citation_scores),
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
