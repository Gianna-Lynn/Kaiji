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

    # TODO 5a：逐条完成角色映射并返回新的 BaseMessage 列表。
    #
    # 必须维持的契约：
    # 1. 三种角色的 content 都原样保留；
    # 2. tool 的 tool_call_id 只能取自 message.tool_result.call_id；
    # 3. tool 缺少 tool_result 时抛 ValueError，不能伪造调用编号；
    # 4. user/assistant 携带 tool_result 时同样抛 ValueError；
    # 5. assistant.content 即使看起来像 JSON，也只是文本，不能据此伪造
    #    LangChain 原生 tool_calls。
    raise NotImplementedError("TODO 5a：实现 Kaiji 到 LangChain 的消息映射")


def build_search_tool(adapter: AzusaAdapter) -> StructuredTool:
    """构造复用 AzusaAdapter 与 ``SearchAzusaArgs`` 契约的搜索工具。

    参数的长度、范围和默认值全部由 ``SearchAzusaArgs`` 校验；通过校验的
    ``query``、``k`` 与 ``mode`` 会原样转发给 Adapter。工具输出先转换为
    JSON 兼容字典，供后续 Runnable 在独立边界重新校验为 ``SearchResponse``。
    """

    def search_azusa(query: str, k: int = 3, mode: str = "hybrid") -> dict:
        """调用 Adapter，并把 Pydantic 响应转换为 JSON 兼容字典。"""

        return adapter.search_azusa(query, k, mode).model_dump(mode="json")

    # TODO 5b：用 StructuredTool.from_function 包装上面的 search_azusa。
    # 工具名固定为 search_azusa，description 应说明它返回回答所需证据，
    # args_schema 必须直接复用 SearchAzusaArgs，不能复制一份近似 Schema。
    raise NotImplementedError("TODO 5b：构建 LangChain StructuredTool")


def build_search_runnable(adapter: AzusaAdapter) -> Runnable:
    """把搜索表达为 Tool → Runnable → ``SearchResponse`` 校验管线。

    即使 Tool 返回了字典，管线也不能把它直接视为可信输出；最后一步必须
    调用 Pydantic 重新校验。校验异常继续向调用方传播，并由 LangChain
    Callback 的错误事件观测，而不是被静默转换为一个伪造结果。
    """

    tool = build_search_tool(adapter)

    # TODO 5c：创建两个 RunnableLambda，并用 | 按顺序连接：
    # 1. 把调用方 payload 原样交给 tool.invoke；
    # 2. 用 SearchResponse.model_validate 校验工具返回的 JSON 兼容字典。
    # 最终返回值应是 SearchResponse，而不是 dict 或 JSON 字符串。
    raise NotImplementedError("TODO 5c：构建 Tool → Runnable → 输出校验")
