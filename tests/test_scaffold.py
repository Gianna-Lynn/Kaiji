"""验证核心模型与静态约束无需进入未来 TODO 即可独立运行。"""

import pytest
from pydantic import ValidationError

from kaiji.demo import DiscountArgs, DiscountResult
from kaiji.models import (
    ProviderAttemptFailure,
    ProviderErrorCode,
    ProviderFailure,
    ToolErrorCode,
    ToolFailure,
    ToolResult,
)
from kaiji.tools import ToolDefinition


def test_pydantic_contract_rejects_bad_arguments() -> None:
    """Pydantic 参数模型必须在工具执行前拒绝越界输入。"""

    with pytest.raises(ValidationError):
        DiscountArgs(price=-1, percent=20)


def test_tool_definition_enforces_single_retry_limit() -> None:
    """v0 的工具定义最多允许一次重试，防止无界执行。"""

    with pytest.raises(ValueError, match="at most one retry"):
        ToolDefinition(
            name="too_many_retries",
            description="非法工具定义",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=lambda args: {"final_price": args.price},
            max_retries=2,
        )


def test_tool_result_requires_consistent_payload() -> None:
    """失败 ToolResult 必须保留结构化错误，而不是伪装成成功输出。"""

    failure = ToolFailure(
        code=ToolErrorCode.EXECUTION_FAILED,
        message="预期的测试失败",
        retryable=False,
    )

    result = ToolResult(
        call_id="scaffold-1",
        tool_name="demo",
        ok=False,
        attempts=1,
        error=failure,
    )

    assert result.error == failure


@pytest.mark.parametrize(
    "values",
    [
        {
            "call_id": "success-zero",
            "tool_name": "demo",
            "ok": True,
            "attempts": 0,
            "output": {},
        },
        {
            "call_id": "preflight-with-attempt",
            "tool_name": "demo",
            "ok": False,
            "attempts": 1,
            "error": ToolFailure(
                code=ToolErrorCode.INVALID_ARGUMENTS,
                message="参数在 handler 前失败",
                retryable=False,
            ),
        },
        {
            "call_id": "runtime-without-attempt",
            "tool_name": "demo",
            "ok": False,
            "attempts": 0,
            "error": ToolFailure(
                code=ToolErrorCode.EXECUTION_FAILED,
                message="执行阶段失败",
                retryable=False,
            ),
        },
    ],
)
def test_tool_result_attempt_count_matches_execution_boundary(values) -> None:
    """成功、执行前失败和执行中失败必须与 handler 尝试次数一致。"""

    with pytest.raises(ValidationError, match="尝试|至少执行"):
        ToolResult.model_validate(values)


def test_prior_attempt_failure_must_have_been_retryable() -> None:
    """不可重试失败不得伪装成随后发生过网络恢复的历史。"""

    with pytest.raises(ValidationError, match="必须可重试"):
        ProviderAttemptFailure(
            failure=ProviderFailure(
                code=ProviderErrorCode.HTTP_ERROR,
                message="鉴权失败",
                retryable=False,
                status_code=401,
            ),
            latency_ms=1,
        )
