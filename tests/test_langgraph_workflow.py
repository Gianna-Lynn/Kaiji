"""验证 LangGraph 的检索、改写、终止、Checkpoint 与人工确认恢复。"""

from kaiji.adapters.azusa import MockAzusaAdapter, SearchResponse
from kaiji.langgraph_workflow import (
    build_agent_graph,
    resume_graph,
    run_graph,
)
from kaiji.models import StopReason


def test_graph_answers_with_citation_and_checkpointed_state() -> None:
    graph = build_agent_graph(MockAzusaAdapter())

    result = run_graph(graph, "Kaiji", thread_id="success")

    assert result["stop_reason"] == StopReason.COMPLETED
    assert "kaiji-001" in result["answer"]
    snapshot = graph.get_state({"configurable": {"thread_id": "success"}})
    assert snapshot.values["step_count"] == 1


def test_graph_rewrites_empty_query_and_recovers_once() -> None:
    graph = build_agent_graph(MockAzusaAdapter())

    result = run_graph(graph, "unfindable-token", thread_id="empty")

    assert result["stop_reason"] == StopReason.COMPLETED
    assert result["retry_count"] == 1
    assert result["step_count"] == 2


def test_graph_stops_when_rewritten_query_is_still_empty() -> None:
    class AlwaysEmptyAdapter(MockAzusaAdapter):
        def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
            return SearchResponse(hits=[], mode=mode)

    graph = build_agent_graph(AlwaysEmptyAdapter())

    result = run_graph(graph, "unfindable-token", thread_id="still-empty")

    assert result["stop_reason"] == StopReason.INSUFFICIENT_EVIDENCE
    assert result["retry_count"] == 1
    assert result["step_count"] == 2


def test_graph_interrupts_side_effect_and_resumes_rejection() -> None:
    graph = build_agent_graph(MockAzusaAdapter())

    paused = run_graph(graph, "delete mock record", thread_id="reject")

    assert paused["__interrupt__"]
    result = resume_graph(graph, thread_id="reject", approved=False)
    assert result["stop_reason"] == StopReason.HUMAN_REJECTED


def test_graph_interrupts_side_effect_and_resumes_approval() -> None:
    graph = build_agent_graph(MockAzusaAdapter())

    paused = run_graph(graph, "send mock report", thread_id="approve")

    assert paused["__interrupt__"]
    result = resume_graph(graph, thread_id="approve", approved=True)
    assert result["stop_reason"] == StopReason.COMPLETED
    assert "Approved simulated action" in result["answer"]
