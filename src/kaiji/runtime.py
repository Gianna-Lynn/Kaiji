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
    def decide(self, messages: list[Message]) -> ModelAction: ...


class AgentRuntime:
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
        # TODO 4: Implement the explicit Agent Loop.
        #
        # Required order per step:
        # 1. ask DecisionProvider for a ModelAction and trace it;
        # 2. return COMPLETED for a final answer;
        # 3. execute a tool call, trace it, and append assistant/tool messages;
        # 4. return TOOL_FAILED when the ToolResult is not ok;
        # 5. append only ok results to successful_tool_results;
        # 6. after exactly max_steps tool iterations, return MAX_STEPS.
        #
        # Every exit path must append a "stop" TraceEvent and return a
        # self-consistent RunResult. Use the tests as the executable contract.
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
