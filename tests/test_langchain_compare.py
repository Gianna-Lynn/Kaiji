"""验证 LangChain 消息、工具、Runnable 与 Callback 的明确教学契约。"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda, RunnableSequence
from pydantic import BaseModel, ValidationError

from kaiji.adapters.azusa import MockAzusaAdapter, SearchResponse
from kaiji.azusa_tools import SearchAzusaArgs
from kaiji.langchain_compare import (
    RecordingCallback,
    build_search_runnable,
    build_search_tool,
    to_langchain_messages,
)
from kaiji.models import Message, ToolResult


class RecordingAdapter(MockAzusaAdapter):
    """记录结构化工具实际转发给 Adapter 的参数。
    RecordingAdapter: 额外记录实际收到的参数，用于验证转发
    MockAzusaAdapter: 接收 query/k/mode，返回合法 SearchResponse
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, int, str]] = []

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        # 每一次调用search_azusa, 都会在RecordingAdapter当中记录调用时传入的参数.
        self.calls.append((query, k, mode))
        return super().search_azusa(query, k, mode)


class InvalidSearchPayload(BaseModel):
    """模拟形状不符合 ``SearchResponse`` 的工具输出。"""

    unexpected: str     # 这个类应当拥有一个叫unexpected的字段, 而且应当是一个字符串.(不要因为类定义太单薄就看不懂.)
                        # 这个类是一个格式正确的pydantic对象. 但是SearchResponse要求{"hits": [...],"mode": "hybrid"}, 所以这个类是一个业务类型错误的搜索结果.


class InvalidOutputAdapter(MockAzusaAdapter):
    """故意返回可序列化但违反搜索响应契约的测试 Adapter。"""

    def search_azusa(self, query: str, k: int, mode: str) -> Any:
        return InvalidSearchPayload(unexpected="不是搜索响应") # 正常Adapter声明返回SearchResponse, 这里返回的类型是InvalidSearchPayload.


def _successful_tool_result() -> ToolResult:
    """构造用于消息映射测试的最小成功工具结果。"""

    return ToolResult(
        call_id="call-search-1",
        tool_name="search_azusa",
        ok=True,
        attempts=1,
        output={"hits": [], "mode": "hybrid"},
    )


def test_message_mapping_preserves_roles_content_and_tool_call_id() -> None:
    """三种 Kaiji 角色应映射到约定的 LangChain 消息类型。"""

    tool_result = _successful_tool_result()
    source = [
        Message(role="user", content="Kaiji 是什么？"),
        Message(role="assistant", content='{"name":"search_azusa"}'),
        Message(
            role="tool",
            content=tool_result.model_dump_json(),
            tool_result=tool_result,
        ),
    ]

    converted = to_langchain_messages(source)

    assert [type(message) for message in converted] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
    ]
    assert [message.content for message in converted] == [
        message.content for message in source
    ]
    assert converted[2].tool_call_id == "call-search-1"
    assert converted[1].tool_calls == []


@pytest.mark.parametrize("role", ["user", "assistant"]) # 装饰器, 提供了形参名"role", 值列表[...], 每个值都会依次注入到role形参中执行一次.
def test_non_tool_message_rejects_tool_result(role: str) -> None:
    """非 tool 角色不得偷偷携带结构化工具结果。"""

    message = Message(  # 使用了Pydantic的构造器, 但Pydantic的字段校验拦不住, 因为字段之间的组合不合逻辑(role不是tool, 却携带了tool_result).必须在to_langchain_messages 里手动 raise.
        role=role,
        content="不合法的角色载荷组合",
        tool_result=_successful_tool_result(),
    )

    with pytest.raises(ValueError): # 断言通过的条件: with块中的语句抛出ValueError
        to_langchain_messages([message])


def test_tool_message_without_result_is_rejected_instead_of_faking_call_id() -> None:
    """缺少结果的 tool 消息无法可靠生成 ``tool_call_id``。"""

    with pytest.raises(ValueError):
        to_langchain_messages([Message(role="tool", content="缺少结构化结果")])


def test_search_runnable_invokes_the_structured_tool_boundary(monkeypatch) -> None:
    """Runnable 必须复用 StructuredTool，不能绕过工具边界直调 Adapter。
    build_search_runnable有这样的语句: tool = build_search_tool(adapter), 
    monkeypatch可以把这里的tool换成间谍工具SpyTool, 不需要真的实现AzusaAdapter"""

    class SpyTool:
        """记录 Runnable 交给结构化工具的原始载荷。"""

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def invoke(self, payload: dict) -> dict:
            self.calls.append(payload)
            return SearchResponse(hits=[], mode=payload["mode"]).model_dump(
                mode="json"
            )

    spy = SpyTool()
    monkeypatch.setattr(
        "kaiji.langchain_compare.build_search_tool",
        lambda adapter: spy,
    )
    payload = {"query": "Kaiji", "k": 2, "mode": "hybrid"}

    result = build_search_runnable(MockAzusaAdapter()).invoke(payload)

    assert spy.calls == [payload] # 验证记录下来的"载荷历史记录"和传入的载荷一致.
    assert result == SearchResponse(hits=[], mode="hybrid") # 验证SpyTool返回的结果符合预期.


def test_search_runnable_keeps_two_explicit_lambda_stages() -> None:
    """调用工具与校验输出必须是两个可观察的 Runnable 阶段。"""

    runnable = build_search_runnable(MockAzusaAdapter())

    assert isinstance(runnable, RunnableSequence)
    assert len(runnable.steps) == 2
    assert all(isinstance(step, RunnableLambda) for step in runnable.steps)


def test_structured_tool_applies_defaults_and_forwards_explicit_arguments() -> None:
    """Schema 默认值和显式参数都应原样传给同一个 Adapter。"""

    adapter = RecordingAdapter()
    tool = build_search_tool(adapter)

    default_result = tool.invoke({"query": "Kaiji"}) # 测试 SearchAzusaArgs 是否自动补全了默认值3, 默认值hybrid. SearchAzusaArgs本身是一个Pydantic模型, 会校验传入参数, 自动补全默认值.
    explicit_result = tool.invoke({"query": "Azusa", "k": 1, "mode": "bm25"})

    assert adapter.calls == [   # 验证传入的参数符合预期.
        ("Kaiji", 3, "hybrid"),
        ("Azusa", 1, "bm25"),
    ]
    # 验证invoke后返回的结果, 和azusa.py/class MockAzusaAdapter中预先填充的示例答案保持一致.
    assert default_result["mode"] == "hybrid"
    assert explicit_result["hits"][0]["chunk_id"] == "azusa-001"
    schema = tool.args_schema.model_json_schema()
    assert schema["required"] == ["query"]
    assert schema["properties"]["k"]["default"] == 3
    assert schema["properties"]["mode"]["default"] == "hybrid"
    assert tool.args_schema is SearchAzusaArgs
    assert tool.name == "search_azusa"
    assert "证据" in tool.description


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "K"},
        {"query": "Kaiji", "k": 0},
        {"query": "Kaiji", "k": 21},
        {"query": "Kaiji", "mode": ""},
    ],
)
def test_structured_tool_rejects_arguments_outside_schema(payload: dict) -> None:
    """工具调用必须执行查询、数量和模式的参数边界校验。"""

    tool = build_search_tool(MockAzusaAdapter())

    with pytest.raises(ValidationError):
        tool.invoke(payload)


def test_runnable_validates_structured_output_and_emits_success_callbacks() -> None:
    """合法输出应恢复为 ``SearchResponse``，且回调完整结束。"""

    callback = RecordingCallback()
    runnable = build_search_runnable(MockAzusaAdapter())

    result = runnable.invoke(
        {"query": "Azusa", "k": 1, "mode": "hybrid"},
        config={"callbacks": [callback]},
    )

    assert isinstance(result, SearchResponse)   # 验证返回结果类型是SearchResponse
    assert result.hits[0].chunk_id == "azusa-001"  # 验证返回结果符合azusa.py/class MockAzusaAdapter中预先填充的示例答案
    assert callback.starts >= 1 #   # 以下三条验证正确开始, 正确结束, 无错误.
    assert callback.ends >= 1
    assert callback.errors == 0


def test_runnable_rejects_invalid_output_and_emits_error_callback() -> None:
    """可序列化不等于合法，最终 Pydantic 边界必须拒绝错误形状。"""

    callback = RecordingCallback()
    runnable = build_search_runnable(InvalidOutputAdapter())

    with pytest.raises(ValidationError):    # 使用一个错误的adapter, 传入参数, 预期: 整个Runnable Sequence开始的时候得到一次start, invoke_tool 得到一次start, 成功, 得到一次end, validate_output 开始得到一次start, 失败并得到一次error, 整个RunnableSequence失败得到一次error.
        runnable.invoke(    
            {"query": "Kaiji"},
            config={"callbacks": [callback]},
        )
    # 理论上, start, end, error的计数分别是3, 1, 2. 
    assert callback.starts >= 1
    assert callback.errors >= 1
    # assert callback.ends == 1
    # 不断言 ends 的精确数量：组合 Runnable 会把同一个 Callback 传给多个内外层组件，
    # 成功完成的组件可能产生 end，失败组件则产生 error。
    # 这些嵌套事件的数量属于框架实现细节，本测试只约束失败链出现 error。