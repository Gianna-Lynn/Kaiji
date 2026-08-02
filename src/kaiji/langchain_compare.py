"""用同一 Azusa 契约对照 LangChain 的消息、工具、Runnable 与回调。"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import StructuredTool

from kaiji.adapters.azusa import AzusaAdapter, SearchResponse
from kaiji.azusa_tools import SearchAzusaArgs
from kaiji.models import Message


class RecordingCallback(BaseCallbackHandler):
    """记录最小回调计数，用于对照框架 Trace 与 Kaiji Trace。"""

    def __init__(self) -> None:
        self.starts = 0
        self.ends = 0
        self.errors = 0

    def on_chain_start(self, serialized: dict, inputs: dict, **kwargs: Any) -> None:
        self.starts += 1

    def on_chain_end(self, outputs: dict, **kwargs: Any) -> None:
        self.ends += 1

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        self.errors += 1


def to_langchain_messages(messages: list[Message]) -> list[BaseMessage]:
    """把 Kaiji 消息转换为教学用的 LangChain 有损映射。

    ``user`` 和 ``assistant`` 分别映射为 ``HumanMessage`` 与
    ``AIMessage``，并原样保留 ``content``。``tool`` 映射为
    ``ToolMessage``，其 ``tool_call_id`` 必须来自结构化
    ``tool_result.call_id``；缺少工具结果时无法建立调用配对，因此直接拒绝。

    该对照刻意不把 Kaiji 的 assistant JSON 内容还原为 LangChain 原生
    ``tool_calls``，也不改变 Kaiji 的消息模型。它用于观察框架类型差异，
    不是两个消息协议之间的无损转换器。
    """

    converted: list[BaseMessage] = []
    for message in messages:
        if message.role == "user":
            if message.tool_result is not None:
                raise ValueError("user 消息不能携带 tool_result")
            converted.append(HumanMessage(content=message.content))
        elif message.role == "assistant":
            if message.tool_result is not None:
                raise ValueError("assistant 消息不能携带 tool_result")
            converted.append(AIMessage(content=message.content))
        else:
            if message.tool_result is None:
                raise ValueError("tool 消息必须携带 tool_result 以提供 tool_call_id")
            converted.append(
                ToolMessage(
                    content=message.content,
                    tool_call_id=message.tool_result.call_id,
                )
            )
    return converted


def build_search_tool(adapter: AzusaAdapter) -> StructuredTool:
    """构造复用 AzusaAdapter 与 ``SearchAzusaArgs`` 契约的搜索工具。

    参数的长度、范围和默认值全部由 ``SearchAzusaArgs`` 校验；通过校验的
    ``query``、``k`` 与 ``mode`` 会原样转发给 Adapter。工具输出先转换为
    JSON 兼容字典，供后续 Runnable 在独立边界重新校验为 ``SearchResponse``。
    """

    def search_azusa(query: str, k: int = 3, mode: str = "hybrid") -> dict:
        return adapter.search_azusa(query, k, mode).model_dump(mode="json")

    return StructuredTool.from_function(
        func=search_azusa,
        name="search_azusa",
        description="从 Azusa 搜索可用于回答的证据。",
        args_schema=SearchAzusaArgs,
    )


def build_search_runnable(adapter: AzusaAdapter) -> Runnable:
    """把搜索表达为 Tool → Runnable → ``SearchResponse`` 校验管线。

    即使 Tool 返回了字典，管线也不会把它直接视为可信输出；最后一步会
    调用 Pydantic 重新校验。校验异常继续向调用方传播，并由 LangChain
    Callback 的错误事件观测，而不是被静默转换为一个伪造结果。
    """

    tool = build_search_tool(adapter)
    invoke_tool = RunnableLambda(lambda payload: tool.invoke(payload))
    validate_output = RunnableLambda(SearchResponse.model_validate)
    return invoke_tool | validate_output
