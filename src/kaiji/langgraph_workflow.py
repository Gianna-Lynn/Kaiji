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
    TypedDict 表示AgentState的运行时是一个dict.
    ``total=False`` 表示: TypedDict 字段在静态类型层面都可以缺省；
    不表示运行中的 State 可以随意缺少字段，也不自动决定节点如何更新 State。
    注意:
    在本课的 StateGraph 节点契约中，节点返回的是局部更新字典，而不是整份
    State。LangGraph 会把返回的字段写回当前 State，未返回的字段保留原值。
    因此每个节点只应返回自己负责更新的字段，不应顺手覆盖或清空其他职责的字段；
    如果确实要清空某个字段，也必须是该节点契约明确要求的行为。
    
    ``_initial_state`` 会在首次进入图时建立完整状态，后续节点依赖这项不变量。  
    ``question`` 保存不可变的原始用户问题，  
    ``rewritten_query`` 保存当前真正送往 Azusa 的查询。  
    ``step_count`` 只统计 search 节点执行次数，也就是检索尝试次数；  
    ``retry_count`` 只统计 query rewrite 次数；  
    ``max_steps`` 只限制检索尝试次数，并不统计 router、answer 等其他图节点。  
    ``route`` 是 router 选择的下一条边，``evidence`` 是校验后的检索命中，  
    ``error`` 与 ``error_retryable`` 描述最近一次检索失败及其恢复属性。  
    ``pending_action``、``approved`` 分别保存待确认的模拟副作用和人的恢复决定。  
    ``answer`` 与``stop_reason`` 只由终止节点写入，保存可移植的停止原因字符串。  
    """

    question: str   # 这不是在声明类成员变量, 而是在声明字典的键类型.
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
    """创建完整状态，并严格拒绝非正整数检索上限。"""

    if type(max_steps) is not int or max_steps < 1:
        raise ValueError("max_steps 必须至少为 1")

    return AgentState(  # 初始State 里还没有 route。route 将由 Router Node 第一次运行时产生。
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

    # TODO 6b：检查原始 question。
    # 大小写归一化后，只要包含 delete、send 或 publish 之一，
    # 就返回 confirm，并把未改写的原问题保存到pending_action；
    # 其余问题返回 search，不能在路由阶段调用 Adapter。
    # 本节点只返回自己负责更新的state字段, 不得顺手返回或者清空不属于自己职责的字段, 也不可以就地修改字段
    question = state["question"].lower()     
    if any (kw in question for kw in ("delete", "send", "publish")):
        return {
            "route" : "confirm",
            "pending_action" : state["question"],
        }
    else:
        return {
            "route" : "search",
        }

def assess_search(state: AgentState) -> str:
    """按固定优先级, 选择搜索后的下一节点。"""

    # TODO 6c：依照以下优先级返回 answer、max_steps、rewrite 或 failure：
    # 1. 有证据时优先 answer；
    # 2. 无证据且 step_count 达到 max_steps 时返回 max_steps；
    # 3. 尚有预算、error_retryable 为真且 retry_count == 0 时返回 rewrite；
    # 4. 其他情况返回 failure。
    # 这里只读取显式 State，不得另设隐藏的局部重试计数器。
    if state["evidence"]:   # 不能用is not None判定, 因为state["evidence"]是list[dict], list无论是否为空, 本身都不是None
                            # 可以利用空列表的假值特性, 直接判断这个列表, 有东西返回True, 没东西返回False
        return "answer"
    if not state["evidence"] and state["step_count"] >= state["max_steps"]:
        return "max_steps"
    if state["step_count"] < state["max_steps"] and state["error_retryable"] and state["retry_count"] == 0:
        return "rewrite"
    return "failure"

def rewrite_query(state: AgentState) -> dict[str, str | int]:
    """确定性改写查询，并只返回查询和改写计数的局部更新。"""

    # TODO 6d：返回 rewritten_query 与递增后的 retry_count，且只能返回这两个键。
    # 改写必须确定、非空、不同于 question，并保留原始 question；具体追加的
    # 检索词由人决定，但不能修改输入 State 或其他字段。
    
    # rewrite是一种机械加词, 思路是: 反正都要重试了，顺手给查询加点料，万一就成了呢——成了就赚，没成也不亏
    # 适用于下面的情况: 
    # 1. 瞬态失败，不是查询的问题. 但是如果不改查询就重试, 可能打到同一台故障节点、命中同一个缓存错误. 加一点词儿, 可能绕开缓存或者路由的问题.
    # 2. 避免向量检索中遇到冷门词 3. 加一个关键词, 试图命中稀疏关键词搜索, 如BM25.

    return{
        "rewritten_query" : state["rewritten_query"] + " 详细 文档 说明",
        "retry_count" : state["retry_count"] + 1,
    }


def request_confirmation(state: AgentState) -> dict[str, bool]:
    """暂停等待人工决定，并严格要求恢复值是布尔值。"""

    # TODO 6e：调用 interrupt，载荷必须包含稳定的 kind、原始 action 和非空中文
    # question。interrupt 返回的是恢复值，不是载荷本身；只接受真正的 bool，
    # "false"、"拒绝"、0、1 和其他类型必须明确失败，不能使用 bool() 偷偷转换。

    payload = {
        "kind" : "side_effect_confirmation",
        "action" : state["pending_action"],
        # "question" : state["question"],
        "question" : "是否批准这项模拟副作用？",
    }
    human_decision = interrupt(payload) 
    # 中断(1) interrupt: 
    # payload被记录为"等待人工处理的中断信息"; 抛出LangGraph内部的暂停信号;
    # LangGraph 捕获内部暂停信号; Checkpointer 保存当前 thread_id 的State和暂停位置; 
    # 本次 graph.invoke() 暂停; 向调用方返回包含 __interrupt__ 信息的结果, 形如:
    # {
    #     "__interrupt__": (
    #         Interrupt(
    #             value={                   # 访问方式: paused["__interrupt__"][0].value
    #                 "kind": "...",
    #                 "action": "...",
    #                 "question": "...",
    #             },
    #             id="...",
    #         ),
    #     )
    # }
    # Checkpoint中的状态大致形如:
    # {
    #     "pending_action": "send mock report",
    #     "approved": None,
    #     "route": "confirm",
    #     ...
    # }
    # 在测试中, 下面的代码会模拟人类的批准, 并调用resume_graph继续执行图.
    # result = resume_graph(
    #     graph,
    #     thread_id="approve",
    #     approved=True,
    # )   


    # 中断(3): 再次执行confirm节点的request_confirmation
    # LangGraph接到Command(resume=True)的指令; 加载 thread_id 对应的 Checkpoint;
    # 重新执行 confirm 节点; 重新执行 request_confirmation();
    # 重新构造 payload; 再次执行 interrupt(payload);
    # 这一次, LangGraph 发现：当前节点的第 0 个 interrupt, 已经有一个恢复值 True.
    # 于是interrupt(payload) 的语义变成：human_decision = True, 不会再次暂停.
    # 接着执行下面的分支: if human_decison is True:

    # 附注: 同一个节点中的多个 interrupt() 会按照出现顺序编号，
    # 例如第一个是 0、第二个是 1。恢复值会按这个编号匹配。
    # 因此恢复执行时，interrupt() 的调用顺序必须稳定，不能第一次调用了两个，第二次因为条件变化只调用一个。
    if human_decision is True:
        return {"approved": True}
    elif human_decision is False:
        return {"approved": False}
    else:
        raise ValueError(f"必须传入True或者False, 不能是{type(human_decision)}")


def confirmation_route(state: AgentState) -> str:
    """严格校验人工决定，并映射为批准或拒绝分支。"""

    # TODO 6f：approved 是真正的 True 时返回 approved，真正的 False 时返回 rejected；
    # None、0、1、字符串及其他非 bool 值必须明确抛出异常，不能伪装成用户拒绝。
    
    if state["approved"] is True:
        return "approved"
    elif state["approved"] is False:
        return "rejected"
    else:
        raise ValueError(f"approve必须是True或者False, 不能是{type(state['approved'])}")

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
            "stop_reason": StopReason.COMPLETED.value,
        }

    def rejected(state: AgentState) -> dict:
        """生成人工拒绝后的终止状态。"""

        return {
            "answer": None,
            "stop_reason": StopReason.HUMAN_REJECTED.value,
        }

    def answer(state: AgentState) -> dict:
        """只使用第一条已校验证据，并仅引用支撑该正文的命中。"""

        evidence = state["evidence"][0]
        return {
            "answer": f"{evidence['text']}\nCitations: {evidence['chunk_id']}",
            "stop_reason": StopReason.COMPLETED.value,
        }

    def failure(state: AgentState) -> dict:
        """在证据不足且无法继续恢复时安全终止。"""

        return {
            "answer": None,
            "stop_reason": StopReason.INSUFFICIENT_EVIDENCE.value,
        }

    def max_steps(state: AgentState) -> dict:
        """在达到最大检索次数时安全终止。"""

        return {
            "answer": None,
            "stop_reason": StopReason.MAX_STEPS.value,
        }

    def unfinished_graph(_state: AgentState) -> dict:
        """让尚未连边的练习版首先明确停在 TODO 6a。"""

        raise NotImplementedError("TODO 6a：连接 LangGraph 节点与条件边")

    # TODO 6a：删除 todo_6a 占位节点，连接以下真实图结构：
    # START → router
    # router -(state["route"])→ search | confirm
    # search -(assess_search)→ answer | rewrite | failure | max_steps
    # rewrite → search
    # confirm -(confirmation_route)→ approved | rejected

    # approved/rejected/answer/failure/max_steps → END
    # 条件边必须显式给出“条件返回值 → 节点名”的映射；不得把循环、Interrupt
    # 或终止判断藏进 run_graph 外层循环。
    graph = StateGraph(AgentState)
    graph.add_node("router", route_request) # 当LangGraph执行到这个节点的时候,调用route_request(current_state). route_request只返回自己负责的字段, 不会返回整份state.
    graph.add_node("search", lambda state: _search(adapter, state)) # 见:def _search(adapter: AzusaAdapter, state: AgentState) -> dict[str, Any]:
    graph.add_node("rewrite", rewrite_query)
    graph.add_node("confirm", request_confirmation)
    graph.add_node("approved", approved)
    graph.add_node("rejected", rejected)
    graph.add_node("answer", answer)
    graph.add_node("failure", failure)
    graph.add_node("max_steps", max_steps)
    # graph.add_node("todo_6a", unfinished_graph)
    # graph.add_edge(START, "todo_6a")
    # graph.add_edge("todo_6a", END)
    graph.add_edge(START, "router")

    # lambda: Python中的关键字. 意思是"我现在要当场捏一个没有名字的一次性函数"。lambda中, ":"左边是参数名, ":"右边是函数体(返回值)
    # 解释lambda语句. path = lambda state: state["route"] 这句话的含义是:创建一个函数.以后谁调用这个函数, 就把传进来的第一个参数, 临时命名为state.
    #                在本句中, 调用函数的时候传进来的第一个参数是一个字典, 有键值对形如{"route": "search"}. 那么state["route"] 的结果就是 "search".
    #                最终结果是: path参数接受一个函数, 这个函数被调用后返回"search"
    # 下面的lambda语句等效于:
    # def choose_next_node(state: AgentState):
    #     return state["route"]
    # ...
    # path = choose_next_node   # 注意这里赋值号右值是函数的名称, path参数需要一个函数. 
    #                             LangGraph调用这个函数.按照框架约定, LangGraph一旦遇到一个可调用对象,就把当前的state传入自定义函数, 也就是调用choose_next_node(current_state)

    graph.add_conditional_edges(
        source="router",    # 从哪个节点出发
        path = lambda state: state["route"],  # state是一个形参名称. path说明如何从state中取得路由键. state是由langgraph框架持有的.
        path_map= {         # 根据路由键, 映射到目标节点.
            "search": "search",
            "confirm": "confirm",
        },
    )

    # 解释lambda语句. path= lambda state : assess_search(state) 这句话的含义是:创建一个函数.以后谁调用这个函数, 就把传进来的第一个参数, 临时命名为state.
    # 下面的lambda语句等效于:
    # def foo(state: AgentState):
    #     return assess_search(state)
    # ...
    # path = foo
    graph.add_conditional_edges(
        source="search",
        path= lambda state : assess_search(state), # assess_search返回一个字符串, 是路由键.
        path_map={
            "answer":"answer",
            "rewrite":"rewrite",
            "failure":"failure",
            "max_steps":"max_steps",
        }
    )
    graph.add_edge("rewrite", "search") # 这条边只负责rewrite 做完以后回到 search. 真正阻止第二次改写的是下一次 Search 后，assess_search() 对显式 State 中 retry_count 的判断。
    graph.add_conditional_edges(
        source="confirm",
        path= lambda state : confirmation_route(state), 
        path_map={
            "approved":"approved",
            "rejected":"rejected",
        }
    )
    for node in ("approved", "rejected", "answer", "failure", "max_steps"):
        graph.add_edge(node, END)
    # 创建或采用 MemorySaver
    active_checkpointer = checkpointer if checkpointer is not None else MemorySaver()
    return graph.compile(checkpointer=active_checkpointer)


def run_graph(graph, question: str, *, thread_id: str, max_steps: int = 3) -> dict:
    """以未使用的线程开始工作流；``max_steps`` 必须是正整数检索上限。"""

    config = {"configurable": {"thread_id": thread_id}}  # 这里的 thread_id 是 Checkpointer 的存档槽编号
    if graph.get_state(config).values:     # LangGraph会问 MemorySaver：thread_id 这个线程, 是否已经存在 State？
        # 拦截两类线程:
        # 暂停中的线程       → 应该拦截，要求 resume_graph()
        # 已经正常结束的线程 → 也会拦截，避免重复启动
        raise ValueError("thread_id 已经存在，只能通过 resume_graph 恢复暂停点")

    return graph.invoke(_initial_state(question, max_steps=max_steps), config=config) # 如果被interrupt, 那么这里返回的是暂停结果, 而不是最终回答.
    # invoke从已经编译好的图的START节点出发, 一直工作, 直到遇见三种情况之一:
    # 1. 图走到END, 返回最终State. 2. 图遇到interrupt(), 返回暂停状态. 3. 节点抛出未处理的异常, 向外抛出异常.

def resume_graph(graph, *, thread_id: str, approved: object) -> dict:
    """在同一 ``thread_id`` 上提交interrupt所需要的恢复值Command(resume=approved)；审批节点会严格验证其类型。"""

    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=approved), config=config)
    # 中断(2): 恢复(假定resume=True)
    # 调用Command后, Checkpointer 找到该 thread_id 的暂停任务, 
    # LangGraph 将 True 登记为这个待恢复 Interrupt 的恢复值, 但不写入state["approved"].
    # 从 confirm 节点开头重新执行 request_confirmation(), 再次执行到 interrupt(payload)
    # interrupt() 发现当前任务已经有第 0 个恢复值 True, 不再暂停，直接 return True, 得到human_decision = True
    # LangGraph 内部还会给同一节点内的 interrupt() 编号。这里仅有一个，因此它是第 0 个 Interrupt；
    # 恢复时，第 0 个恢复值就交给第 0 个 interrupt()。
    # 当confirm节点恢复执行, 并返回{"approved": True}之后，LangGraph 才会把它合并进 State。

    # 中断(4): 整个中断的流程图:
    # 第一次 invoke(initial_state)
    # ↓
    # router
    #     ↓
    # confirm
    #     ↓
    # interrupt(payload)
    #     ↓
    # 暂停信号
    #     ↓
    # 保存 Checkpoint
    #     ↓
    # 返回 __interrupt__ 给调用方
    #     ↓
    # 人做决定 True / False
    #     ↓
    # Command(resume=决定)
    #     ↓
    # 第二次 invoke(Command(...))
    #     ↓
    # 根据 thread_id 加载 Checkpoint
    #     ↓
    # confirm 节点从头重跑
    #     ↓
    # 再次执行 interrupt(payload)
    #     ↓
    # 取得已登记的 resume 值
    #     ↓
    # interrupt() 返回 True / False
    #     ↓
    # 写入 approved
    #     ↓
    # approved 或 rejected
    #     ↓
    # END