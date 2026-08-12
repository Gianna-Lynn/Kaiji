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
    """抽象模型决策边界，便于用真实模型或确定性脚本替换。"""

    def decide(self, messages: list[Message]) -> ModelCallResult: ...


class AgentRuntime:
    """执行原生单 Agent 循环，并保证循环、证据和停止原因有明确边界。"""

    def __init__(
        self,
        decision_provider: DecisionProvider,
        executor: ToolExecutor,
        *,
        max_steps: int = 4,
        duplicate_guard: bool = True,
    ) -> None:
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps 必须是真正的正整数")
        if type(duplicate_guard) is not bool:
            raise TypeError("duplicate_guard 必须是真正的 bool")
        self._decision_provider = decision_provider
        self._executor = executor
        self._max_steps = max_steps
        self._duplicate_guard = duplicate_guard

    def run(self, question: str) -> RunResult:
        """运行一次问题处理流程，直到回答或明确停止。"""

        if not isinstance(question, str) or not question.strip():
            raise ValueError("question 必须是非空字符串")
        messages = [Message(role="user", content=question)]
        successful_results = []
        trace = []
        model_calls = []
        seen_calls: set[str] = set()
        seen_call_ids: set[str] = set()

        for step in range(1, self._max_steps + 1):
            try:
                model_call = self._decision_provider.decide(messages)
            except Exception as exc:  # Provider 未预期异常也不能击穿 Runtime。
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

            action = model_call.action
            assert action is not None
            trace.append(
                TraceEvent(
                    step=step,
                    event="model_action",
                    data=model_call.model_dump(mode="json"),
                )
            )

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

            assert action.tool_call is not None
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
            signature = json.dumps(
                {
                    "name": action.tool_call.name,
                    "arguments": action.tool_call.arguments,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            if self._duplicate_guard and signature in seen_calls:
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

            successful_results.append(result)

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
