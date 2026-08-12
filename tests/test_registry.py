"""验证工具注册表的唯一命名、查找和 JSON Schema 暴露。"""

import pytest

from kaiji.demo import DiscountArgs, DiscountResult
from kaiji.tools import ToolDefinition, ToolRegistry


def make_definition(name: str = "discount") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="测试工具",
        arguments_model=DiscountArgs,
        result_model=DiscountResult,
        handler=lambda args: {"final_price": args.price},
    )


def test_registry_exposes_schema_and_lookup() -> None:
    registry = ToolRegistry()
    definition = make_definition()

    registry.register(definition)

    assert registry.get("discount") is definition
    parameters = registry.schemas()[0]["parameters"]
    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["price", "percent"]
    assert "减免" in parameters["properties"]["percent"]["description"]
    assert "九折" in parameters["properties"]["percent"]["description"]


def test_registry_rejects_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(make_definition())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(make_definition())


def test_registry_definitions_preserve_order_without_exposing_mutable_container() -> (
    None
):
    """组合 Registry 可读取定义，但不能取得内部可变字典。"""

    registry = ToolRegistry()
    first = make_definition("first")
    second = make_definition("second")
    registry.register(first)
    registry.register(second)

    assert registry.definitions() == (first, second)


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("name", "   "),
        ("description", "   "),
        ("timeout_seconds", True),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("max_retries", True),
    ],
)
def test_definition_rejects_ambiguous_contract_values(field, bad) -> None:
    values = {
        "name": "discount",
        "description": "测试工具",
        "arguments_model": DiscountArgs,
        "result_model": DiscountResult,
        "handler": lambda args: {"final_price": args.price},
    }
    values[field] = bad
    with pytest.raises((TypeError, ValueError)):
        ToolDefinition(**values)
