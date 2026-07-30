"""定义模型决策协议和具有明确停止原因的原生 Agent 循环。"""

from __future__ import annotations

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
        """运行一次问题处理流程；练习版需要补全显式 Agent Loop。"""
        # TODO 4：实现显式 Agent Loop。
        #
        # 每一步必须按以下顺序处理：
        # 1. 向 DecisionProvider 请求 ModelAction 并记录 Trace；
        # 2. 遇到最终回答时返回 COMPLETED；
        # 3. 执行工具、记录结果，并追加 assistant/tool 消息；
        # 4. ToolResult 不成功时返回 TOOL_FAILED；
        # 5. 只有 ok=True 的结果才能加入 successful_tool_results；
        # 6. name+arguments 重复时以 DUPLICATE_CALL 停止；
        # 7. 完成 max_steps 次工具迭代后返回 MAX_STEPS。
        #
        # 每条退出路径都必须追加 stop TraceEvent，并返回内部一致的 RunResult。
        # 测试就是这里的可执行契约。
        return RunResult(
            answer=None,
            stop_reason=StopReason.MAX_STEPS,
            steps=0,
            messages=[Message(role="user", content=question)],
            successful_tool_results=[],
            trace=[
                TraceEvent(
                    step=0,
                    event="stop",
                    data={"reason": StopReason.MAX_STEPS},
                )
            ],
        )
