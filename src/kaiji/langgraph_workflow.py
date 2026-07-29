from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from kaiji.adapters.azusa import AzusaAdapter, AzusaAdapterError
from kaiji.models import StopReason


class AgentState(TypedDict, total=False):
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
    """Build the task-book workflow with one bounded recovery and HITL."""

    def router(state: AgentState) -> dict:
        lowered = state["question"].lower()
        side_effect = any(word in lowered for word in ("delete", "send", "publish"))
        if side_effect:
            return {
                "route": "confirm",
                "pending_action": state["question"],
            }
        return {"route": "search"}

    def search(state: AgentState) -> dict:
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
        if state.get("evidence"):
            return "answer"
        if state["step_count"] >= state["max_steps"]:
            return "max_steps"
        if state.get("error_retryable") and state["retry_count"] < 1:
            return "rewrite"
        return "failure"

    def rewrite(state: AgentState) -> dict:
        return {
            "rewritten_query": f"{state['question']} Kaiji Azusa evidence",
            "retry_count": state["retry_count"] + 1,
        }

    def request_confirmation(state: AgentState) -> dict:
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
        return "approved" if state.get("approved") else "rejected"

    def approved(state: AgentState) -> dict:
        return {
            "answer": f"Approved simulated action: {state['pending_action']}",
            "stop_reason": StopReason.COMPLETED,
        }

    def rejected(state: AgentState) -> dict:
        return {
            "answer": None,
            "stop_reason": StopReason.HUMAN_REJECTED,
        }

    def answer(state: AgentState) -> dict:
        citations = [item["chunk_id"] for item in state["evidence"]]
        text = state["evidence"][0]["text"]
        return {
            "answer": f"{text}\nCitations: {', '.join(citations)}",
            "stop_reason": StopReason.COMPLETED,
        }

    def failure(state: AgentState) -> dict:
        return {
            "answer": None,
            "stop_reason": StopReason.INSUFFICIENT_EVIDENCE,
        }

    def max_steps(state: AgentState) -> dict:
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
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(_initial_state(question, max_steps=max_steps), config=config)


def resume_graph(graph, *, thread_id: str, approved: bool) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=approved), config=config)
