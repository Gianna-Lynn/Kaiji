"""构建带条件边、一次恢复、Checkpoint 和人工确认的 LangGraph 工作流。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from kaiji.adapters.azusa import AzusaAdapter, AzusaAdapterError
from kaiji.models import StopReason


class AgentState(TypedDict, total=False):
    """保存 LangGraph 节点之间显式传递且可 Checkpoint 的运行状态。"""

    question: str
    rewritten_query: str
    route: Literal["search", "confirm"]
    evidence: list[dict[str, Any]]
    answer: str | None
    retry_count: int
    step_count: int
    max_steps: int
    error: str | None
    error_retryable: bool
    pending_action: str | None
    approved: bool | None
    stop_reason: str | None


def _initial_state(question: str, *, max_steps: int) -> AgentState:
    """为一次新线程构造字段完整、计数清零的初始状态。"""

    return AgentState(
        question=question,
        rewritten_query=question,
        evidence=[],
        answer=None,
        retry_count=0,
        step_count=0,
        max_steps=max_steps,
        error=None,
        error_retryable=False,
        pending_action=None,
        approved=None,
        stop_reason=None,
    )


def build_agent_graph(
    adapter: AzusaAdapter,
    *,
    checkpointer: MemorySaver | None = None,
):
    """构建包含一次有界恢复和人工确认的任务书工作流。"""

    def router(state: AgentState) -> dict:
        """区分只读检索与需要人工确认的副作用请求。"""
        # TODO 6a：把 delete/send/publish 请求路由到 confirm，并保存
        # pending_action；所有只读问题路由到 search。
        del state
        return {"route": "search"}

    def search(state: AgentState) -> dict:
        """调用 Azusa 搜索，并把证据或结构化错误写回 State。"""

        step_count = state["step_count"] + 1
        try:
            response = adapter.search_azusa(
                state["rewritten_query"],
                3,
                "hybrid",
            )
            return {
                "evidence": [hit.model_dump(mode="json") for hit in response.hits],
                "step_count": step_count,
                "error": None if response.hits else "empty search result",
                "error_retryable": not response.hits,
            }
        except AzusaAdapterError as exc:
            return {
                "evidence": [],
                "step_count": step_count,
                "error": str(exc),
                "error_retryable": exc.retryable,
            }

    def assess(state: AgentState) -> str:
        """根据证据、重试属性和步数选择下一条条件边。"""
        # TODO 6b：在 answer/max_steps/rewrite/failure 中选择。只有失败
        # 可重试且 retry_count < 1 时才能 rewrite。
        del state
        return "failure"

    def rewrite(state: AgentState) -> dict:
        """生成确定性查询改写，并递增显式重试计数。"""
        # TODO 6c：完成一次确定性查询改写，并递增 retry_count。
        del state
        return {}

    def request_confirmation(state: AgentState) -> dict:
        """通过 interrupt 暂停副作用，并接收恢复时的人工选择。"""
        # TODO 6d：用 interrupt({...}) 暂停，把恢复值转换为 bool。中断负载
        # 应包含 kind、action 和便于人理解的问题。
        del state
        return {"approved": False}

    def confirmation_route(state: AgentState) -> str:
        """把恢复后的批准标记映射为 approved 或 rejected 条件边。"""
        # TODO 6e：把恢复后的 approval 标记映射为 approved/rejected。
        del state
        return "rejected"

    def approved(state: AgentState) -> dict:
        """完成已获人工批准的模拟副作用。"""

        return {
            "answer": f"已批准模拟操作：{state['pending_action']}",
            "stop_reason": StopReason.COMPLETED,
        }

    def rejected(state: AgentState) -> dict:
        """以明确停止原因结束被人工拒绝的副作用。"""

        return {
            "answer": None,
            "stop_reason": StopReason.HUMAN_REJECTED,
        }

    def answer(state: AgentState) -> dict:
        """仅使用校验成功的证据生成带 chunk_id 引用的回答。"""

        citations = [item["chunk_id"] for item in state["evidence"]]
        text = state["evidence"][0]["text"]
        return {
            "answer": f"{text}\n引用：{', '.join(citations)}",
            "stop_reason": StopReason.COMPLETED,
        }

    def failure(state: AgentState) -> dict:
        """在证据不足且无法恢复时明确失败。"""

        return {
            "answer": None,
            "stop_reason": StopReason.INSUFFICIENT_EVIDENCE,
        }

    def max_steps(state: AgentState) -> dict:
        """在达到工作流步数上限时停止。"""

        return {
            "answer": None,
            "stop_reason": StopReason.MAX_STEPS,
        }

    # TODO 6f：连接真实工作流：
    # START → router
    # router -(route)→ search | confirm
    # search -(assess)→ answer | rewrite | failure | max_steps
    # rewrite → search
    # confirm -(approval)→ approved | rejected
    # 所有终止节点 → END
    #
    # 下方先注册所有节点，使练习脚手架可以编译。请用上述边替换占位的
    # START → failure。
    graph = StateGraph(AgentState)
    graph.add_node("router", router)
    graph.add_node("search", search)
    graph.add_node("rewrite", rewrite)
    graph.add_node("confirm", request_confirmation)
    graph.add_node("approved", approved)
    graph.add_node("rejected", rejected)
    graph.add_node("answer", answer)
    graph.add_node("failure", failure)
    graph.add_node("max_steps", max_steps)
    graph.add_edge(START, "failure")
    graph.add_edge("failure", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def run_graph(graph, question: str, *, thread_id: str, max_steps: int = 3) -> dict:
    """以新初始状态运行图，并用 thread_id 隔离 Checkpoint。"""

    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(_initial_state(question, max_steps=max_steps), config=config)


def resume_graph(graph, *, thread_id: str, approved: bool) -> dict:
    """用人工批准或拒绝结果恢复指定线程。"""

    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=approved), config=config)
