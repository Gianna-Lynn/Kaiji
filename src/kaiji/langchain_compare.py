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
    def search_azusa(query: str, k: int = 3, mode: str = "hybrid") -> dict:
        return adapter.search_azusa(query, k, mode).model_dump()

    return StructuredTool.from_function(
        func=search_azusa,
        name="search_azusa",
        description="Search Azusa for evidence.",
        args_schema=SearchAzusaArgs,
    )


def build_search_runnable(adapter: AzusaAdapter):
    """The same call expressed as Tool -> Runnable -> structured output."""

    tool = build_search_tool(adapter)
    invoke_tool = RunnableLambda(lambda payload: tool.invoke(payload))
    validate_output = RunnableLambda(SearchResponse.model_validate)
    return invoke_tool | validate_output
