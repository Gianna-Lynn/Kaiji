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
    返回或清空不属于自己职责的字段。

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

        # TODO 6b：检查原始 question。大小写归一化后，只要包含 delete、
        # send 或 publish 之一，就返回 confirm，并把未改写的原问题保存到
        # pending_action；其余问题返回 search，不能在路由阶段调用 Adapter。
        raise NotImplementedError("TODO 6b：实现只读检索与副作用确认路由")

    def search(state: AgentState) -> dict:
        """调用 Azusa 检索，并把成功结果或结构化错误写回状态。

        ``step_count`` 在每次真实调用前递增。合法但没有命中的响应不是协议
        异常，而是可重试的空证据；Adapter 错误则保留自身的 ``retryable``。
        后续成功必须清除上一次失败留下的 ``error`` 与恢复标记。
        """

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

        # TODO 6c：依照上述优先级返回 answer、max_steps、rewrite 或
        # failure。这里读取的是 State，不得另设隐藏的局部重试计数器。
        raise NotImplementedError("TODO 6c：实现搜索结果的条件边判断")

    def rewrite(state: AgentState) -> dict:
        """改写查询并增加重试计数，避免无界恢复循环。"""

        # TODO 6d：返回新的 rewritten_query 与递增后的 retry_count。
        # 改写必须确定、非空、不同于 question，并保留原始 question；具体
        # 追加哪些检索词不是公开契约，但不能修改其他 State 字段。
        raise NotImplementedError("TODO 6d：实现一次有界的查询改写")

    def request_confirmation(state: AgentState) -> dict:
        """在模拟副作用前暂停工作流，等待人的明确决定。"""

        # TODO 6e：调用 interrupt 暂停，并把恢复值转换为 bool 后写入
        # approved。中断负载必须包含：
        # - kind="side_effect_confirmation"；
        # - action=state["pending_action"]；
        # - 非空且包含“批准”的中文 question。
        # interrupt 返回的是恢复时传入的值，不是中断负载本身。
        raise NotImplementedError("TODO 6e：实现副作用前的 Interrupt")

    def confirmation_route(state: AgentState) -> str:
        """把人工决定映射为批准或拒绝分支。"""

        # TODO 6f：approved 为真时返回 approved，否则返回 rejected。
        raise NotImplementedError("TODO 6f：实现人工决定的条件边判断")

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

    def unfinished_graph(_state: AgentState) -> dict:
        """让尚未连边的练习版首先明确停在 TODO 6a。"""

        raise NotImplementedError("TODO 6a：连接 LangGraph 节点与条件边")

    # TODO 6a：先连接真实工作流，并删除 todo_6a 占位节点及其两条边。
    # 只有先完成接线，后续测试才会进入 TODO 6b—6f 的节点逻辑：
    # START → router
    # router -(state["route"])→ search | confirm
    # search -(assess)→ answer | rewrite | failure | max_steps
    # rewrite → search
    # confirm -(confirmation_route)→ approved | rejected
    # approved/rejected/answer/failure/max_steps → END
    #
    # 条件边必须显式给出“条件返回值 → 节点名”的映射。不得把 retry、
    # Interrupt 或终止判断藏进 run_graph 外层循环。
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
    graph.add_node("todo_6a", unfinished_graph)
    graph.add_edge(START, "todo_6a")
    graph.add_edge("todo_6a", END)
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
