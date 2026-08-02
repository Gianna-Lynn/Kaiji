"""验证 LangGraph 的状态语义、路由优先级、恢复边界与线程隔离。"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from kaiji.adapters.azusa import (
    AzusaAdapterError,
    MockAzusaAdapter,
    SearchHit,
    SearchResponse,
)
from kaiji.langgraph_workflow import build_agent_graph, resume_graph, run_graph
from kaiji.models import StopReason


class AlwaysEmptyAdapter(MockAzusaAdapter):
    """始终返回空证据，用于覆盖改写、失败和步数上限。"""

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        return SearchResponse(hits=[], mode=mode)


class RecordingAdapter(MockAzusaAdapter):
    """记录工作流每次真正发送给 Azusa 的搜索参数。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, int, str]] = []

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        self.calls.append((query, k, mode))
        return super().search_azusa(query, k, mode)


class TwoHitAdapter(MockAzusaAdapter):
    """返回两条内容不同的证据，用于校验正文与引用的一一对应。"""

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        return SearchResponse(
            hits=[
                SearchHit(chunk_id="first", text="第一条证据正文"),
                SearchHit(chunk_id="second", text="第二条未使用证据"),
            ],
            mode=mode,
        )


class RetryOnceAdapter(MockAzusaAdapter):
    """第一次产生可重试错误，改写后恢复成功。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        self.calls += 1
        if self.calls == 1:
            raise AzusaAdapterError("暂时不可用", retryable=True)
        return super().search_azusa(query, k, mode)


class NonRetryableAdapter(MockAzusaAdapter):
    """产生不可重试错误，并记录是否发生了多余调用。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        self.calls += 1
        raise AzusaAdapterError("请求不合法", retryable=False)


def _interrupt_payload(paused: dict) -> dict:
    """从 LangGraph 暂停结果中读取结构化审批载荷。"""

    interrupts = paused["__interrupt__"]
    assert len(interrupts) == 1
    return interrupts[0].value


def test_graph_answers_with_citation_at_search_limit() -> None:
    """证据优先于步数上限，最后一次成功检索仍必须生成回答。"""

    adapter = RecordingAdapter()
    graph = build_agent_graph(adapter)

    result = run_graph(graph, "Kaiji", thread_id="success", max_steps=1)

    assert result["stop_reason"] == StopReason.COMPLETED
    assert "kaiji-001" in result["answer"]
    assert result["step_count"] == 1
    assert result["retry_count"] == 0
    assert result["route"] == "search"
    assert adapter.calls == [("Kaiji", 3, "hybrid")]
    snapshot = graph.get_state({"configurable": {"thread_id": "success"}})
    assert snapshot.values["step_count"] == 1


def test_graph_answer_only_cites_the_evidence_used_for_its_text() -> None:
    """未用于正文的第二条命中不得被硬编码进引用。"""

    graph = build_agent_graph(TwoHitAdapter())

    result = run_graph(graph, "任意问题", thread_id="two-hits")

    assert result["stop_reason"] == StopReason.COMPLETED
    assert "第一条证据正文" in result["answer"]
    assert "first" in result["answer"]
    assert "第二条未使用证据" not in result["answer"]
    assert "second" not in result["answer"]


def test_graph_rewrites_empty_query_and_recovers_once() -> None:
    """空证据应触发恰好一次改写，两个计数器必须各自计数。"""

    adapter = RecordingAdapter()
    graph = build_agent_graph(adapter)

    result = run_graph(graph, "unfindable-token", thread_id="empty")

    assert result["stop_reason"] == StopReason.COMPLETED
    assert result["question"] == "unfindable-token"
    assert result["rewritten_query"].strip()
    assert result["rewritten_query"] != result["question"]
    assert result["retry_count"] == 1
    assert result["step_count"] == 2
    assert result["max_steps"] == 3
    assert result["route"] == "search"
    assert result["pending_action"] is None
    assert result["approved"] is None
    assert result["error"] is None
    assert result["error_retryable"] is False
    assert adapter.calls == [
        ("unfindable-token", 3, "hybrid"),
        (result["rewritten_query"], 3, "hybrid"),
    ]


def test_graph_query_rewrite_is_deterministic_for_the_same_question() -> None:
    """相同原问题在不同线程中必须得到相同且非空的确定性改写。"""

    first = run_graph(
        build_agent_graph(RecordingAdapter()),
        "unfindable-token",
        thread_id="deterministic-first",
    )
    second = run_graph(
        build_agent_graph(RecordingAdapter()),
        "unfindable-token",
        thread_id="deterministic-second",
    )

    assert first["rewritten_query"] == second["rewritten_query"]
    assert first["rewritten_query"].strip()
    assert first["rewritten_query"] != first["question"]


def test_graph_stops_after_single_rewrite_when_result_remains_empty() -> None:
    """尚有检索预算也不能突破“一次改写”的恢复边界。"""

    graph = build_agent_graph(AlwaysEmptyAdapter())

    result = run_graph(graph, "unfindable-token", thread_id="still-empty")

    assert result["stop_reason"] == StopReason.INSUFFICIENT_EVIDENCE
    assert result["answer"] is None
    assert result["retry_count"] == 1
    assert result["step_count"] == 2


def test_graph_prefers_max_steps_after_second_empty_search_at_exact_limit() -> None:
    """第二次空检索恰好耗尽预算时，步数上限仍优先于改写次数判断。"""

    result = run_graph(
        build_agent_graph(AlwaysEmptyAdapter()),
        "unfindable-token",
        thread_id="max-steps-after-rewrite",
        max_steps=2,
    )

    assert result["stop_reason"] == StopReason.MAX_STEPS
    assert result["step_count"] == 2
    assert result["retry_count"] == 1


def test_graph_returns_max_steps_before_rewrite_when_budget_is_exhausted() -> None:
    """无证据且检索次数达到上限时，步数判断优先于可重试恢复。"""

    graph = build_agent_graph(AlwaysEmptyAdapter())

    result = run_graph(
        graph,
        "unfindable-token",
        thread_id="max-steps",
        max_steps=1,
    )

    assert result["stop_reason"] == StopReason.MAX_STEPS
    assert result["step_count"] == 1
    assert result["retry_count"] == 0


def test_graph_recovers_once_from_retryable_adapter_error() -> None:
    """可重试 Adapter 错误应改写一次，并清理成功后的旧错误。"""

    adapter = RetryOnceAdapter()
    graph = build_agent_graph(adapter)

    result = run_graph(graph, "Kaiji", thread_id="retryable")

    assert result["stop_reason"] == StopReason.COMPLETED
    assert adapter.calls == 2
    assert result["step_count"] == 2
    assert result["retry_count"] == 1
    assert result["error"] is None
    assert result["error_retryable"] is False


def test_graph_does_not_retry_non_retryable_adapter_error() -> None:
    """不可重试错误必须直接以证据不足停止。"""

    adapter = NonRetryableAdapter()
    graph = build_agent_graph(adapter)

    result = run_graph(graph, "Kaiji", thread_id="non-retryable")

    assert result["stop_reason"] == StopReason.INSUFFICIENT_EVIDENCE
    assert result["error"] == "请求不合法"
    assert result["error_retryable"] is False
    assert result["step_count"] == 1
    assert result["retry_count"] == 0
    assert adapter.calls == 1


@pytest.mark.parametrize("verb", ["delete", "SEND", "Publish"])
def test_graph_interrupt_payload_covers_every_side_effect_keyword(verb: str) -> None:
    """三类模拟副作用必须暂停，并暴露稳定的结构化确认载荷。"""

    adapter = RecordingAdapter()
    graph = build_agent_graph(adapter)
    action = f"{verb} mock record"

    paused = run_graph(graph, action, thread_id=f"interrupt-{verb}")

    payload = _interrupt_payload(paused)
    assert payload["kind"] == "side_effect_confirmation"
    assert payload["action"] == action
    assert isinstance(payload["question"], str)
    assert "批准" in payload["question"]
    snapshot = graph.get_state(
        {"configurable": {"thread_id": f"interrupt-{verb}"}}
    )
    assert snapshot.values["pending_action"] == action
    assert snapshot.values["approved"] is None
    assert snapshot.values["route"] == "confirm"
    assert snapshot.values["step_count"] == 0
    assert snapshot.values["evidence"] == []
    assert adapter.calls == []


def test_graph_resumes_rejection_on_the_same_thread() -> None:
    """拒绝决定应从原线程暂停点恢复并安全终止。"""

    graph = build_agent_graph(MockAzusaAdapter())
    run_graph(graph, "delete mock record", thread_id="reject")

    result = resume_graph(graph, thread_id="reject", approved=False)

    assert result["stop_reason"] == StopReason.HUMAN_REJECTED
    assert result["answer"] is None
    assert result["approved"] is False


def test_graph_resumes_approval_and_preserves_pending_action() -> None:
    """批准决定应保留待执行动作并以完成状态结束。"""

    graph = build_agent_graph(MockAzusaAdapter())
    run_graph(graph, "send mock report", thread_id="approve")

    result = resume_graph(graph, thread_id="approve", approved=True)

    assert result["stop_reason"] == StopReason.COMPLETED
    assert result["answer"] is not None
    assert "send mock report" in result["answer"]
    assert result["approved"] is True


def test_two_interrupted_threads_resume_in_reverse_order_without_state_leakage() -> None:
    """两个暂停线程逆序恢复时必须保留各自的动作和人工决定。"""

    graph = build_agent_graph(MockAzusaAdapter())
    first_action = "delete first mock record"
    second_action = "publish second mock report"
    run_graph(graph, first_action, thread_id="pending-first")
    run_graph(graph, second_action, thread_id="pending-second")

    second = resume_graph(graph, thread_id="pending-second", approved=True)
    first = resume_graph(graph, thread_id="pending-first", approved=False)

    assert second["pending_action"] == second_action
    assert second["approved"] is True
    assert second["answer"] is not None
    assert second_action in second["answer"]
    assert first["pending_action"] == first_action
    assert first["approved"] is False
    assert first["answer"] is None
    assert first["stop_reason"] == StopReason.HUMAN_REJECTED


def test_checkpointer_isolates_state_between_thread_ids() -> None:
    """同一图实例中的两个线程不得共享问题、证据或计数器。"""

    graph = build_agent_graph(MockAzusaAdapter())

    run_graph(graph, "Kaiji", thread_id="thread-kaiji")
    run_graph(graph, "Azusa", thread_id="thread-azusa")

    kaiji_state = graph.get_state(
        {"configurable": {"thread_id": "thread-kaiji"}}
    ).values
    azusa_state = graph.get_state(
        {"configurable": {"thread_id": "thread-azusa"}}
    ).values
    assert kaiji_state["question"] == "Kaiji"
    assert azusa_state["question"] == "Azusa"
    assert kaiji_state["evidence"][0]["chunk_id"] == "kaiji-001"
    assert azusa_state["evidence"][0]["chunk_id"] == "azusa-001"
    assert kaiji_state["step_count"] == azusa_state["step_count"] == 1


def test_build_graph_uses_explicitly_injected_memory_saver() -> None:
    """调用方提供的 Checkpointer 必须用于编译和读取线程快照。"""

    checkpointer = MemorySaver()
    graph = build_agent_graph(MockAzusaAdapter(), checkpointer=checkpointer)

    assert graph.checkpointer is checkpointer
    run_graph(graph, "Kaiji", thread_id="explicit-checkpointer")
    snapshot = graph.get_state(
        {"configurable": {"thread_id": "explicit-checkpointer"}}
    )
    assert snapshot.values["question"] == "Kaiji"


def test_default_checkpointer_is_not_shared_between_graph_instances() -> None:
    """未注入 Checkpointer 时，每个图都必须获得独立的线程状态容器。"""

    first = build_agent_graph(MockAzusaAdapter())
    second = build_agent_graph(MockAzusaAdapter())

    assert first.checkpointer is not second.checkpointer


@pytest.mark.parametrize("max_steps", [0, -1])
def test_graph_rejects_non_positive_search_limit(max_steps: int) -> None:
    """无效上限不能进入图执行，也不能产生含糊的零步结果。"""

    graph = build_agent_graph(MockAzusaAdapter())

    with pytest.raises(ValueError):
        run_graph(
            graph,
            "Kaiji",
            thread_id=f"invalid-{max_steps}",
            max_steps=max_steps,
        )
