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
    """把 Kaiji 消息转换为 LangChain 消息；练习版需要补全角色映射。"""
    # TODO 5a：把 Kaiji 的 user/assistant/tool 消息转换为对应的三种
    # LangChain Message；ToolMessage 必须保留 tool_call_id。
    del messages
    return []


def build_search_tool(adapter: AzusaAdapter) -> StructuredTool:
    """构造复用 AzusaAdapter 的 LangChain 结构化搜索工具。"""

    def search_azusa(query: str, k: int = 3, mode: str = "hybrid") -> dict:
        return adapter.search_azusa(query, k, mode).model_dump()

    # TODO 5b：使用 StructuredTool.from_function 包装 search_azusa，并保留
    # SearchAzusaArgs Schema，不接受无类型字典。
    del search_azusa
    raise NotImplementedError("TODO 5b：构建 LangChain StructuredTool")


def build_search_runnable(adapter: AzusaAdapter):
    """把同一次搜索表达为 Tool → Runnable → 结构化输出。"""

    # TODO 5c：用 | 组合两个 RunnableLambda。第一个调用结构化工具，第二个
    # 校验 SearchResponse。
    del adapter
    raise NotImplementedError("TODO 5c：构建 Tool → Runnable → 输出校验")
