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
        """运行一次问题处理流程，直到回答或明确停止。"""

        messages = [Message(role="user", content=question)]
        successful_results = []
        trace = []
        seen_calls: set[str] = set()

        for step in range(1, self._max_steps + 1):
            action = self._decision_provider.decide(messages)
            trace.append(
                TraceEvent(
                    step=step,
                    event="model_action",
                    data=action.model_dump(mode="json"),
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
                )

            assert action.tool_call is not None
            signature = json.dumps(
                {
                    "name": action.tool_call.name,
                    "arguments": action.tool_call.arguments,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            if signature in seen_calls:
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
                    content=f"Call tool {action.tool_call.name}",
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
        )
