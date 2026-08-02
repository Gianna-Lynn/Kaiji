"""定义模型决策协议和具有明确停止原因的原生 Agent 循环。"""

from __future__ import annotations
import json
from typing import Protocol

from kaiji.models import (
    Message,
    ModelAction,
    RunResult,
    StopReason,
    TraceEvent,
)
from kaiji.tools import ToolExecutor

# 继承 Protocol之后, 其他的类要是也实现了签名完全相同的decide方法, 就可以被当做是DecisionProvider.
class DecisionProvider(Protocol):
    """抽象模型决策边界，便于用真实模型或确定性脚本替换。"""

    def decide(self, messages: list[Message]) -> ModelAction: ...


class AgentRuntime:
    """执行原生单 Agent 循环，并保证循环、证据和停止原因有明确边界。"""

    def __init__(
        self,
        decision_provider: DecisionProvider,
        executor: ToolExecutor,
        *,
        max_steps: int = 4,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self._decision_provider = decision_provider
        self._executor = executor
        self._max_steps = max_steps

    def run(self, question: str) -> RunResult:
        """运行一次问题处理流程；练习版需要补全显式 Agent Loop。"""
        # TODO 4：实现显式 Agent Loop。
        #
        # region: 每一步必须按以下顺序处理：
        # 1. 向 DecisionProvider 请求 ModelAction，并记录 model_action Trace；
        # 2. 若为 final_answer：记录 stop(COMPLETED) Trace，以 COMPLETED 返回；
        # 3. 若为 tool_call，按顺序：
        #    a. 用 name+arguments 生成签名，若已出现过，记录 stop(DUPLICATE_CALL) Trace，
        #       以 DUPLICATE_CALL 返回；
        #    b. 追加 assistant 消息（内容是序列化后的工具调用）；
        #    c. 执行工具，记录 tool_result Trace；
        #    d. 追加 tool 消息（内容是序列化后的工具结果）；
        #    e. 若工具失败：记录 stop(TOOL_FAILED) Trace，以 TOOL_FAILED 返回；
        #    f. 若工具成功：结果加入 successful_tool_results，继续下一轮；
        # 4. 循环耗尽仍未返回：记录 stop(MAX_STEPS) Trace，以 MAX_STEPS 返回。
        #
        # 每条正常的退出路径都必须追加格式一致的 stop TraceEvent，并返回内部一致的 RunResult。
        # 测试就是这里的可执行契约。 
        # endregion
        successful_tool_results = []    # 保存可信证据, 包含仅 ok=True 的工具结果
        trace_list: list[TraceEvent] = []   # 用于完整运行审计, 包含每次模型动作、每次工具结果、停止事件
        message_list: list[Message] = [Message(role="user", content=question, tool_result=None)] # 用于下一轮模型输入, 包含用户问题、已执行的工具调用、工具执行结果
        seen_call_signatures: set[str] = set()  # 保存工具调用记录, 避免出现重复调用.
        # 注意区分, 容易混淆:
        # TraceEvent.data 是审计字典
        # Message.content 是下一轮模型读取的字符串。
        for step in range(1, self._max_steps + 1):

            # 先让模型干活一次. 模型可能返回调用工具请求, 或者最终回答.
            model_action: ModelAction = self._decision_provider.decide(messages=message_list) # 传入message_list, 模型就是在这里看到不断更新的聊天记录,和上下文的.
            trace_list.append(TraceEvent(
                    step=step,
                    event="model_action",
                    data=model_action.model_dump(mode="json"), # 这里的data表达: 模型这一轮决定做什么.序列化操作得到一个字典.
                )
            ) 
            # region 上面的data, 形如:
            # data={
            #     "kind": "tool_call", # 请求调用工具
            #     "tool_call": {
            #         "call_id": "call-1",
            #         "name": "search_azusa",
            #         "arguments": {
            #             "query": "什么是 RAG",
            #             "k": 3
            #         }
            #     }
            # }

            # data={
            #     "kind": "final_answer",   # 直接给出最终回答
            #     "final_answer": "最终回答内容"
            # }
            # endregion
            if model_action.kind == "final_answer":
                # 模型给出最终答案, 更新trace, 不再更新message, 返回RunResult.
                # 不再更新messages: messages 是"喂给模型继续决策的上下文". 最终回答是输出, 不参与后续决策, 不加入messages.
                trace_list.append(TraceEvent(
                        step=step,
                        event="stop",
                        data={"reason": StopReason.COMPLETED.value} # stop的data只说明这次运行结束的原因. 所有的stop trace的data统一为data={"reason": stop_reason.value}
                    )
                )
                return RunResult(
                    answer= model_action.final_answer,
                    stop_reason= StopReason.COMPLETED,
                    steps=step, # steps的语义:DecisionProvider.decide() 被调用的轮数。
                    messages=message_list, 
                    successful_tool_results=successful_tool_results,
                    trace=trace_list,
                )

            elif model_action.kind == "tool_call":
                tool_call = model_action.tool_call
                
                # 序列化操作json.dumps()把当前的这一次tool_call制成一个signature,是一个json字符串,记录了name+arguments.
                tool_call_data = tool_call.model_dump(mode="json")

                tool_call_signature = json.dumps(
                    {
                        "name": tool_call_data["name"],
                        "arguments": tool_call_data["arguments"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
   
                if tool_call_signature in seen_call_signatures: 
                    # 如果name+arguments 重复, 以 DUPLICATE_CALL 停止
                    # 使用集合seen_call_signatures来检查是否出现了name+arguments重复. 集合平均查找的复杂度是O(1).
                    trace_list.append(TraceEvent(
                            step=step,
                            event="stop",
                            data={"reason": StopReason.DUPLICATE_CALL.value}, # stop的data只说明这次运行结束的原因: 调用重复.
                        )
                    )
                    # 这里不再需要添加消息给模型看, 因为整个runtime马上就结束.
                    return RunResult(
                        answer=None,
                        stop_reason=StopReason.DUPLICATE_CALL,
                        steps=step,
                        messages=message_list, 
                        successful_tool_results=successful_tool_results,
                        trace=trace_list,
                    )
                else:
                    # 如果name+arguments 没有重复, 就加入"已经见过的调用"集合
                    seen_call_signatures.add(tool_call_signature)

                # 在执行工具之前, 加入 assistant 工具调用的信息.
                message_list.append(Message(
                        role="assistant",
                        content=tool_call.model_dump_json(), # 序列化规则2: Message.content：model_dump_json()，得到字符串
                        # tool_call=tool_call,
                        tool_result=None,
                    )
                )
                # region 上面的content形如:
                # {"call_id":"call-1","name":"calculate_discount","arguments":{"price":100,"percent":10}}
                # endregion
                # 工具执行: 模型提出要调用工具, 并给出想要调用工具的名字和参数等信息, 封装进tool_call
                tool_result = self._executor.execute(tool_call) 

                # 记录 tool_result Trace
                trace_list.append(TraceEvent(
                        step=step,
                        event="tool_result",
                        data=tool_result.model_dump(mode="json"),# 这里的data保存: 完整的结构化工具结果.序列化操作得到一个字典.
                    )
                )
                # region: 上面的data包括完整的结构化工具结果, .model_dump(mode="json")得到一个字典, 形如:
                # mode="json"把 ToolResult 里那些 JSON 不认识的 Python 类型转成 JSON 兼容的字符串和字典.
                # data={
                #     "call_id": "call-1",
                #     "tool_name": "search_azusa",
                #     "ok": True,
                #     "attempts": 1,
                #     "output": {
                #         "chunks": [...]
                #     },
                #     "error": None
                # }

                # {
                #     "call_id": "call-1",
                #     "tool_name": "search_azusa",
                #     "ok": False,
                #     "attempts": 1,
                #     "output": None,
                #     "error": {
                #         "code": "timeout",
                #         "message": "工具执行超时",
                #         "retryable": True
                #     }
                # }
                # endregion

                # 追加 tool结果消息
                message_list.append(Message(
                        role="tool",
                        content=tool_result.model_dump_json(), # 这里不能只写"某某工具执行成功, 还要把工具执行的结果也写进messages列表, 虽然 tool_result 字段中保存了对象，但真实模型适配器通常主要读取可序列化的消息内容 content.
                        tool_result=tool_result,
                    )
                )
                # region: 上面的content形如:
                # {"call_id":"call-1","tool_name":"calculate_discount","ok":true,"attempts":1,"output":{"final_price":"90"},"error":null}
                # {"call_id":"call-1","tool_name":"calculate_discount","ok":false,"attempts":1,"output":null,"error":{"code":"execution_failed","message":"服务暂时不可用","retryable":true}}
                # endregion
                if tool_result.ok: 
                    # 工具执行成功. 加入successful_tool_results列表, 继续下一轮循环.
                    successful_tool_results.append(tool_result)
                else: 
                    # 工具执行失败. 记录 stop Trace, 返回 RunResult, 退出循环
                    trace_list.append(TraceEvent(
                            step=step,
                            event="stop",
                            data={"reason": StopReason.TOOL_FAILED.value} # stop的data只表达: 工具执行失败，所以停止。
                        )
                    )
                    return RunResult(
                        answer=None,
                        stop_reason=StopReason.TOOL_FAILED,
                        steps=step,
                        messages=message_list,
                        successful_tool_results=successful_tool_results,
                        trace=trace_list,
                    )
            else:
                raise ValueError("model_action出现了final_answer和tool_call以外的非法值")
        
        # 经过的循环数已达最大值. 记录 stop Trace, 返回 RunResult, 退出循环
        trace_list.append(TraceEvent(
                step=self._max_steps, # 使用最大步数表达循环耗尽时完成的决策轮数
                event="stop",
                data={"reason": StopReason.MAX_STEPS.value}, # stop的data只表达: Runtime 达到了循环上限，所以停止。
            )
        )
        return RunResult(
            answer=None,
            stop_reason=StopReason.MAX_STEPS,
            steps=self._max_steps,
            messages=message_list,
            successful_tool_results=successful_tool_results,
            trace=trace_list,
        )
