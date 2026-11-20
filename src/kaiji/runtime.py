"""定义模型决策协议和具有明确停止原因的原生 Agent 循环。"""

from __future__ import annotations

import json
from typing import Protocol

from kaiji.models import (
    Message,
    ModelCallMetadata,
    ModelCallResult,
    ModelUsage,
    ProviderErrorCode,
    ProviderFailure,
    RunResult,
    StopReason,
    TraceEvent,
)
from kaiji.tools import ToolExecutor


class DecisionProvider(Protocol):
    """抽象模型决策边界,便于用真实模型或确定性脚本替换。

    Runtime 只依赖 decide() 获得模型的下一步动作或最终回答,
    实现方可以是真实模型客户端,也可以是确定性脚本。
    """

    def decide(self, messages: list[Message]) -> ModelCallResult:
        """根据当前对话历史返回模型的下一步动作或最终回答。

        Args:
            messages: 模型当前可见的完整对话历史。

        Returns:
            携带动作(action)或 Provider 失败(failure)的 ModelCallResult;
            Runtime 先检查 failure,失败时 action 不会被使用。
        """
        ...


class AgentRuntime:
    """执行原生单 Agent 循环,保证循环、证据和停止原因有明确边界。

    每个逻辑步骤由 DecisionProvider 决定动作:最终回答、工具调用或
    失败都会停止;循环受 max_steps 上限约束,每种停止路径都写入
    trace 并携带明确的 StopReason。
    """

    def __init__(
        self,
        decision_provider: DecisionProvider,
        executor: ToolExecutor,
        *,
        max_steps: int = 4,
        duplicate_guard: bool = True,
    ) -> None:
        """初始化 Runtime。

        Args:
            decision_provider: 提供模型下一步决策的来源。
            executor: 负责工具执行、校验与有界重试的执行器。
            max_steps: 循环的最大步数,必须为正整数。
            duplicate_guard: 为 True 时,完全相同的工具调用只允许执行一次。

        Raises:
            ValueError: max_steps 不是正整数。
            TypeError: duplicate_guard 不是 bool。
        """

        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps 必须是真正的正整数")
        if type(duplicate_guard) is not bool:
            raise TypeError("duplicate_guard 必须是真正的 bool")
        self._decision_provider = decision_provider
        self._executor = executor
        self._max_steps = max_steps
        self._duplicate_guard = duplicate_guard

    def run(self, question: str) -> RunResult:
        """运行一次问题处理流程,直到模型回答或触发明确停止。

        run(question)
        +-- 入参无效 -> 抛出 ValueError
        `-- 初始化消息、Trace 和成功结果
            +-- 还有步数 -> Provider 决策（每轮一步）
            |   +-- 失败或异常 -> model_failed
            |   +-- 最终回答 -> completed
            |   `-- 工具调用（按顺序检查）
            |       +-- call_id 已使用 -> model_failed
            |       +-- 判重开启且工具名和参数重复 -> duplicate_call
            |       `-- Executor 执行，回填消息和 Trace
            |           +-- 失败 -> tool_failed
            |           `-- 成功 -> 保存结果，回到步数检查
            `-- 步数用尽 -> max_steps

        Args:
            question: 用户的初始问题,必须为非空字符串。

        Returns:
            携带最终答案(可能为 None)、停止原因、步数、消息历史、
            成功工具结果、trace 与模型调用记录的 RunResult。

        Raises:
            ValueError: question 为空或不是字符串。
        """

        # ── 阶段 1/4:入参校验与状态容器初始化 ──
        # 各容器的职责:
        # messages           传给 Provider 的完整对话历史(用户问题 + 模型/工具响应)
        # successful_results 仅保存成功且经过校验的工具结果,是最终答案的证据来源
        # trace              运行事件序列(model_action/tool_result/model_failure/stop)
        # model_calls        每个逻辑步骤的模型调用记录,含失败、延迟、Token 元数据
        # seen_calls         已执行过的"名称+参数"签名,防止完全相同调用反复执行
        # seen_call_ids      已出现过的 tool_call_id,防止不同调用复用同一 ID
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question 必须是非空字符串")
        messages = [Message(role="user", content=question)]
        successful_results = []
        trace = []
        model_calls = []
        seen_calls: set[str] = set()
        seen_call_ids: set[str] = set()

        # ── 阶段 2/4:循环内 · 模型决策(每轮执行) ──
        for step in range(1, self._max_steps + 1):
            # 步骤 2.1:询问 Provider 下一步动作;未预期异常不击穿 Runtime,
            # 收敛为 INTERNAL_ERROR 失败,统一走下面的 Provider 失败分支。
            try:
                model_call = self._decision_provider.decide(messages)
            except Exception as exc:
                model_call = ModelCallResult(
                    failure=ProviderFailure(
                        code=ProviderErrorCode.INTERNAL_ERROR,
                        message=f"Provider 未捕获异常：{type(exc).__name__}",
                        retryable=False,
                    ),
                    metadata=ModelCallMetadata(
                        model="unknown",
                        latency_ms=0,
                        usage=ModelUsage(),
                    ),
                )
            model_calls.append(model_call)

            # 分支 2a:Provider 失败 → 记录 model_failure + stop,
            # 以 MODEL_FAILED 停止,动作(action)不再处理。
            if model_call.failure is not None:
                trace.append(
                    TraceEvent(
                        step=step,
                        event="model_failure",
                        data=model_call.model_dump(mode="json"),
                    )
                )
                trace.append(
                    TraceEvent(
                        step=step,
                        event="stop",
                        data={"reason": StopReason.MODEL_FAILED},
                    )
                )
                return RunResult(
                    answer=None,
                    stop_reason=StopReason.MODEL_FAILED,
                    steps=step,
                    messages=messages,
                    successful_tool_results=successful_results,
                    trace=trace,
                    model_calls=model_calls,
                )

            # 分支 2b:Provider 成功 → 记录 model_action 事件,进入阶段 3。
            # 契约保证 failure 为 None 时 action 一定存在,assert 仅作防御。
            action = model_call.action
            assert action is not None
            trace.append(
                TraceEvent(
                    step=step,
                    event="model_action",
                    data=model_call.model_dump(mode="json"),
                )
            )

            # ── 阶段 3/4:循环内 · 动作执行(每轮一次) ──
            # 分支 3a:模型给出最终回答 → 记录 stop,以 COMPLETED 停止,
            # 回答内容随 RunResult 返回。
            if action.kind == "final_answer":
                trace.append(
                    TraceEvent(
                        step=step,
                        event="stop",
                        data={"reason": StopReason.COMPLETED},
                    )
                )
                return RunResult(
                    answer=action.final_answer,
                    stop_reason=StopReason.COMPLETED,
                    steps=step,
                    messages=messages,
                    successful_tool_results=successful_results,
                    trace=trace,
                    model_calls=model_calls,
                )

            # 分支 3b:模型给出工具调用(契约保证此时 tool_call 非 None,
            # assert 仅作防御)。先做两层重复防护,再执行工具。
            assert action.tool_call is not None
            # 子分支 3b-1:tool_call_id 已出现过 → 按模型无效响应处理,
            # 记录 model_failure + stop,以 MODEL_FAILED 停止。
            if action.tool_call.call_id in seen_call_ids:
                trace.append(
                    TraceEvent(
                        step=step,
                        event="model_failure",
                        data={
                            "code": ProviderErrorCode.INVALID_RESPONSE,
                            "message": "模型重复使用了 tool_call_id",
                            "call_id": action.tool_call.call_id,
                        },
                    )
                )
                trace.append(
                    TraceEvent(
                        step=step,
                        event="stop",
                        data={"reason": StopReason.MODEL_FAILED},
                    )
                )
                return RunResult(
                    answer=None,
                    stop_reason=StopReason.MODEL_FAILED,
                    steps=step,
                    messages=messages,
                    successful_tool_results=successful_results,
                    trace=trace,
                    model_calls=model_calls,
                )
            seen_call_ids.add(action.tool_call.call_id)
            # 子分支 3b-2:重复调用防护 —— 用"名称+参数"的规范化 JSON
            # 作为判定签名;duplicate_guard 开启且签名已出现时停止。
            signature = json.dumps(
                {
                    "name": action.tool_call.name,
                    "arguments": action.tool_call.arguments,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            if self._duplicate_guard and signature in seen_calls:
                # 记录 stop,以 DUPLICATE_CALL 停止。
                trace.append(
                    TraceEvent(
                        step=step,
                        event="stop",
                        data={"reason": StopReason.DUPLICATE_CALL},
                    )
                )
                return RunResult(
                    answer=None,
                    stop_reason=StopReason.DUPLICATE_CALL,
                    steps=step,
                    messages=messages,
                    successful_tool_results=successful_results,
                    trace=trace,
                    model_calls=model_calls,
                )
            seen_calls.add(signature)
            # 子分支 3b-3:执行工具并回填状态。记录 tool_result 事件,
            # 并把 assistant 调用与 tool 结果放回消息历史,
            # 供下一轮 Provider 决策时可见。
            result = self._executor.execute(action.tool_call)
            trace.append(
                TraceEvent(
                    step=step,
                    event="tool_result",
                    data=result.model_dump(mode="json"),
                )
            )
            messages.append(
                Message(
                    role="assistant",
                    content=action.assistant_content,
                    tool_call=action.tool_call,
                )
            )
            messages.append(
                Message(
                    role="tool",
                    content=result.model_dump_json(),
                    tool_result=result,
                )
            )

            # 子分支 3b-3 失败:工具执行失败 → 记录 stop,以 TOOL_FAILED
            # 停止;失败结果不进 successful_results(证据只收成功结果)。
            if not result.ok:
                trace.append(
                    TraceEvent(
                        step=step,
                        event="stop",
                        data={"reason": StopReason.TOOL_FAILED},
                    )
                )
                return RunResult(
                    answer=None,
                    stop_reason=StopReason.TOOL_FAILED,
                    steps=step,
                    messages=messages,
                    successful_tool_results=successful_results,
                    trace=trace,
                    model_calls=model_calls,
                )

            # 子分支 3b-3 成功:保存证据,回到循环头部进入下一轮。
            successful_results.append(result)

        # ── 阶段 4/4:循环耗尽 ──
        # 达到 max_steps 仍无最终回答或失败 → 记录 stop,以 MAX_STEPS 停止。
        trace.append(
            TraceEvent(
                step=self._max_steps,
                event="stop",
                data={"reason": StopReason.MAX_STEPS},
            )
        )
        return RunResult(
            answer=None,
            stop_reason=StopReason.MAX_STEPS,
            steps=self._max_steps,
            messages=messages,
            successful_tool_results=successful_results,
            trace=trace,
            model_calls=model_calls,
        )
