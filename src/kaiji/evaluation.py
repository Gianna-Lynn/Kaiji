"""定义 Kaiji 固定评测用例、观测结果、指标聚合与参考运行器。"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field, model_validator

from kaiji.models import StopReason


class EvalCase(BaseModel):
    """定义一条固定评测用例及其期望行为。"""

    case_id: str
    prompt: str
    category: str
    expected_stop_reason: StopReason
    expected_tool: str | None = None
    expects_retry_recovery: bool = False
    expects_citation: bool = False
    max_tool_calls: int = Field(default=1, ge=0)


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
      预期结果；不适用时为 ``None``。
    - ``steps`` 是状态机消耗的逻辑迭代：Native 使用 ``RunResult.steps``，
      检索图使用 ``step_count``，不经过检索的终止流程也至少记 1 步。
    - ``latency_ms`` 从 runner 开始执行该 case 到产生终止结果，采用墙钟时间。
    - ``unnecessary_tool_calls`` 只统计上述实际尝试中，按 case 判据本不需要的
      次数；它不能超过 ``tool_calls``。
    - ``citation_supported`` 只有在引用均来自成功且经过校验的工具证据，并且
      证据支持回答中的相关表述时才为真；不要求引用的 case 使用 ``None``。
    """

    case_id: str
    task_success: bool
    selected_tool: str | None
    arguments_correct: bool | None
    tool_calls: int = Field(default=0, ge=0)
    successful_tool_calls: int = Field(default=0, ge=0)
    retry_recovered: bool | None = None
    unnecessary_tool_calls: int = Field(default=0, ge=0)
    steps: int = Field(ge=1)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    stop_reason: StopReason
    citation_supported: bool | None = None

    @model_validator(mode="after")
    def call_counts_are_consistent(self) -> EvalObservation:
        """确保成功调用和无用调用都不会超过总调用数。"""

        # TODO 11a：补全跨字段计数不变量。
        #
        # Field 已经负责单字段下界；这里处理只有同时观察多个字段才知道的
        # 矛盾。successful_tool_calls 和 unnecessary_tool_calls 都是
        # tool_calls 所表示的“实际 handler 尝试”中的子集，各自都不得大于
        # tool_calls。违反契约时抛出 ValueError，合法时返回 self。
        #
        # 注意：successful 与 unnecessary 不要求互斥，不能擅自增加“两者之和
        # 不得超过总数”的契约。当前占位只返回 self，关系异常测试应当失败。
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

    # TODO 11b：建立不会静默吞掉重复项的 case_id 索引。
    #
    # EvalCase 与 EvalObservation 共用这条身份契约，所以通过 EvalItem 复用
    # 实现。遍历 items：保存首次/当前对象的同时收集所有重复 case_id；若存在
    # 重复，抛出包含 label 和排序后重复标识的 ValueError；否则返回索引。
    #
    # 当前占位的字典推导会让后值覆盖前值，正是本 TODO 要消除的假阳性来源。
    return {item.case_id: item for item in items}


def load_cases(path: Path) -> list[EvalCase]:
    """从 JSONL 文件加载用例，并拒绝重复的 ``case_id``。"""

    cases = [
        EvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # TODO 11c：把 JSONL 解析结果接入统一的唯一性检查。
    #
    # 上方代码已经完成“非空物理行 JSON 字符串 -> EvalCase”的序列化边界；
    # 这里仍需调用 _index_by_unique_case_id(cases, "评测用例")。不要把列表直接
    # 转成 dict 后再返回，否则重复 case_id 会在没有告警的情况下被覆盖；成功
    # 校验后仍按文件顺序返回原 cases 列表。
    return cases


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def aggregate(cases: list[EvalCase], observations: list[EvalObservation]) -> EvalReport:
    """按固定分母聚合与用例一一对应的完整观测集。

    工具选择和参数准确率只以 ``expected_tool`` 非空的用例为分母；重试与
    引用指标分别以显式要求该能力的用例为分母。适用字段为 ``None`` 时按
    失败计入既定分母，不能通过缺失观测缩小分母。
    """

    # TODO 11d：严格对齐 cases 与 observations。
    #
    # 先分别调用 _index_by_unique_case_id，确保两侧标识各自唯一，再比较两组
    # case_id。观测必须不多不少地覆盖每条用例；缺失或额外标识都抛 ValueError，
    # 错误文本应分别列出排序后的缺失项和额外项。不能只比较 set 推导，因为
    # set 会掩盖“同一 id 出现两次”的重复观测。
    #
    # TODO 11e：按既定口径计算完整 EvalReport。
    #
    # 1. 按 cases 的原顺序从 observation 索引取值；输入列表顺序不是配对依据；
    # 2. task_success_rate 中一条用例成功，必须同时满足：观测的业务答案正确、
    #    stop_reason 等于期望值、tool_calls 不超过 max_tool_calls；
    # 3. selection/argument 只统计 expected_tool 非空的用例；argument 的 None
    #    在适用用例中按 False，而不是从分母删掉；
    # 4. retry/citation 只统计 case 明确要求相应能力的用例；适用字段为 None
    #    同样按 False；
    # 5. tool_success 与 unnecessary 的分母都是所有观测的 tool_calls 总和。
    #    这里的调用数是 handler 尝试，不是 Runtime 的 steps；零调用时比率为 0；
    # 6. average_steps 与延迟来自严格对齐后的全部观测；p50/p95 使用
    #    _percentile；停止原因按观测实际 stop_reason 计数；空分母由 _rate
    #    返回 0。最终 case_count 等于用例数。
    #
    raise NotImplementedError("TODO 11d—11e：实现严格对齐与全部指标聚合")


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
