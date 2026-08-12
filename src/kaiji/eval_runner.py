"""把可执行 Agent Case 运行成保留原始结果的 Episode。"""

from __future__ import annotations

from collections.abc import Callable
import json
from time import perf_counter
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from kaiji.models import Message, RunResult, StopReason
from kaiji.runtime import AgentRuntime, DecisionProvider
from kaiji.tools import ToolExecutor


class AgentEvalCase(BaseModel):
    """定义真实 Runtime 可执行用例及确定性评分契约。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    semantic_group: str = Field(min_length=1)
    suite: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    scenario: str = Field(default="default", min_length=1)
    expected_stop_reason: StopReason
    expected_tool: str | None = None
    expected_arguments: dict = Field(default_factory=dict)
    required_answer_facts: list[str] = Field(default_factory=list)
    allowed_citation_ids: list[str] = Field(default_factory=list)
    expects_no_tool: StrictBool = False
    expects_retry_recovery: StrictBool = False
    max_steps: StrictInt = Field(default=4, ge=1)

    @field_validator("case_id", "semantic_group", "suite", "prompt", "scenario")
    @classmethod
    def text_boundaries_are_not_blank(cls, value: str) -> str:
        """身份、分组和模型提示不能只由空白组成。"""

        if not value.strip():
            raise ValueError("Agent Eval 文本边界不得为空白")
        return value

    @model_validator(mode="after")
    def expectations_are_consistent(self) -> AgentEvalCase:
        """拒绝工具预期、自相矛盾的无工具标记和重复引用编号。"""

        if self.expects_no_tool and self.expected_tool is not None:
            raise ValueError("expects_no_tool 用例不能同时指定 expected_tool")
        if not self.expects_no_tool and self.expected_tool is None:
            raise ValueError("用例必须明确指定 expected_tool 或 expects_no_tool")
        if self.expected_tool is not None and not self.expected_tool.strip():
            raise ValueError("expected_tool 不得为空白")
        if self.expected_arguments and self.expected_tool is None:
            raise ValueError("expected_arguments 必须配合 expected_tool 使用")
        if self.expects_retry_recovery and self.expected_tool is None:
            raise ValueError("重试恢复用例必须指定 expected_tool")
        if self.allowed_citation_ids and self.expected_tool is None:
            raise ValueError("引用用例必须指定提供证据的 expected_tool")
        if any(not fact.strip() for fact in self.required_answer_facts):
            raise ValueError("required_answer_facts 不得包含空白事实")
        if len(self.required_answer_facts) != len(set(self.required_answer_facts)):
            raise ValueError("required_answer_facts 不能重复")
        if any(not item.strip() for item in self.allowed_citation_ids):
            raise ValueError("allowed_citation_ids 不得包含空白标识")
        if len(self.allowed_citation_ids) != len(set(self.allowed_citation_ids)):
            raise ValueError("allowed_citation_ids 不能重复")
        try:
            json.dumps(self.expected_arguments, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected_arguments 必须是有限 JSON 值") from exc
        return self


class AgentEpisode(BaseModel):
    """保存一次不可丢失原始结果的 Agent 执行记录。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    suite: str = Field(min_length=1)
    repeat_index: StrictInt = Field(default=0, ge=0)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    result: RunResult

    @field_validator("run_id", "case_id", "suite")
    @classmethod
    def identifiers_are_not_blank(cls, value: str) -> str:
        """Episode 的持久化身份不能只由空白组成。"""

        if not value.strip():
            raise ValueError("Episode 标识不得为空白")
        return value


ProviderFactory = Callable[[AgentEvalCase], DecisionProvider]
ExecutorFactory = Callable[[AgentEvalCase], ToolExecutor]


class AgentEvalRunner:
    """为每个 case 创建隔离 Provider/Executor，并执行真实 Runtime。"""

    def __init__(
        self,
        provider_factory: ProviderFactory,
        executor_factory: ExecutorFactory,
        *,
        duplicate_guard: bool = True,
    ) -> None:
        self._provider_factory = provider_factory
        self._executor_factory = executor_factory
        self._duplicate_guard = duplicate_guard

    def run(self, case: AgentEvalCase, *, repeat_index: int = 0) -> AgentEpisode:
        """运行单条 case；工厂按 case 调用以避免故障状态跨 case 泄漏。"""

        if type(repeat_index) is not int or repeat_index < 0:
            raise ValueError("repeat_index 必须是真正的非负整数")
        started = perf_counter()
        runtime = AgentRuntime(
            self._provider_factory(case),
            self._executor_factory(case),
            max_steps=case.max_steps,
            duplicate_guard=self._duplicate_guard,
        )
        result = runtime.run(case.prompt)
        return AgentEpisode(
            run_id=f"{case.case_id}-{repeat_index}-{uuid4().hex}",
            case_id=case.case_id,
            suite=case.suite,
            repeat_index=repeat_index,
            latency_ms=max(0.0, (perf_counter() - started) * 1000),
            result=result,
        )


def first_tool_call(episode: AgentEpisode):
    """返回模型首次工具选择；没有工具动作时返回空值。"""

    for model_call in episode.result.model_calls:
        if model_call.action is not None and model_call.action.tool_call is not None:
            return model_call.action.tool_call
    return None


def tool_messages(episode: AgentEpisode) -> list[Message]:
    """返回进入对话历史的所有结构化工具结果消息。"""

    return [message for message in episode.result.messages if message.role == "tool"]
