"""用同一 Azusa 契约对照 LangChain 的消息、工具、Runnable 与回调。"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from kaiji.adapters.azusa import AzusaAdapter, SearchResponse
from kaiji.azusa_tools import SearchAzusaArgs
from kaiji.models import Message


class LCDecision(BaseModel):
    """表示 LangChain 对照流程中的搜索或回答决策。"""

    kind: Literal["search", "answer"]
    query: str | None = None
    answer: str | None = None


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


def to_langchain_messages(messages: list[Message]) -> list:
    """把 Kaiji 的三种消息角色转换为对应的 LangChain Message。"""

    converted = []
    for message in messages:
        if message.role == "user":
            converted.append(HumanMessage(content=message.content))
        elif message.role == "assistant":
            converted.append(AIMessage(content=message.content))
        else:
            call_id = (
                message.tool_result.call_id if message.tool_result else "unknown-call"
            )
            converted.append(ToolMessage(content=message.content, tool_call_id=call_id))
    return converted


def build_search_tool(adapter: AzusaAdapter) -> StructuredTool:
    """构造复用 AzusaAdapter 和 Pydantic Schema 的结构化搜索工具。"""

    def search_azusa(query: str, k: int = 3, mode: str = "hybrid") -> dict:
        return adapter.search_azusa(query, k, mode).model_dump()

    return StructuredTool.from_function(
        func=search_azusa,
        name="search_azusa",
        description="从 Azusa 搜索可用于回答的证据。",
        args_schema=SearchAzusaArgs,
    )


def build_search_runnable(adapter: AzusaAdapter):
    """把同一次搜索表达为 Tool → Runnable → 结构化输出。"""

    tool = build_search_tool(adapter)
    invoke_tool = RunnableLambda(lambda payload: tool.invoke(payload))
    validate_output = RunnableLambda(SearchResponse.model_validate)
    return invoke_tool | validate_output
