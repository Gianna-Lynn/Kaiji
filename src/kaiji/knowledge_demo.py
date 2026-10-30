"""演示只读知识库问答：规则决策驱动真实 Runtime，不冒充自主模型成绩。"""

from __future__ import annotations

import json

from kaiji.adapters.azusa import AzusaAdapter
from kaiji.azusa_tools import build_azusa_registry
from kaiji.demo import ScriptedDecisionProvider
from kaiji.models import Message, ModelAction, ModelCallMetadata, ModelCallResult, RunResult, ToolCall
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolExecutor


class EvidenceDecisionProvider:
    """按固定 search→get→摘录策略使用实际证据；没有题目金标或预设引用 ID。"""

    def __init__(self) -> None:
        """为单次运行初始化搜索命中；实例不可跨对话复用。"""
        self.hit: dict | None = None

    def decide(self, messages: list[Message]) -> ModelCallResult:
        """把规则动作包装为标准 Provider 结果，明确标注零网络请求的规则来源。"""
        return ModelCallResult(
            action=self._decide_action(messages),
            metadata=ModelCallMetadata(model="rule-driven-evidence", latency_ms=0),
        )

    def _decide_action(self, messages: list[Message]) -> ModelAction:
        """消费当前 Runtime 的已校验工具消息，依次生成 search、get 和最终摘录动作。

        搜索查询来自首条用户消息，后续 ID 来自实际命中，不读取题目金标。只允许
        同一运行内的三步顺序；无命中或回读正文不一致即抛出 ValueError，由 Runtime
        记录为 model_failed，不能把结构合法但互相矛盾的证据拼成已完成回答。
        """
        tools = [message for message in messages if message.role == "tool"]
        if not tools:
            return ModelAction(kind="tool_call", tool_call=ToolCall(
                call_id="search", name="search_azusa",
                arguments={"query": messages[0].content, "k": 3, "mode": "bm25"},
            ))
        output = json.loads(tools[-1].content)["output"]
        if len(tools) == 1:
            if not output["hits"]:
                raise ValueError("没有检索证据，不能生成摘录")
            self.hit = output["hits"][0]
            return ModelAction(kind="tool_call", tool_call=ToolCall(
                call_id="get", name="get_chunk_by_id", arguments={"chunk_id": self.hit["chunk_id"]},
            ))
        if self.hit is None or output["chunk_id"] != self.hit["chunk_id"] or output["text"] != self.hit["text"]:
            raise ValueError("检索和按 ID 回读的证据不一致")
        return ModelAction(kind="final_answer", final_answer=f'{output["text"]}\nCitations: {output["chunk_id"]}')


def run_knowledge_demo(adapter: AzusaAdapter, question: str) -> tuple[RunResult, RunResult]:
    """对给定 Adapter 和问题运行两个隔离 Runtime，返回成功路径、故障路径的结果。

    第一条路径按规则完成搜索和回读，第二条故意请求不存在的 chunk；调用方负责
    检查停止原因和证据集合，不能仅凭函数返回判断成功。Adapter 生命周期由调用方
    管理，本函数不关闭共享连接，也不使用真实模型。
    """
    success = AgentRuntime(
        EvidenceDecisionProvider(), ToolExecutor(build_azusa_registry(adapter, tool_timeout_seconds=120)),
    ).run(question)
    failure = AgentRuntime(
        ScriptedDecisionProvider([ModelAction(kind="tool_call", tool_call=ToolCall(
            call_id="missing", name="get_chunk_by_id", arguments={"chunk_id": "deliberately-missing-p1"},
        ))]), ToolExecutor(build_azusa_registry(adapter, tool_timeout_seconds=120)),
    ).run("读取故意不存在的证据以验证失败边界")
    return success, failure
