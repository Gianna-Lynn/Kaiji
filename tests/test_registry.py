import pytest

from kaiji.demo import DiscountArgs, DiscountResult
from kaiji.tools import ToolDefinition, ToolRegistry


def make_definition(name: str = "discount") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="test tool",
        arguments_model=DiscountArgs,
        result_model=DiscountResult,
        handler=lambda args: {"final_price": args.price},
    )


def test_registry_exposes_schema_and_lookup() -> None:
    registry = ToolRegistry()
    definition = make_definition()

    registry.register(definition)

    assert registry.get("discount") is definition
    assert registry.schemas()[0]["parameters"]["required"] == ["price", "percent"]


def test_registry_rejects_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(make_definition())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(make_definition())
