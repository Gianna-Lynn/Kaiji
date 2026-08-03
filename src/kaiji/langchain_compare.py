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
    """回调计数器，用于对照框架 Trace 与 Kaiji Trace。

    Callback（回调）是被动通知机制：
    把这个实例通过 config 交给 LangChain的 Runnable
    Runnable 在运行过程中会自动在合适的时机调用这些 on_* 方法:
    —链条启动时调 on_chain_start
    正常结束时调 on_chain_end
    出错时调on_chain_error
    你不需要主动去调用任何东西，只要覆写这几个方法、在方法里写自己的逻辑，剩下的由框架负责。
    """

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

    该对照刻意不把 Kaiji 的 assistant message content 里面的JSON 内容还原为 LangChain 原生
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
    msg_list= []
    for message in messages:
        if message.role == "user":
            if message.tool_result is not None:
                raise ValueError("user消息携带了tool_result")
            msg = HumanMessage(content = message.content)
        elif message.role == "assistant":
            if message.tool_result is not None:
                raise ValueError("assistant消息携带了tool_result")
            msg = AIMessage(content = message.content)
        else:  # message.role == "tool"
            if message.tool_result is None:
                raise ValueError("tool消息没有携带tool_result")
            tool_call_id = message.tool_result.call_id
            msg = ToolMessage(content = message.content, tool_call_id = tool_call_id)

        msg_list.append(msg)
    return msg_list


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
    # StructuredTool 对应 ToolDefinition 中的名称、说明、处理函数和输入模型，并在 invoke() 时使用 args_schema 校验参数。
    # 与 _prepare_call 不同，它不负责 Registry 名称查找，也不会把参数校验失败转换成结构化 ToolResult；
    # 当前校验失败会以 ValidationError 向调用方传播。
    # 输出模型校验由后续 RunnableLambda(SearchResponse.model_validate) 负责；
    # 超时、有限重试和结构化执行失败仍属于在原生 Kaiji 架构中 ToolExecutor 的职责。
    #  工具名固定为 search_azusa，description 应说明它返回回答所需证据，
    # args_schema 必须直接复用 SearchAzusaArgs，不能复制一份近似 Schema。

    return StructuredTool.from_function(
        func=search_azusa, 
        name="search_azusa",
        description="返回回答所需证据",
        args_schema=SearchAzusaArgs, # args_schema: 工具的输入参数契约, 是一个Pydantic模型类. 传入SearchAzusaArgs这个类本身, 而不是实例. LangChain会在 tool.invoke(payload) 时用这个类校验输入, 不像 tools.py中需要显式调用.model_validate
    )


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

    def invoke_tool_fn(payload: dict) -> dict:
        # langchain的工具调用是 StructuredTool.invoke(), 
        # 不包含tools.py/_execute_with_retry()中提供的功能: 超时、重试、错误分类、
        # 不包含runtime.py/AgentRuntime.run()中提供的功能: 循环上限、停止原因、积累可信证据。
        return tool.invoke(payload)
    
    invoke_tool = RunnableLambda(invoke_tool_fn) # 把普通Python函数包装成Runnable.
    validate_output = RunnableLambda(SearchResponse.model_validate)
    return invoke_tool | validate_output # 框架提供Runnable的目的, 是为了把.invoke和.model_validate组合起来, 成为一个可组合、可调用、可配置、可观测的管线组件。