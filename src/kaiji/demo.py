from __future__ import annotations

from collections import deque
from decimal import Decimal

from pydantic import BaseModel, Field

from kaiji.models import Message, ModelAction, ToolCall
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolDefinition, ToolExecutor, ToolRegistry


class DiscountArgs(BaseModel):
    price: Decimal = Field(gt=0)
    percent: Decimal = Field(ge=0, le=100)


class DiscountResult(BaseModel):
    final_price: Decimal


class SearchArgs(BaseModel):
    query: str = Field(min_length=2)
    k: int = Field(default=3, ge=1, le=10)


class SearchResult(BaseModel):
    matches: list[str]


class ProfileArgs(BaseModel):
    user_id: str = Field(pattern=r"^user-\d+$")


class ProfileResult(BaseModel):
    display_name: str


class ScriptedDecisionProvider:
    """A deterministic model stand-in so the runtime works without API keys."""

    def __init__(self, actions: list[ModelAction]) -> None:
        self._actions = deque(actions)

    def decide(self, messages: list[Message]) -> ModelAction:
        del messages
        return self._actions.popleft()


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="calculate_discount",
            description="Calculate a price after a percentage discount.",
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
            description="Search a tiny local knowledge base.",
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
            description="Read a mock user profile.",
            arguments_model=ProfileArgs,
            result_model=ProfileResult,
            handler=lambda args: {"display_name": f"Passenger {args.user_id[5:]}"},
        )
    )
    return registry


def main() -> None:
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
