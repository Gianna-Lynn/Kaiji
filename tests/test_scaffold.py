"""验证核心模型与静态约束无需进入未来 TODO 即可独立运行。"""

import pytest
from pydantic import ValidationError

from kaiji.demo import DiscountArgs, DiscountResult
from kaiji.models import ToolErrorCode, ToolFailure, ToolResult
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
