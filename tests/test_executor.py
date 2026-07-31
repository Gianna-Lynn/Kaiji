"""验证工具执行器的校验、重试、失败分类、结果约束与超时。"""

import time

from pydantic import BaseModel

from kaiji.demo import DiscountArgs, DiscountResult
from kaiji.models import ToolCall, ToolErrorCode
from kaiji.tools import (
    ToolDefinition,
    ToolExecutionError,
    ToolExecutor,
    ToolRegistry,
)


def executor_for(definition: ToolDefinition) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(definition)
    return ToolExecutor(registry)


def test_unknown_tool_is_structured_and_not_attempted() -> None:
    result = ToolExecutor(ToolRegistry()).execute(
        ToolCall(call_id="1", name="missing", arguments={})
    )

    assert not result.ok
    assert result.attempts == 0
    assert result.error is not None
    assert result.error.code == ToolErrorCode.UNKNOWN_TOOL
    assert not result.error.retryable


def test_arguments_are_validated_before_handler_runs() -> None:
    called = False

    def handler(args: BaseModel) -> dict:
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
            arguments={"price": -1, "percent": 20},
        )
    )

    assert not called
    assert result.error is not None
    assert result.error.code == ToolErrorCode.INVALID_ARGUMENTS
    assert result.attempts == 0


def test_retryable_failure_recovers_after_one_retry() -> None:
    attempts = 0

    def flaky_handler(args: DiscountArgs) -> dict:
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
            max_retries=1,
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
    assert result.attempts == 2
    assert result.output == {"final_price": "10"}


def test_non_retryable_failure_stops_immediately() -> None:
    def broken_handler(args: DiscountArgs) -> dict:
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
    assert result.attempts == 1
    assert result.error is not None
    assert not result.error.retryable


def test_invalid_tool_output_never_becomes_success() -> None:
    executor = executor_for(
        ToolDefinition(
            name="bad_output",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=lambda args: {"wrong_field": args.price},
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
    def slow_handler(args: DiscountArgs) -> dict:
        time.sleep(0.05)
        return {"final_price": args.price}

    executor = executor_for(
        ToolDefinition(
            name="slow",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=slow_handler,
            timeout_seconds=0.005,
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
    assert result.error.retryable

def test_handler_runtime_error_return_execution_failed() -> None:
    """
    handler 抛 RuntimeError，应返回 EXECUTION_FAILED，不能向外抛异常。
    """
    def runtime_error_handler(args: BaseModel):
        raise RuntimeError("runtime_error_handler raise RuntimeError")
    executor = executor_for(
        ToolDefinition(
            name="runtime_error_test",
            description="测试工具",
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
    assert result.error.retryable is False

def test_attempt_is_two_after_max_attempts() -> None:
    """可重试的失败持续发生后, 确认最多尝试两次(目前max_retry最多重试次数设置为1, 最多尝试次数为2)"""
    call_cnt = 0

    def broken_handler_retryable(args: DiscountArgs) -> dict:
        nonlocal call_cnt
        call_cnt += 1
        raise ToolExecutionError("temporary outage", retryable=True)

    executor = executor_for(
        ToolDefinition(
            name="broken_handler_retryable",
            description="测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=broken_handler_retryable,
            max_retries=1, # 最多允许重试一次, handler最多会被调用两次.attempts是handler实实在在被调用的次数, 
        )
    )
    result = executor.execute(
        ToolCall(
            call_id="8",
            name="broken_handler_retryable",
            arguments={"price": 10, "percent": 0},
        )
    )
    assert not result.ok
    assert result.attempts == 2 
    assert call_cnt == 2
    assert result.error is not None
    assert result.error.retryable


def test_handler_return_original_error_message() -> None:
    """
    错误结果应保留原始异常信息，便于 Trace 诊断。
    """
    def value_error_handler(args: BaseModel):
        raise ValueError("value_error_handler raise ValueError")
    executor = executor_for(
        ToolDefinition(
            name="value_error_handler",
            description="测试工具",
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
    assert result.error.retryable is False
    assert "工具调用失败, 报错信息为value_error_handler raise ValueError" == result.error.message