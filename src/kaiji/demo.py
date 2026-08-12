"""提供无需 API key 的确定性原生 Agent Runtime 演示和三种安全工具。"""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from pydantic import BaseModel, Field

from kaiji.models import (
    Message,
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ToolCall,
)
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolDefinition, ToolExecutor, ToolRegistry


class DiscountArgs(BaseModel):
    """折扣计算工具的输入参数。"""

    price: Decimal = Field(gt=0, description="折扣前的原价，必须大于零。")
    percent: Decimal = Field(
        ge=0,
        le=100,
        description="从原价中减免的百分比；九折表示减免 10，而不是 90。",
    )


class DiscountResult(BaseModel):
    """折扣计算工具的结构化输出。"""

    final_price: Decimal


class SearchArgs(BaseModel):
    """本地搜索工具的查询和返回数量。"""

    query: str = Field(min_length=2)
    k: int = Field(default=3, ge=1, le=10)


class SearchResult(BaseModel):
    """本地搜索工具返回的匹配文本。"""

    matches: list[str]


class ProfileArgs(BaseModel):
    """模拟用户资料工具的用户编号。"""

    user_id: str = Field(pattern=r"^user-\d+$")


class ProfileResult(BaseModel):
    """模拟用户资料工具返回的显示名称。"""

    display_name: str


class ScriptedDecisionProvider:
    """提供确定性模型替身，使 Runtime 无需 API key 即可运行。"""

    def __init__(self, actions: list[ModelAction]) -> None:
        self._actions = deque(actions)

    def decide(self, messages: list[Message]) -> ModelCallResult:
        """忽略消息内容，按预设顺序返回下一项模型动作。"""

        del messages
        return ModelCallResult(
            action=self._actions.popleft(),
            metadata=ModelCallMetadata(model="scripted", latency_ms=0),
        )


def build_registry() -> ToolRegistry:
    """构建包含计算、检索和资料读取三种语义的安全工具注册表。"""

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="calculate_discount",
            description="根据原价和减免百分比计算最终价格。",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=lambda args: {
                "final_price": args.price * (Decimal(100) - args.percent) / 100
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="local_search",
            description=(
                "搜索演示用的极小本地关键词知识库；结果没有稳定证据编号，不能作为引用。"
            ),
            arguments_model=SearchArgs,
            result_model=SearchResult,
            handler=lambda args: {
                "matches": [
                    item
                    for item in ["Kaiji uses tools", "Azusa provides retrieval"]
                    if args.query.lower() in item.lower()
                ][: args.k]
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_profile",
            description="读取一条模拟用户资料。",
            arguments_model=ProfileArgs,
            result_model=ProfileResult,
            handler=lambda args: {"display_name": f"Passenger {args.user_id[5:]}"},
        )
    )
    return registry


def main() -> None:
    """运行一次折扣工具调用与最终回答的离线演示。"""

    provider = ScriptedDecisionProvider(
        [
            ModelAction(
                kind="tool_call",
                tool_call=ToolCall(
                    call_id="demo-1",
                    name="calculate_discount",
                    arguments={"price": "120", "percent": "25"},
                ),
            ),
            ModelAction(kind="final_answer", final_answer="The final price is 90."),
        ]
    )
    runtime = AgentRuntime(provider, ToolExecutor(build_registry()))
    result = runtime.run("What is 25% off 120?")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
