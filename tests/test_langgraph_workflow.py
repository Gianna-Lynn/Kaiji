"""验证 LangGraph 的状态语义、路由优先级、恢复边界与线程隔离。"""

from __future__ import annotations

from copy import deepcopy

import pytest
from langgraph.checkpoint.memory import MemorySaver

from kaiji.adapters.azusa import (
    AzusaAdapterError,
    MockAzusaAdapter,
    SearchHit,
    SearchResponse,
)
from kaiji.langgraph_workflow import (
    AgentState,
    assess_search,
    build_agent_graph,
    confirmation_route,
    request_confirmation,
    resume_graph,
    rewrite_query,
    route_request,
    run_graph,
)
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


def _base_state(**overrides: object) -> AgentState:
    """构造可供纯节点函数测试使用的完整初始状态。"""

    state: AgentState = {
        "question": "Kaiji",
        "rewritten_query": "Kaiji",
        "route": "search",
        "evidence": [],
        "answer": None,
        "retry_count": 0,
        "step_count": 0,
        "max_steps": 3,
        "error": None,
        "error_retryable": False,
        "pending_action": None,
        "approved": None,
        "stop_reason": None,
    }
    state.update(overrides)
    return state


def _assert_plain_checkpoint_stop_reason(
    graph, thread_id: str, result: dict, expected: StopReason
) -> None:
    """确认对外结果和 Checkpoint 都只保存停止原因字符串。"""

    assert result["stop_reason"] == expected.value
    assert type(result["stop_reason"]) is str
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    assert snapshot.values["stop_reason"] == expected.value
    assert type(snapshot.values["stop_reason"]) is str


def test_todo_6a_graph_has_explicit_nodes_and_edges() -> None:
    """6a 只检查图结构，不执行任何节点。"""

    graph = build_agent_graph(MockAzusaAdapter())
    compiled = graph.get_graph()
    nodes = set(compiled.nodes)
    edges = {(edge.source, edge.target) for edge in compiled.edges}

    assert {
        "router",
        "search",
        "rewrite",
        "confirm",
        "approved",
        "rejected",
        "answer",
        "failure",
        "max_steps",
    } <= nodes
    assert "todo_6a" not in nodes
    assert {
        ("__start__", "router"),
        ("router", "search"),
        ("router", "confirm"),
        ("search", "answer"),
        ("search", "rewrite"),
        ("search", "failure"),
        ("search", "max_steps"),
        ("rewrite", "search"),
        ("confirm", "approved"),
        ("confirm", "rejected"),
        ("approved", "__end__"),
        ("rejected", "__end__"),
        ("answer", "__end__"),
        ("failure", "__end__"),
        ("max_steps", "__end__"),
    } <= edges


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Kaiji 是什么？", {"route": "search"}),
        ("delete mock record", {"route": "confirm", "pending_action": "delete mock record"}),
        ("sEnD mock report", {"route": "confirm", "pending_action": "sEnD mock report"}),
        ("Publish mock report", {"route": "confirm", "pending_action": "Publish mock report"}),
    ],
)
def test_todo_6b_router_returns_only_its_local_update(
    question: str, expected: dict[str, str]
) -> None:
    """6b 直接验证 Router 的路由和局部 State 更新。"""

    state = _base_state(question=question)
    before = deepcopy(state)

    assert route_request(state) == expected
    assert state == before


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"evidence": [{"chunk_id": "c1"}], "step_count": 3}, "answer"),
        ({"evidence": [{"chunk_id": "c1"}], "step_count": 4}, "answer"),
        ({"step_count": 3, "max_steps": 3}, "max_steps"),
        ({"step_count": 4, "max_steps": 3}, "max_steps"),
        ({"error_retryable": True, "retry_count": 0}, "rewrite"),
        ({"error_retryable": True, "retry_count": 1}, "failure"),
        ({"error_retryable": False}, "failure"),
    ],
)
def test_todo_6c_assess_enforces_priority(
    overrides: dict[str, object], expected: str
) -> None:
    """6c 用手工 State 直接验证条件边优先级。"""

    assert assess_search(_base_state(**overrides)) == expected


def test_todo_6d_rewrite_returns_only_local_updates() -> None:
    """6d 只能更新查询和改写计数，且不得修改输入 State。"""

    state = _base_state(question="原始问题", retry_count=0)
    before = deepcopy(state)

    result = rewrite_query(state)

    assert set(result) == {"rewritten_query", "retry_count"}
    assert result["rewritten_query"] != state["question"]
    assert result["rewritten_query"].strip()
    assert result["retry_count"] == 1
    assert rewrite_query(deepcopy(state)) == result
    assert state["question"] == "原始问题"
    assert state["route"] == "search"
    assert state == before


@pytest.mark.parametrize("decision", [True, False])
def test_todo_6e_confirmation_returns_strict_bool_and_stable_payload(
    monkeypatch: pytest.MonkeyPatch,
    decision: bool,
) -> None:
    """6e 验证审批载荷和真正布尔值的局部更新。"""

    captured: list[dict] = []
    state = _base_state(pending_action="delete mock record")
    before = deepcopy(state)

    def fake_interrupt(payload: dict) -> bool:
        captured.append(payload)
        return decision

    monkeypatch.setattr("kaiji.langgraph_workflow.interrupt", fake_interrupt)

    result = request_confirmation(state)

    assert result == {"approved": decision}
    assert set(result) == {"approved"}
    assert state == before
    assert captured == [
        {
            "kind": "side_effect_confirmation",
            "action": "delete mock record",
            "question": "是否批准这项模拟副作用？",
        }
    ]


@pytest.mark.parametrize("decision", ["false", "拒绝", 0, 1, None])
def test_todo_6e_rejects_non_bool_resume_values(
    monkeypatch: pytest.MonkeyPatch, decision: object
) -> None:
    """字符串、数字和空值不得被转换成审批结果。"""

    monkeypatch.setattr(
        "kaiji.langgraph_workflow.interrupt",
        lambda payload: decision,
    )

    with pytest.raises(ValueError):
        request_confirmation(_base_state(pending_action="send mock report"))


@pytest.mark.parametrize(
    ("approved", "expected"), [(True, "approved"), (False, "rejected")]
)
def test_todo_6f_confirmation_route_requires_true_bool(
    approved: object, expected: str
) -> None:
    """6f 直接验证真正的 True/False 才能进入对应分支。"""

    assert confirmation_route(_base_state(approved=approved)) == expected


@pytest.mark.parametrize("approved", [None, 0, 1, "true", "false", "拒绝"])
def test_todo_6f_confirmation_route_rejects_non_bool_state(
    approved: object,
) -> None:
    """被破坏 State 中的非法审批值必须明确失败，不能伪装成拒绝。"""

    with pytest.raises(ValueError):
        confirmation_route(_base_state(approved=approved))


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


def test_checkpoint_stop_reason_uses_plain_strings_for_all_terminal_paths() -> None:
    """所有终止节点都必须避免把自定义 StopReason 对象写入 Checkpoint。"""

    completed_graph = build_agent_graph(MockAzusaAdapter())
    completed = run_graph(completed_graph, "Kaiji", thread_id="plain-completed")
    _assert_plain_checkpoint_stop_reason(
        completed_graph, "plain-completed", completed, StopReason.COMPLETED
    )

    max_steps_graph = build_agent_graph(AlwaysEmptyAdapter())
    max_steps = run_graph(
        max_steps_graph,
        "unfindable-token",
        thread_id="plain-max-steps",
        max_steps=1,
    )
    _assert_plain_checkpoint_stop_reason(
        max_steps_graph, "plain-max-steps", max_steps, StopReason.MAX_STEPS
    )

    failure_graph = build_agent_graph(AlwaysEmptyAdapter())
    failure = run_graph(
        failure_graph, "unfindable-token", thread_id="plain-failure"
    )
    _assert_plain_checkpoint_stop_reason(
        failure_graph, "plain-failure", failure, StopReason.INSUFFICIENT_EVIDENCE
    )

    rejected_graph = build_agent_graph(MockAzusaAdapter())
    run_graph(rejected_graph, "delete mock record", thread_id="plain-rejected")
    rejected = resume_graph(
        rejected_graph, thread_id="plain-rejected", approved=False
    )
    _assert_plain_checkpoint_stop_reason(
        rejected_graph, "plain-rejected", rejected, StopReason.HUMAN_REJECTED
    )


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


def test_resume_graph_rejects_non_bool_decision() -> None:
    """真实 Command(resume=...) 路径不得把字符串批准值转成 True。"""

    graph = build_agent_graph(MockAzusaAdapter())
    run_graph(graph, "delete mock record", thread_id="invalid-resume")

    with pytest.raises(ValueError):
        resume_graph(graph, thread_id="invalid-resume", approved="false")


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


def test_run_graph_rejects_reusing_an_existing_thread_id() -> None:
    """新运行不得覆盖已有线程；暂停线程只能通过 resume_graph 继续。"""

    adapter = RecordingAdapter()
    graph = build_agent_graph(adapter)

    run_graph(graph, "Kaiji", thread_id="single-use")

    with pytest.raises(ValueError):
        run_graph(graph, "Azusa", thread_id="single-use")

    assert adapter.calls == [("Kaiji", 3, "hybrid")]


def test_run_graph_rejects_reusing_a_paused_thread_without_overwriting_checkpoint() -> None:
    """暂停线程不能被新运行覆盖，原 Interrupt 必须仍可恢复。"""

    adapter = RecordingAdapter()
    graph = build_agent_graph(adapter)
    thread_id = "paused-single-use"
    action = "delete original mock record"

    paused = run_graph(graph, action, thread_id=thread_id)
    assert _interrupt_payload(paused)["action"] == action

    with pytest.raises(ValueError):
        run_graph(graph, "publish replacement mock report", thread_id=thread_id)

    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    assert snapshot.values["pending_action"] == action
    assert snapshot.values["route"] == "confirm"
    assert snapshot.values["approved"] is None

    result = resume_graph(graph, thread_id=thread_id, approved=False)
    assert result["pending_action"] == action
    assert result["stop_reason"] == StopReason.HUMAN_REJECTED
    assert result["answer"] is None
    assert adapter.calls == []


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


@pytest.mark.parametrize(
    "max_steps", [0, -1, True, 1.5, float("nan"), "3", None]
)
def test_graph_rejects_invalid_search_limit(max_steps: object) -> None:
    """非正整数上限不能进入图执行，也不能产生含糊的零步结果。"""

    graph = build_agent_graph(MockAzusaAdapter())

    with pytest.raises(ValueError):
        run_graph(
            graph,
            "Kaiji",
            thread_id=f"invalid-{max_steps}",
            max_steps=max_steps,
        )
