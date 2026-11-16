"""验证工具执行器的校验、重试、失败分类、结果约束与超时。"""

import time

import pytest
from pydantic import BaseModel

from kaiji.demo import DiscountArgs, DiscountResult
from kaiji.models import ToolCall, ToolErrorCode
from kaiji.tools import (
    ToolDefinition,
    ToolExecutionError,
    ToolExecutor,
    ToolRegistry,
)


def test_executor_rejects_truthy_retry_flag() -> None:
    """是否重试属于安全决定，只接受真正的 bool。"""

    # 1 虽然在条件判断中为真，但不能代替显式的 True；配置错误应直接抛异常。
    with pytest.raises(TypeError, match="retry_enabled"):
        ToolExecutor(ToolRegistry(), retry_enabled=1)


def executor_for(definition: ToolDefinition) -> ToolExecutor:
    """把给定工具注册到独立注册表，返回只包含该工具的测试执行器。"""

    registry = ToolRegistry()
    registry.register(definition)
    return ToolExecutor(registry)


def test_unknown_tool_is_structured_and_not_attempted() -> None:
    """空注册表找不到工具时，返回 UNKNOWN_TOOL，且不得尝试执行 handler。"""

    # missing 未注册；故障发生在查找工具阶段，尚未进入参数校验和执行阶段。
    result = ToolExecutor(ToolRegistry()).execute(
        ToolCall(call_id="1", name="missing", arguments={})
    )

    assert not result.ok
    assert result.attempts == 0  # 未执行 handler，尝试次数必须为零。
    assert result.error is not None
    assert result.error.code == ToolErrorCode.UNKNOWN_TOOL
    assert not result.error.retryable  # 原样重试不能补上缺失的工具注册。


def test_arguments_are_validated_before_handler_runs() -> None:
    """价格不符合输入模型时，必须在 handler 执行前返回 INVALID_ARGUMENTS。"""

    called = False

    def handler(args: BaseModel) -> dict:
        """记录是否被调用，用于直接证明非法参数没有到达 handler。"""

        nonlocal called
        called = True
        return {"final_price": 1}

    executor = executor_for(
        ToolDefinition(
            name="discount",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=handler,
        )
    )

    result = executor.execute(
        ToolCall(
            call_id="2",
            name="discount",
            arguments={"price": -1, "percent": 20},  # DiscountArgs 要求 price > 0。
        )
    )

    assert not called  # 直接检查没有发生执行，避免只验证错误码而漏掉错误执行。
    assert result.error is not None
    assert result.error.code == ToolErrorCode.INVALID_ARGUMENTS
    assert result.attempts == 0


def test_executor_rejects_undeclared_arguments_before_handler_runs() -> None:
    """输入含未声明的 admin 字段时必须拒绝，不能静默丢弃后继续执行。"""

    called = False

    def handler(args: BaseModel) -> dict:
        """记录执行标记，检测额外字段是否绕过了输入校验。"""

        nonlocal called
        called = True
        return {"final_price": 1}

    result = executor_for(
        ToolDefinition(
            name="discount",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=handler,
        )
    ).execute(
        ToolCall(
            call_id="extra-argument",
            name="discount",
            arguments={"price": 100, "percent": 10, "admin": True},  # admin 不在输入契约中。
        )
    )

    assert not called
    assert result.attempts == 0
    assert result.error is not None
    assert result.error.code == ToolErrorCode.INVALID_ARGUMENTS


def test_executor_rejects_undeclared_result_fields() -> None:
    """输出即使包含合法价格，也不得携带结果模型未声明的额外字段。"""

    result = executor_for(
        ToolDefinition(
            name="discount",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=lambda args: {"final_price": 90, "untrusted": "extra"},  # 用额外字段触发输出校验失败。
        )
    ).execute(
        ToolCall(
            call_id="extra-result",
            name="discount",
            arguments={"price": 100, "percent": 10},
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == ToolErrorCode.INVALID_RESULT
    assert result.output is None  # 非法输出不得作为成功载荷泄漏给后续使用方。


def test_executor_rejects_non_finite_argument_before_handler() -> None:
    """绕过 ToolCall 构造校验传入 NaN，验证 Executor 自己仍会拦截非法 JSON 参数。"""

    called = False

    class LooseArgs(BaseModel):
        """使用普通 float 字段，避免由业务模型的价格约束代替有限 JSON 检查。"""

        value: float

    def handler(args: LooseArgs) -> dict:
        """记录是否执行，验证非有限参数在执行前被拒绝。"""

        nonlocal called
        called = True
        return {"final_price": 1}

    result = executor_for(
        ToolDefinition(
            name="finite-input",
            description="有限输入",
            arguments_model=LooseArgs,
            result_model=DiscountResult,
            handler=handler,
        )
    ).execute(
        # 普通 ToolCall 构造本就拒绝 NaN；故意绕过它，才能单独测试 Executor 的防线。
        ToolCall.model_construct(
            call_id="nan-argument",
            name="finite-input",
            arguments={"value": float("nan")},
        )
    )

    assert not called
    assert result.attempts == 0
    assert result.error is not None
    assert result.error.code == ToolErrorCode.INVALID_ARGUMENTS


def test_executor_rejects_non_finite_result_as_invalid_result() -> None:
    """宽松输出模型接受 NaN 后，Executor 的有限 JSON 检查仍须返回 INVALID_RESULT。"""

    class LooseResult(BaseModel):
        """允许普通浮点值，用来触发模型校验之后的有限 JSON 检查。"""

        value: float

    result = executor_for(
        ToolDefinition(
            name="finite-output",
            description="有限输出",
            arguments_model=DiscountArgs,
            result_model=LooseResult,
            handler=lambda args: {"value": float("nan")},
        )
    ).execute(
        ToolCall(
            call_id="nan-result",
            name="finite-output",
            arguments={"price": 100, "percent": 10},
        )
    )

    assert not result.ok
    assert result.attempts == 1  # handler 已执行一次，失败发生在输出检查阶段。
    assert result.error is not None
    assert result.error.code == ToolErrorCode.INVALID_RESULT


def test_retryable_failure_recovers_after_one_retry() -> None:
    """首次执行发生可重试失败、第二次成功时，返回成功输出并报告两次尝试。"""

    attempts = 0

    def flaky_handler(args: DiscountArgs) -> dict:
        """用闭包计数制造先失败后成功的确定性执行序列。"""

        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ToolExecutionError("temporary outage", retryable=True)
        return {"final_price": args.price}

    executor = executor_for(
        ToolDefinition(
            name="flaky",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=flaky_handler,
            max_retries=1,  # 首次执行之外再允许一次重试，最多执行两次。
        )
    )

    result = executor.execute(
        ToolCall(
            call_id="3",
            name="flaky",
            arguments={"price": 10, "percent": 0},
        )
    )

    assert result.ok
    assert result.attempts == 2  # 统计 handler 尝试次数，而非 Runtime 决策轮数。
    assert result.output == {"final_price": "10"}


@pytest.mark.parametrize("bad", [1, 0, "false", None])
def test_tool_execution_error_requires_strict_retryable_bool(bad) -> None:
    """整数、字符串和 None 都不能充当重试标记，必须直接拒绝错误类型。"""

    # 字符串 "false" 在 Python 中反而为真，不能用 bool(bad) 代替严格校验。
    with pytest.raises(TypeError, match="真正的 bool"):
        ToolExecutionError("bad retry flag", retryable=bad)


def test_non_retryable_failure_stops_immediately() -> None:
    """即使配置了一次重试预算，不可重试失败也必须在首次执行后结束。"""

    def broken_handler(args: DiscountArgs) -> dict:
        """模拟权限拒绝，并明确标记原样重试无法恢复。"""

        raise ToolExecutionError("permission denied", retryable=False)

    executor = executor_for(
        ToolDefinition(
            name="broken",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=broken_handler,
            max_retries=1,
        )
    )
    result = executor.execute(
        ToolCall(
            call_id="4",
            name="broken",
            arguments={"price": 10, "percent": 0},
        )
    )

    assert not result.ok
    assert result.attempts == 1  # 有重试预算不等于所有失败都应该重试。
    assert result.error is not None
    assert not result.error.retryable


def test_invalid_tool_output_never_becomes_success() -> None:
    """handler 正常返回但字段不符合输出契约时，必须返回 INVALID_RESULT。"""

    executor = executor_for(
        ToolDefinition(
            name="bad_output",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=lambda args: {"wrong_field": args.price},  # 缺少 final_price，且存在未声明字段。
        )
    )

    result = executor.execute(
        ToolCall(
            call_id="5",
            name="bad_output",
            arguments={"price": 10, "percent": 0},
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == ToolErrorCode.INVALID_RESULT


def test_timeout_is_structured_and_respects_retry_policy() -> None:
    """单次等待超时应转为 TIMEOUT；默认无重试预算，因此仅尝试一次。"""

    def slow_handler(args: DiscountArgs) -> dict:
        """故意执行得比等待上限更久，以触发 Executor 的超时分支。"""

        # 超时只结束 Executor 的等待，已开始的线程任务仍可能继续执行。
        time.sleep(0.05)
        return {"final_price": args.price}

    executor = executor_for(
        ToolDefinition(
            name="slow",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=slow_handler,
            timeout_seconds=0.005,  # 小于 handler 的 0.05 秒；max_retries 默认是 0。
        )
    )

    result = executor.execute(
        ToolCall(
            call_id="6",
            name="slow",
            arguments={"price": 10, "percent": 0},
        )
    )

    assert not result.ok
    assert result.attempts == 1
    assert result.error is not None
    assert result.error.code == ToolErrorCode.TIMEOUT
    assert result.error.retryable  # 错误可重试与是否还有重试预算是两件事。


def test_unexpected_runtime_error_becomes_non_retryable_execution_failure() -> None:
    """Executor 必须把 handler 的普通 RuntimeError 转成不可重试的结构化失败。"""

    def runtime_error_handler(args: DiscountArgs) -> dict:
        """抛出非 ToolExecutionError，验证执行器对未预期异常的兜底处理。"""

        raise RuntimeError("runtime handler failed")

    executor = executor_for(
        ToolDefinition(
            name="runtime_error_test",
            description="测试普通异常边界",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=runtime_error_handler,
        )
    )

    result = executor.execute(
        ToolCall(
            call_id="7",
            name="runtime_error_test",
            arguments={"price": 10, "percent": 0},
        )
    )

    assert not result.ok
    assert result.attempts == 1
    assert result.error is not None
    assert result.error.code == ToolErrorCode.EXECUTION_FAILED
    assert not result.error.retryable


def test_retryable_failure_reports_two_attempts_when_budget_is_exhausted() -> None:
    """一次重试预算表示最多执行两次 handler，耗尽后 attempts 必须为 2。"""

    call_count = 0

    def always_retryable(args: DiscountArgs) -> dict:
        """每次都记录调用并抛可重试错误，确保失败不会自行恢复。"""

        nonlocal call_count
        call_count += 1
        raise ToolExecutionError("temporary outage", retryable=True)

    executor = executor_for(
        ToolDefinition(
            name="always_retryable",
            description="测试重试耗尽",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=always_retryable,
            max_retries=1,
        )
    )

    result = executor.execute(
        ToolCall(
            call_id="8",
            name="always_retryable",
            arguments={"price": 10, "percent": 0},
        )
    )

    assert not result.ok
    assert result.attempts == 2
    assert call_count == 2  # 同时核对真实调用次数，防止 attempts 只是填了正确数字。
    assert result.error is not None
    assert result.error.retryable  # 预算耗尽不改变错误本身的可重试分类。


def test_unexpected_error_message_preserves_type_and_original_text() -> None:
    """普通异常的结构化消息应保留异常类型和原始文本，便于 Trace 诊断。"""

    def value_error_handler(args: DiscountArgs) -> dict:
        """在 handler 内抛出带固定文本的 ValueError，供错误消息断言核对。"""

        raise ValueError("value handler failed")

    executor = executor_for(
        ToolDefinition(
            name="value_error_handler",
            description="测试错误诊断信息",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=value_error_handler,
        )
    )

    result = executor.execute(
        ToolCall(
            call_id="9",
            name="value_error_handler",
            arguments={"price": 10, "percent": 0},
        )
    )

    assert not result.ok
    assert result.attempts == 1
    assert result.error is not None
    assert result.error.code == ToolErrorCode.EXECUTION_FAILED
    assert not result.error.retryable
    assert "ValueError" in result.error.message  # 保留异常类型，便于定位失败原因。
    assert "value handler failed" in result.error.message  # 保留原始文本，便于诊断。
