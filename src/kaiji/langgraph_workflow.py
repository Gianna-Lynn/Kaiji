"""构建带检索、有限恢复、人工审批和检查点续跑能力的 LangGraph 工作流。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from kaiji.adapters.azusa import AzusaAdapter, AzusaAdapterError
from kaiji.models import StopReason


class AgentState(TypedDict, total=False):
    """描述 LangGraph 各节点之间共享的运行状态。"""

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
    """创建一份边界明确、计数器归零的初始工作流状态。"""

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
    """构建符合任务书要求、只允许一次恢复且支持人工介入的工作流。"""

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
        """根据证据、步数和错误可重试性选择下一节点。"""

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
                    "question": "Approve this simulated side effect?",
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
            "answer": f"Approved simulated action: {state['pending_action']}",
            "stop_reason": StopReason.COMPLETED,
        }

    def rejected(state: AgentState) -> dict:
        """生成人工拒绝后的终止状态。"""

        return {
            "answer": None,
            "stop_reason": StopReason.HUMAN_REJECTED,
        }

    def answer(state: AgentState) -> dict:
        """仅使用已校验证据生成带引用的最终回答。"""

        citations = [item["chunk_id"] for item in state["evidence"]]
        text = state["evidence"][0]["text"]
        return {
            "answer": f"{text}\nCitations: {', '.join(citations)}",
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
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def run_graph(graph, question: str, *, thread_id: str, max_steps: int = 3) -> dict:
    """以新的线程状态运行工作流。"""

    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(_initial_state(question, max_steps=max_steps), config=config)


def resume_graph(graph, *, thread_id: str, approved: bool) -> dict:
    """使用人工审批结果恢复已暂停的工作流。"""

    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=approved), config=config)
