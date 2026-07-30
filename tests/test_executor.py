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
