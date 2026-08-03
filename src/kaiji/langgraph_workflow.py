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

    ``total=False`` 表示节点可以只返回自己负责更新的字段；LangGraph 会把这份
    局部更新合并进当前 State，而不是用返回字典替换整份状态。因此节点不得顺手
    返回或清空不属于自己职责的字段。它不表示运行中的 State 可以随意缺少基础字段：
    ``_initial_state`` 会在首次进入图时建立完整状态，后续节点依赖这项不变量。

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
    """创建完整的初始运行状态，并拒绝无效检索上限。"""

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


def route_request(state: AgentState) -> dict[str, str | None]:
    """根据原始问题选择检索或人工确认，并只返回 Router 负责的字段。"""

    lowered = state["question"].lower()
    side_effect = any(word in lowered for word in ("delete", "send", "publish"))
    if side_effect:
        return {
            "route": "confirm",
            "pending_action": state["question"],
        }
    return {"route": "search"}


def assess_search(state: AgentState) -> str:
    """按固定优先级选择搜索后的下一节点。"""

    if state.get("evidence"):
        return "answer"
    if state["step_count"] >= state["max_steps"]:
        return "max_steps"
    if state.get("error_retryable") and state["retry_count"] < 1:
        return "rewrite"
    return "failure"


def rewrite_query(state: AgentState) -> dict[str, str | int]:
    """确定性改写查询，并只返回查询和改写计数的局部更新。"""

    return {
        "rewritten_query": f"{state['question']} Kaiji Azusa evidence",
        "retry_count": state["retry_count"] + 1,
    }


def request_confirmation(state: AgentState) -> dict[str, bool]:
    """暂停等待人工决定，并严格要求恢复值是布尔值。"""

    decision = interrupt(
        {
            "kind": "side_effect_confirmation",
            "action": state["pending_action"],
            "question": "是否批准这项模拟副作用？",
        }
    )
    if type(decision) is not bool:
        raise ValueError("人工审批结果必须是 bool")
    return {"approved": decision}


def confirmation_route(state: AgentState) -> str:
    """严格校验人工决定，并映射为批准或拒绝分支。

    这里既是 Interrupt 恢复后的正常路由，也是被破坏 State 的最后一道防线；
    非 Python ``bool`` 的值属于内部协议错误，不能伪装成用户拒绝。
    """

    decision = state.get("approved")
    if type(decision) is not bool:
        raise ValueError("人工审批结果必须是 bool")
    return "approved" if decision else "rejected"


def _search(adapter: AzusaAdapter, state: AgentState) -> dict[str, Any]:
    """调用 Azusa，并把响应或适配器失败写回工作流 State。"""

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
        """在达到最大检索次数时安全终止。"""

        return {
            "answer": None,
            "stop_reason": StopReason.MAX_STEPS,
        }

    graph = StateGraph(AgentState)
    graph.add_node("router", route_request)
    graph.add_node("search", lambda state: _search(adapter, state))
    graph.add_node("rewrite", rewrite_query)
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
        assess_search,
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
    """以未使用的 ``thread_id`` 开始新工作流；上限表示最多检索次数。"""

    config = {"configurable": {"thread_id": thread_id}}
    if graph.get_state(config).values:
        raise ValueError("thread_id 已经存在，只能通过 resume_graph 恢复暂停点")
    return graph.invoke(_initial_state(question, max_steps=max_steps), config=config)


def resume_graph(graph, *, thread_id: str, approved: object) -> dict:
    """在同一 ``thread_id`` 上提交恢复值；审批节点会严格验证其类型。"""

    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=approved), config=config)
