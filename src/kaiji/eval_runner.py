"""把可执行 Agent Case 运行成保留原始结果的 Episode。"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from uuid import uuid4
import json

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
from kaiji.runtime import DecisionProvider, AgentRuntime
from kaiji.tools import ToolExecutor


class AgentEvalCase(BaseModel):
    """定义真实 Runtime 可执行用例及确定性评分契约。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)                      # 用例唯一标识。
    semantic_group: str = Field(min_length=1)               # 语义测试分组。
    suite: str = Field(min_length=1)                        # 所属测试套件。
    prompt: str = Field(min_length=1)                       # 提供给 Agent 的用户提示。
    scenario: str = Field(default="default", min_length=1)  # 执行场景名称。
    expected_stop_reason: StopReason                        # 预期的停止原因。
    expected_tool: str | None = None                        # 预期调用的工具名称。
    expected_arguments: dict = Field(default_factory=dict)  # 预期的工具参数。
    required_answer_facts: list[str] = Field(default_factory=list)  # 答案必须包含的事实。
    allowed_citation_ids: list[str] = Field(default_factory=list)   # 允许引用的证据编号。
    expects_no_tool: StrictBool = False                             # 是否预期不调用工具。
    expects_retry_recovery: StrictBool = False                      # 是否预期重试后恢复成功。
    max_steps: StrictInt = Field(default=4, ge=1)                   # Agent 最多执行的步数。

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
    """保存一次 Agent 执行的完整记录，确保运行结果及其上下文可追溯。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)                    # 本次运行的唯一标识。
    case_id: str = Field(min_length=1)                   # 对应评测用例的标识。
    suite: str = Field(min_length=1)                     # 所属评测套件名称。
    repeat_index: StrictInt = Field(default=0, ge=0)     # 同一用例的重复运行序号。
    latency_ms: float = Field(ge=0, allow_inf_nan=False) # 本次运行的耗时（毫秒）。
    result: RunResult                                    # 未删减的 Runtime 运行结果。

    @field_validator("run_id", "case_id", "suite")
    @classmethod
    def identifiers_are_not_blank(cls, value: str) -> str:
        """Episode 的持久化身份不能只由空白组成。"""

        if not value.strip():
            raise ValueError("Episode 标识不得为空白")
        return value

# 语法上：Callable[[参数类型列表], 返回类型] 表示“赋值号左边这个可调用对象的类型”；
# 右侧的 [AgentEvalCase] 是一个参数类型列表，里面只包含一个元素, 这个元素的数据类型是AgentEvalCase.
# 逗号后的 DecisionProvider 表示该可调用对象返回 DecisionProvider。
ProviderFactory = Callable[[AgentEvalCase], DecisionProvider]
ExecutorFactory = Callable[[AgentEvalCase], ToolExecutor]

class AgentEvalRunner:
    """为每个 case 创建隔离 Provider/Executor，并执行真实 Runtime。"""

    def __init__(
        self,
        provider_factory: ProviderFactory,  # 按用例创建决策提供器。
        executor_factory: ExecutorFactory,  # 按用例创建工具执行器。
        *,  
        duplicate_guard: bool = True,       # 是否启用重复调用防护。
    ) -> None:
        self._provider_factory = provider_factory
        self._executor_factory = executor_factory
        self._duplicate_guard = duplicate_guard

    def run(self, case: AgentEvalCase, *, repeat_index: int = 0) -> AgentEpisode:
        """运行单条评测用例, 返回完整的执行记录Episode。

        每次运行都会根据当前用例重新创建决策提供器和工具执行器，以隔离不同用例之间的状态.
        返回值Episode包含唯一运行标识、重复序号、墙钟耗时以及未经删减的 Runtime 结果。
        """
        # TODO 14a（L2）：严格验证 repeat_index；每条 case 都重新调用两个工厂，
        # 用 case.max_steps 和本 Runner 的 duplicate_guard 运行真实 AgentRuntime；
        # 保存唯一 run_id、墙钟延迟及未经删减的 RunResult。
        if type(repeat_index) is not int or repeat_index < 0:
            raise ValueError("repeat_index必须是真正的非负整数")
        start_time = perf_counter()
        provider = self._provider_factory(case)
        executor = self._executor_factory(case)
        agent_runtime = AgentRuntime(
            decision_provider=provider,
            executor=executor,
            max_steps=case.max_steps,
            duplicate_guard= self._duplicate_guard,
        )
        run_result = agent_runtime.run(case.prompt)
        agent_episode=AgentEpisode(
            run_id=f"{case.case_id}-{repeat_index}-{uuid4().hex}",
            case_id=case.case_id,
            suite=case.suite,
            repeat_index=repeat_index,
            latency_ms=self._elapsed_ms(start_time),
            result=run_result,
        )
        return agent_episode
    def _elapsed_ms(self, start_time: float) -> float:
        return max(0.0 , (perf_counter() - start_time) * 1000)
        

def first_tool_call(episode: AgentEpisode):
    """返回模型首次工具选择；没有工具动作时返回空值。"""

    for model_call in episode.result.model_calls:
        if model_call.action is not None and model_call.action.tool_call is not None:
            return model_call.action.tool_call
    return None


def tool_messages(episode: AgentEpisode) -> list[Message]:
    """返回进入对话历史的所有结构化工具结果消息。"""

    return [message for message in episode.result.messages if message.role == "tool"]
