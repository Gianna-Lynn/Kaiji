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
    kind: Literal["search", "answer"]
    query: str | None = None
    answer: str | None = None


class RecordingCallback(BaseCallbackHandler):
    """Tiny local callback used to compare framework tracing with Kaiji Trace."""

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
    # TODO 5a: Convert Kaiji user/assistant/tool messages to the three matching
    # LangChain message classes. ToolMessage must retain tool_call_id.
    del messages
    return []


def build_search_tool(adapter: AzusaAdapter) -> StructuredTool:
    def search_azusa(query: str, k: int = 3, mode: str = "hybrid") -> dict:
        return adapter.search_azusa(query, k, mode).model_dump()

    # TODO 5b: Wrap search_azusa with StructuredTool.from_function. Preserve
    # the SearchAzusaArgs schema instead of accepting an untyped dict.
    del search_azusa
    raise NotImplementedError("TODO 5b: build the LangChain StructuredTool")


def build_search_runnable(adapter: AzusaAdapter):
    """The same call expressed as Tool -> Runnable -> structured output."""

    # TODO 5c: Compose two RunnableLambda objects with |. The first invokes the
    # structured tool; the second validates SearchResponse.
    del adapter
    raise NotImplementedError("TODO 5c: build Tool -> Runnable -> output validation")
