"""构建带检索、有限恢复、人工审批和检查点续跑能力的 LangGraph 工作流。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from kaiji.adapters.azusa import AzusaAdapter, AzusaAdapterError
from kaiji.models import StopReason


class AgentState(TypedDict, total=False):
    """描述 LangGraph 节点间共享的状态及各字段的唯一职责。

    ``question`` 保存不可变的原始用户问题，``rewritten_query`` 保存当前真正
    送往 Azusa 的查询。``step_count`` 只统计 search 节点执行次数，也就是
    检索尝试次数；``retry_count`` 只统计 query rewrite 次数；``max_steps``
    只限制检索尝试次数，并不统计 router、answer 等其他图节点。

    ``route`` 是 router 选择的下一条边，``evidence`` 是校验后的检索命中，
    ``error`` 与 ``error_retryable`` 描述最近一次失败。``pending_action``、
    ``approved`` 分别保存待确认的模拟副作用和人的恢复决定。``answer`` 与
    ``stop_reason`` 只由终止节点写入，表示对外结果。
    """

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
    stop_reason: StopReason | None


def _initial_state(question: str, *, max_steps: int) -> AgentState:
    """创建计数器归零的初始状态，并拒绝无效检索上限。"""

    if max_steps < 1:
        raise ValueError("max_steps 必须至少为 1")

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
    """构建带检索、一次查询改写、人工确认与线程检查点的工作流。

    工作流只允许一次 query rewrite；是否继续检索同时受 ``retry_count`` 和
    ``max_steps`` 约束。副作用分支只模拟批准或拒绝，不执行真实外部写操作。
    ``thread_id`` 由调用入口通过 checkpointer 隔离，本函数不维护全局状态。
    """

    def router(state: AgentState) -> dict:
        """把普通问题送往检索，把模拟副作用送往人工确认。"""

        lowered = state["question"].lower()
        side_effect = any(word in lowered for word in ("delete", "send", "publish"))
        if side_effect:
            return {
                "route": "confirm",
                "pending_action": state["question"],
            }
        return {"route": "search"}

    def search(state: AgentState) -> dict:
        """调用 Azusa 检索，并把成功结果或结构化错误写回状态。"""

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
        """按固定优先级选择搜索后的下一节点。

        优先级依次是：已有证据时回答；无证据且检索次数已达上限时停止；
        尚有预算、错误可重试且从未改写时改写；其他情况以证据不足停止。
        因此最后一次检索若得到证据，仍应回答而不是返回 ``MAX_STEPS``。
        """

        if state.get("evidence"):
            return "answer"
        if state["step_count"] >= state["max_steps"]:
            return "max_steps"
        if state.get("error_retryable") and state["retry_count"] < 1:
            return "rewrite"
        return "failure"

    def rewrite(state: AgentState) -> dict:
        """改写查询并增加重试计数，避免无界恢复循环。"""

        return {
            "rewritten_query": f"{state['question']} Kaiji Azusa evidence",
            "retry_count": state["retry_count"] + 1,
        }

    def request_confirmation(state: AgentState) -> dict:
        """在模拟副作用前暂停工作流，等待人的明确决定。"""

        approved = bool(
            interrupt(
                {
                    "kind": "side_effect_confirmation",
                    "action": state["pending_action"],
                    "question": "是否批准这项模拟副作用？",
                }
            )
        )
        return {"approved": approved}

    def confirmation_route(state: AgentState) -> str:
        """把人工决定映射为批准或拒绝分支。"""

        return "approved" if state.get("approved") else "rejected"

    def approved(state: AgentState) -> dict:
        """生成模拟操作获批后的终止状态。"""

        return {
            "answer": f"已批准模拟操作：{state['pending_action']}",
            "stop_reason": StopReason.COMPLETED,
        }

    def rejected(state: AgentState) -> dict:
        """生成人工拒绝后的终止状态。"""

        return {
            "answer": None,
            "stop_reason": StopReason.HUMAN_REJECTED,
        }

    def answer(state: AgentState) -> dict:
        """只使用第一条已校验证据，并仅引用支撑该正文的命中。"""

        evidence = state["evidence"][0]
        return {
            "answer": f"{evidence['text']}\nCitations: {evidence['chunk_id']}",
            "stop_reason": StopReason.COMPLETED,
        }

    def failure(state: AgentState) -> dict:
        """在证据不足且无法继续恢复时安全终止。"""

        return {
            "answer": None,
            "stop_reason": StopReason.INSUFFICIENT_EVIDENCE,
        }

    def max_steps(state: AgentState) -> dict:
        """在达到最大步数时安全终止。"""

        return {
            "answer": None,
            "stop_reason": StopReason.MAX_STEPS,
        }

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
    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        lambda state: state["route"],
        {"search": "search", "confirm": "confirm"},
    )
    graph.add_conditional_edges(
        "search",
        assess,
        {
            "answer": "answer",
            "rewrite": "rewrite",
            "failure": "failure",
            "max_steps": "max_steps",
        },
    )
    graph.add_edge("rewrite", "search")
    graph.add_conditional_edges(
        "confirm",
        confirmation_route,
        {"approved": "approved", "rejected": "rejected"},
    )
    for terminal in ("approved", "rejected", "answer", "failure", "max_steps"):
        graph.add_edge(terminal, END)
    active_checkpointer = checkpointer if checkpointer is not None else MemorySaver()
    return graph.compile(checkpointer=active_checkpointer)


def run_graph(graph, question: str, *, thread_id: str, max_steps: int = 3) -> dict:
    """以新线程状态运行工作流；``max_steps`` 表示最多检索次数。"""

    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(_initial_state(question, max_steps=max_steps), config=config)


def resume_graph(graph, *, thread_id: str, approved: bool) -> dict:
    """在同一 ``thread_id`` 上使用人工审批结果恢复暂停点。"""

    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=approved), config=config)
