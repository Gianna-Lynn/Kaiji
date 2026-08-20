# Kaiji 学习驾驶台

这份文档主要写给人。它不只是告诉你“哪些测试要通过”，还说明每一阶段为什么存在、
你应该亲手掌握什么，以及什么时候才适合查看标准答案。

## 1. Kaiji 到底是什么

Kaiji 是位于 Azusa 上层的单 Agent 应用工程。

```text
用户问题
  ↓
Kaiji
  ├─ Message / Tool / AgentState
  ├─ Runtime / Workflow
  ├─ Checkpoint / Memory / Human-in-the-loop
  └─ Trace / Eval
       ↓ HTTP Adapter
Azusa
  └─ HNSW / BM25 / Hybrid / Rerank / Evidence Chunk
```

Kaiji 不实现向量索引、BM25 或 RAG 检索；Azusa 也不管理 Agent 的循环、状态和人工
确认。两个仓库只通过以下稳定接口连接：

```python
search_azusa(query, k, mode)
get_chunk_by_id(chunk_id)
```

本课程的目标不是“调用一个现成 Agent 框架”，而是让人能够解释、修改和测试可靠的
单 Agent Runtime，然后理解 LangChain 与 LangGraph 替你隐藏了什么。

## 2. 大橘和人的分工

大橘已经完成：

- ChuoEnv、Python package 和框架依赖配置；
- Pydantic 数据模型、目录结构、Mock、Demo 与测试框架；
- Kaiji 和 Azusa 的完整标准答案分支；
- 不依赖 API key、GPU 或在线服务的离线验证路径；
- 默认不阻塞离线测试、但 TODO 12 必须实际完成的 Azusa BM25 HTTP 联调路径；
- TODO 13—15 的 Provider、Eval 与可靠性实验课程脚手架、受控 fixture 和离线验收路径。

人需要完成：

- `main` 中标记为 `TODO 1`—`TODO 15` 的关键逻辑；
- 每完成一课，运行对应窄测试；
- 在看答案前，先解释自己的设计和失败边界；
- 最后完成 Kaiji 与 Azusa 的真实跨仓库验收。

这不是填空题。测试规定外部行为，但控制流、错误边界和状态推进需要人自己理解后实现。

## 3. 分支怎么使用

Kaiji：

| 分支 | 用途 |
|---|---|
| `main` | 人实际写 TODO 的练习分支 |
| `solution/kaiji-v0-full-course` | 第一批 TODO 1—12 的完整答案与真实 Azusa 集成；第二批课程的答案边界见 `docs/` |

Azusa：

| 分支 | 用途 |
|---|---|
| `main` | 人完成语义网关与 Azusa v2 课程 TODO |
| `solution/azusa-v2` | 可运行的完整参考答案 |

同一 Python 环境的 editable install 只能指向一个工作树。在 Kaiji 答案 worktree 运行
`python -m kaiji.*` 前，应临时把该 worktree 的 `src` 放入 `PYTHONPATH`，并打印
`kaiji.__file__` 确认没有误载 main；具体命令见 `docs/curriculum.md`。回到 main 后清除
该临时变量，避免答案代码越过 TODO。这条检查尤其重要：命令成功不等于导入来源正确。

日常学习始终留在 Kaiji `main`。不要为了看答案直接把 solution 分支合并进 main。

卡住时，先只查看一个文件的差异：

```powershell
git diff main..solution/kaiji-v0-full-course -- src/kaiji/tools.py
```

建议顺序：

1. 自己实现；
2. 运行窄测试并阅读失败；
3. 写下你认为的错误边界；
4. 只比较当前文件；
5. 关闭 diff，重新用自己的方式完成；
6. 再运行测试。

## 4. 开始前确认环境

所有命令默认在 `F:\Repositories\Kaiji` 执行：

```powershell
Conda activate ChuoEnv

python -m pytest tests/test_scaffold.py
python -m kaiji.demo
```

练习初始状态应当是：

- `tests/test_scaffold.py`：3 个测试通过；
- `kaiji.demo`：可运行，但返回 `max_steps`、`steps=0`；
- 全量测试：大量 TODO 测试失败；
- live integration：未设置环境变量时跳过。

这说明环境和契约正常，尚未完成的是人的实现。不要在第一课一开始就以“全量 pytest
必须全绿”为目标；先让当前课的窄测试转绿。

## 5. 推荐学习路线

课程被分成五站。必须按顺序前进，因为后面的 Adapter、Workflow、Eval 和可靠性实验都
依赖前面的工具执行语义。

### 第一站：亲手建立可靠的原生 Tool Agent

#### TODO 1：Tool Registry

##### 这是什么，为什么需要它

Tool Registry 是 Kaiji 的工具目录：集中声明 Agent 被允许看见的工具、输入输出 Schema 与 handler。模型能提出调用，但不能凭自由文本获得任意能力；Registry 让工具边界可审查、可发现。

没有它，工具定义和权限会散落在 Runtime 分支中，新增工具会牵连控制流。本课只负责声明与查找，不决定调用、重试或失败策略；完成后，人能判断一次调用是否指向已注册、Schema 已公开的能力。

文件：`src/kaiji/tools.py`

完成：

- 工具名唯一；
- 按名称查找；
- 向模型暴露 Pydantic JSON Schema；
- Registry 不负责执行策略。

```powershell
python -m pytest tests/test_registry.py
```

完成后应能解释：为什么未知工具由 Executor 转成结构化失败，而不是让 Registry 抛出
运行时异常？
答: Registry只负责检索工作, 返回"某某工具是有还是没有".Executor在拿到Registry的返回结果后,决定怎么使用.Executor有整个 Agent 循环的控制权（State、Budget、Trace、Approval）,由它负责决策.

#### TODO 2：调用前校验

##### 这是什么，为什么需要它

调用前校验是工具边界的门禁：先确认工具存在，再将模型给出的 JSON 参数解析为 Pydantic 输入模型，只有通过后 handler 才能接触网络、文件或业务服务。

模型输出不天然可信。没有这层门禁，拼错名称或缺失参数会混成业务失败、甚至触发副作用，attempts 与重试统计也会失真。本课只拒绝无效请求，不处理 handler 内部的超时和异常；完成后，人能区分“模型填错”与“工具已开始执行但失败”。

文件：`src/kaiji/tools.py`

完成：

- 查找工具；
- 在 handler 运行前校验参数；
- 未知工具与参数错误的 `attempts` 都是 0；
- 参数错误不可重试。

```powershell
python -m pytest tests/test_executor.py -k 'unknown or arguments'
```

完成后应能解释：为什么“没有真正调用 handler”不能算一次 attempt？
答:
attempt 表示一次真正发起 handler 的执行尝试。执行器会先查找工具并校验参数；如果工具不存在，或者参数校验失败，流程会在 handler 调用之前结束，因此没有产生任何 attempt，attempts 应为 0。
一旦参数校验成功并进入 handler 执行阶段，即使 handler 最后超时、抛出不可重试异常，或者返回了非法结果，这次也已经算一次 attempt。attempts 只表示实际执行了多少轮，不表示成功次数，也不表示是否允许重试。
是否继续执行下一轮由 retryable 单独决定。未知工具和参数错误通常不可重试；暂时性网络错误或超时可能允许重试。两者的职责不同：attempts 记录执行次数，retryable 决定失败后是否继续。

#### TODO 3：执行、结果校验与有界重试

##### 这是什么，为什么需要它

Tool Executor 把合法调用请求变成可被 Agent 信任的 ToolResult：它限制等待时间、区分可恢复与不可恢复失败、限制重试次数，并校验 handler 输出。网络会抖动，handler 也可能返回形似成功但结构错误的数据，二者都不能直接进入模型上下文或引用。

没有统一 Executor，每个工具都会各自发明 timeout、错误格式和 retry 规则，造成无限重试、吞错或坏数据伪装成功。本课只做一次额外重试来学习“恢复必须有预算”，不实现生产级退避、熔断或队列；完成后，人能解释一个结果为何可信、一次失败为何停止或再试。

文件：`src/kaiji/tools.py`

完成：

- 超时；
- Pydantic 结果校验；
- 可重试与不可重试错误；
- 最多一次重试；
- 非法结果永远不能伪装成成功。

```powershell
python -m pytest tests/test_executor.py
```

完成后应能画出：第一次失败 → 是否可重试 → 第二次尝试 → 成功或停止。

答:
尝试一次
├─ 成功且输出合法 → success
├─ 超时 → 有额度则重试
├─ ToolExecutionError
│  ├─ retryable → 有额度则重试
│  └─ 不可重试 → stop
├─ 输出 ValidationError → stop
└─ 未预期异常 → stop

#### TODO 4：原生 Agent Loop

##### 这是什么，为什么需要它

原生 Agent Loop 是 Kaiji 最小的受控 Runtime：它反复取得模型动作、执行必要工具、回填已校验结果，并在明确条件下终止。Agent 不是一次模型调用，而是会累积状态、触发外部能力的有限状态机。

没有显式 Loop，重复调用、工具失败、无最终答案和无限思考都会变成隐式行为，既不能诊断，也无法阻止失败结果进入答案。本课建立最小可靠闭环，不实现自主规划、长期记忆或任意代码执行；完成后，人能从 question 追到 final answer 或明确 stop reason。

文件：`src/kaiji/runtime.py`

完成：

- 获取模型动作；
- 工具执行和消息回填；
- 只收集经过校验的成功结果；
- Trace；
- `completed`、`tool_failed`、`duplicate_call`、`max_steps`；
- 重复 name + arguments 调用检测。

```powershell
python -m pytest tests/test_runtime.py
python -m kaiji.demo
```

第一站验收：

```powershell
python -m pytest tests/test_registry.py tests/test_executor.py tests/test_runtime.py
```

此时人应该能脱离代码解释完整 Tool Agent Loop。
答:

```
run(question)
├─ 初始化：message_list=[user], trace_list=[], successful_tool_results=[], seen_call_signatures=set()
│
└─ for step in 1..max_steps
    │
    ├─ model_action = decide(message_list)      ← 每轮先让模型决策
    ├─ trace += model_action                    ← 记录模型动作
    │
    ├─ kind == "final_answer"?
    │   ├─ final_answer is None ──→ raise ValueError
    │   ├─ trace += stop(COMPLETED)
    │   └─ return COMPLETED                     ← 出口①
    │
    ├─ kind == "tool_call"?
    │   ├─ tool_call is None ──→ raise RuntimeError
    │   ├─ 生成 signature(name+arguments)
    │   ├─ signature ∈ seen?
    │   │   ├─ trace += stop(DUPLICATE_CALL)
    │   │   └─ return DUPLICATE_CALL            ← 出口②
    │   ├─ seen += signature
    │   ├─ messages += assistant(tool_call)
    │   ├─ tool_result = executor.execute(tool_call)
    │   ├─ tool_result is None ──→ raise ValueError
    │   ├─ trace += tool_result
    │   ├─ messages += tool(tool_result)
    │   │
    │   ├─ tool_result.ok?
    │   │   ├─ 是 → successful_tool_results += tool_result → 回到循环头
    │   │   └─ 否 → trace += stop(TOOL_FAILED)
    │   │            └─ return TOOL_FAILED      ← 出口③
    │   │
    ├─ 其他 kind ──→ raise ValueError
    │
    └─ 循环自然耗尽
        ├─ trace += stop(MAX_STEPS)
        └─ return MAX_STEPS                     ← 出口④
```


### 第二站：理解框架究竟替你做了什么

#### TODO 5：LangChain 对照

##### 这是什么，为什么需要它

这一课把 TODO 1—4 中亲手实现的消息与工具边界映射到 LangChain 的 Message、StructuredTool 与 Runnable。框架能减少组合代码，却不会自动替人定义合法输入、可信结果或 timeout、权限责任。

没有这层对照，学习框架 API 容易变成“能跑但不知道发生什么”的记忆题。本课用最小且有损的映射揭示框架包装的机械工作，不把 Kaiji 的可靠性边界交给框架；完成后，人能指出原生对象的框架对应物与仍由 Kaiji 负责的部分。

文件：`src/kaiji/langchain_compare.py`

这一课只比较表达方式，不改变 Kaiji 已经建立的可靠性边界。当前 `Message` 没有
LangChain 原生的 `tool_calls` 字段，因此这里采用明确的教学用有损映射：
  
| Kaiji 消息 | LangChain 消息 | 字段契约 |
|---|---|---|
| `role="user"` | `HumanMessage` | 原样保留 `content` |
| `role="assistant"` | `AIMessage` | 原样保留 `content`，不伪造结构化 `tool_calls` |
| `role="tool"` | `ToolMessage` | 原样保留 `content`，并从 `tool_result.call_id` 取得 `tool_call_id` |

`tool` 消息缺少 `tool_result` 属于协议破坏，必须明确报错，不能制造
`unknown-call`；反过来，`user` 或 `assistant` 携带 `tool_result` 也是角色与载荷冲突，
必须拒绝。当前对照不引入额外“模型决策”类型，Runnable 的最终输出就是经过校验的
`SearchResponse`，避免留下没有调用方的近义字段。

StructuredTool 必须直接复用 `SearchAzusaArgs`，而不是另造一份相似 Schema：

| 参数 | 契约 |
|---|---|
| `query` | 至少 2 个字符，必填 |
| `k` | `1..20`，默认 `3` |
| `mode` | 非空字符串，默认 `hybrid`；允许值由具体 Adapter 决定 |

完成：

- Kaiji Message 与 LangChain Message 的映射；
- 使用 `SearchAzusaArgs` 的 `StructuredTool`，并验证默认值与参数边界；
- 用两个 `RunnableLambda` 表达“调用工具 → `SearchResponse` 校验”；
- 用非法 Adapter 输出证明结构化输出校验确实生效；
- 分别验证成功和失败 Callback。

练习按依赖顺序实现：**5a Message 映射 → 5b StructuredTool → 5c 两阶段
Runnable**。5c 会调用 5b 的工具；测试会用 monkeypatch 单独隔离 5c，因此某条 5c
测试越过尚未完成的 5b 是有意的单元隔离，不代表 5b 已经通过。

```powershell
python -m pytest tests/test_langchain_compare.py -k message
python -m pytest tests/test_langchain_compare.py -k structured_tool
python -m pytest tests/test_langchain_compare.py -k runnable
```

本课非目标是实现完整的 LangChain 模型 Tool Calling，也不把超时、重试或权限责任
交给框架。工具边界仍由 Kaiji Adapter、Schema 和 Executor 决定。组合 Runnable
可能让同一个 Callback 在不同嵌套层各收到事件，因此本课只要求成功链出现 start/end、
失败链出现 error，不把某个框架版本的精确回调次数当成业务契约。

```powershell
python -m pytest tests/test_langchain_compare.py
```

重点不是记 API，而是回答：原生版本中哪些逻辑被框架包装了？哪些可靠性责任仍然属于
Kaiji？

#### TODO 6：LangGraph 状态工作流

##### 这是什么，为什么需要它

LangGraph 工作流把多步控制流显式表达为可持久化的 State、Node、Edge 与条件分支。它适合“检索失败后只改写一次”和“有副作用前必须人工确认”这类流程：执行到哪里、为何跳转、暂停后如何继续都成为状态机的一部分。

没有显式工作流，重试计数、审批决定和恢复位置容易藏在局部变量或调用栈中；中断后无法可靠恢复，也难审查是否绕过审批。本课用 LangGraph 表达既定流程，不重新实现 TODO 4 的完整 Tool Agent，也不把框架 Checkpoint 混同于长期 Memory；完成后，人能解释 Interrupt 前后哪些状态被保存与恢复。

文件：`src/kaiji/langgraph_workflow.py`

##### 先知道这一课最终要做出什么

这一课不是要求人从零实现一套 Agent Runtime，而是把一个已经确定的检索与审批流程，
改用 LangGraph 的 **State、Node、Edge、Conditional Edge、Checkpoint 和 Interrupt**
表达出来。最终产物应能沿三条主路径运行：

```text
普通问题：初始化 State → Router → Search → 条件判断 → Answer → END
空证据或可恢复错误：Search → 条件判断 → Rewrite → Search（最多一次）
模拟副作用：Router → Confirm → Interrupt 暂停 → Command(resume=...) → Approved/Rejected → END
```

完成后，人应当能把 TODO4 中亲手维护的状态变量、`if/elif`、循环跳转和暂停恢复，逐项
对应到 LangGraph 的 State、节点、边和 Checkpoint；不要求在本课重新实现 TODO4 的
Message、Trace、ToolExecutor 或完整 Agent Loop。

##### 先分清哪些已经提供，哪些需要人实现

| 已经提供的课程脚手架 | 人需要完成的 TODO |
|---|---|
| `AgentState` 字段与 `_initial_state()` | 6b：`route_request()` Router 节点 |
| `_search()` 以及 Answer/Failure/Max Steps 等终止节点 | 6c：`assess_search()` 条件边判断 |
| `MemorySaver` 的创建与注入 | 6d：`rewrite_query()` 局部 State 更新 |
| `run_graph()` 与 `resume_graph()` 调用入口 | 6e：`request_confirmation()` Interrupt 节点 |
| 正确答案分支中已经验证过的完整工作流 | 6f：`confirmation_route()` 审批条件边判断 |
| 每个子任务的窄测试和完整集成测试 | 6a：在 `build_agent_graph()` 中组装整张图 |

Search、Answer、Failure、Max Steps 和 Checkpointer 接线是供人阅读与复用的脚手架，
不是本课要求重新设计的隐藏 TODO。真实副作用、生产级持久化、模型 Tool Calling 和
ToolExecutor 可靠性也不属于本课实现范围。

先明确 State 字段的职责：

`AgentState` 使用 `TypedDict(total=False)`：它只表示字段在静态类型层面可以缺省，
不表示运行中的 State 可以随意缺少基础字段。`_initial_state()` 会在首次进入图时
建立完整状态，后续节点依赖这项不变量。

本课的节点返回局部更新字典，LangGraph 会将其合并进当前 State，未返回的字段保留原值；
因此 rewrite 只更新查询与重试计数，审批节点也不能顺手清空证据、原问题或其他无关字段。

| 字段 | 含义 |
|---|---|
| `question` | 原始用户问题，不因改写而覆盖 |
| `rewritten_query` | 下一次真正发送给 Azusa 的查询 |
| `route` | Router 选择的 `search` 或 `confirm` 条件边 |
| `evidence` | 最近一次成功响应中经过 Pydantic 校验的命中 |
| `retry_count` | 已执行的 query rewrite 次数，最多一次 |
| `step_count` | `search` 节点执行次数，也就是实际检索尝试次数 |
| `max_steps` | 检索尝试上限，必须是正整数；`bool`、浮点数、字符串和 `None` 均非法 |
| `error` / `error_retryable` | 最近一次检索失败及其恢复属性 |
| `pending_action` | 等待人工确认的模拟副作用请求 |
| `approved` | Interrupt 恢复时写入的人工决定 |
| `answer` / `stop_reason` | 终止节点写入的对外结果；LangGraph State 中的 `stop_reason` 是可移植字符串 |

先沿着实际执行顺序认识这张图。这里的 `assess_search()` 和
`confirmation_route()` 是 **条件边使用的路由函数，不是独立 Node**：

```text
START
  ↓
Router Node
  ├─ search  → Search Node
  │               ↓
  │          assess_search(state)〔条件边判断，不是 Node〕
  │               ├─ 有证据     → Answer → END
  │               ├─ 可恢复     → Rewrite → Search（最多一次）
  │               ├─ 达到上限   → Max Steps → END
  │               └─ 不可恢复   → Failure → END
  │
  └─ confirm → Confirmation Node(执行request_confirmation, 等待人工决定)
                  ↓
             interrupt(payload) 暂停并保存 Checkpoint
                  ↓
             外部取得人的 True / False
                  ↓
             Command(resume=decision)
                  ↓
             Confirmation Node返回 {"approved": decision}(人工决定)
                  ↓
             confirmation_route(state)〔条件边判断〕
                  ├─ approved → Approved → END
                  └─ rejected → Rejected → END
```

##### 推荐的第一次施工顺序

编号 6a—6f 用于稳定定位课程文件和测试，并不代表初学者最自然的施工顺序。第一次学习
建议先完成职责单一的函数，理解它们的输入输出，最后再用 6a 把零件组装成图：

| 顺序 | 修改位置 | 这一小步真正学习什么 | 完成后先运行 |
|---|---|---|---|
| 0 | 只阅读 `AgentState`、`_initial_state()` 和已提供节点 | 哪些数据必须跨步骤保存在 State；哪些节点只返回局部更新 | 暂不写代码 |
| 1 | 6b `route_request()` | Node 读取完整 State，只返回自己负责的字段 | `pytest -k todo_6b` |
| 2 | 6c `assess_search()` | 条件边路由函数读取 State，返回下一节点名称，但不更新 State | `pytest -k todo_6c` |
| 3 | 6d `rewrite_query()` | 循环所需的新查询和计数为什么必须写回显式 State | `pytest -k todo_6d` |
| 4 | 6e `request_confirmation()` | `interrupt()` 如何暂停，以及恢复值如何成为它的返回值 | `pytest -k todo_6e` |
| 5 | 6f `confirmation_route()` | 恢复值写入 State 后，如何严格分流到批准或拒绝节点 | `pytest -k todo_6f` |
| 6 | 6a `build_agent_graph()` | 注册 Node，连接普通边与条件边，并编译完整工作流 | `pytest -k todo_6a` |
| 7 | 不再挖新 TODO | 运行三条主路径，观察 State、停止原因、暂停与恢复是否一致 | 完整 TODO6 测试 |

6a 的图结构测试不会执行尚未完成的节点，因此它在技术上可以最先完成；但第一次接触
LangGraph 时，先理解 6b—6f 的返回值，再连接条件边，更容易知道每条边为什么存在。

下面保留的是完整验收契约。第一次施工时不需要把它一次背完：完成某个子 TODO 后，
回到对应段落核对边界；完成整张图后，再从头通读一次。

`assess` 的判断优先级固定为：有证据先回答；否则达到 `max_steps` 时停止；否则仅在
错误可重试且尚未改写时改写一次；最后以证据不足失败。最后一次允许的检索如果成功，
仍应回答。合法但没有命中的 `SearchResponse` 在本课中视为可恢复的空证据，因此允许
触发一次 query rewrite；具体改写句式不是公开契约，但必须保留原始 `question`、让
`rewritten_query` 发生变化，并且最多执行一次。每次 search 都把当前
`rewritten_query`、固定 `k=3` 和 `mode="hybrid"` 原样传给 Adapter。

回答节点当前只使用排名第一的证据正文，因此也只能引用第一条 `chunk_id`；不能把没有
用于回答的其他命中一并列为引用。`delete`、`send`、`publish` 视为模拟副作用，必须在
任何检索发生前进入 Interrupt；中断负载包含稳定的 `kind`、原始 `action` 和非空中文
问题。批准结果必须包含待执行 action，拒绝则没有答案；具体自然语言模板不是契约。
审批恢复值必须是 Python 的 `bool`；`"false"`、`"拒绝"`、`0`、`1`、`None`
等其他类型不得被 truthy/falsy 转换成批准或拒绝，非法恢复值应明确失败。`request_confirmation`
和 `confirmation_route` 都必须执行这项严格校验：前者拒绝非法 Interrupt 恢复值，后者还要
防御被破坏的 AgentState，不能把内部协议错误伪装成用户拒绝。
`max_steps` 必须是真正的 Python 正整数；`True`、浮点数、`NaN`、字符串、`None` 和小于 1
的整数都必须在进入图前抛 `ValueError`。类型标注不会执行运行时校验，不能依赖比较运算或
truthy/falsy 语义代替严格类型检查。测试不绑定异常文案。

LangGraph Checkpoint 的 State 必须由可移植的基础类型组成，因此终止节点把 `StopReason`
枚举保存为 `.value` 字符串，而不是直接保存自定义枚举对象；需要恢复成枚举时，应在对外
模型边界显式转换。这样调用方注入的 `MemorySaver` 也不会依赖自定义类型白名单。

不同 `thread_id` 的暂停点和恢复决定必须隔离，包括两个线程同时处于 Interrupt、再按
不同顺序恢复的情况。`build_agent_graph(checkpointer=...)` 必须使用调用方传入的
`MemorySaver`，不能悄悄另建全局存储。

`resume_graph()` 的前置条件是同一图中存在该 `thread_id` 的待恢复 Interrupt；未知或已经
终止的线程如何转成产品级错误不在本课范围内，当前允许 LangGraph 抛出框架异常。
`run_graph()` 只接受尚未使用的 `thread_id`；重复启动同一线程（包括仍停在 Interrupt 的
暂停线程）会明确抛出 `ValueError`，不能用它覆盖或延续已有运行。暂停线程必须保留原始
`pending_action` 和 Interrupt，并通过 `resume_graph()` 继续。

本课与 TODO4 的横向边界：LangGraph 主要替代显式 State 变量、`if/elif` 分支、循环
跳转、暂停与恢复编排；它没有整体替代 TODO4 的 `AgentRuntime`。本实现的 Search 节点
直接调用 `AzusaAdapter`，不经过 `ToolExecutor`，因此不会自动获得 ToolExecutor 的
超时、有限重试、`ToolResult`/`ToolFailure` 结构化失败，也不包含原生 Runtime 的
`Message`、`Trace`、重复工具调用检测和停止流程。这里的 `retry_count` 只表示 query
rewrite 次数，不是工具执行重试次数。

本课完成时，人应能在现有脚手架中辨认 AgentState、Node、普通 Edge、Conditional Edge、
Checkpoint、Interrupt 和 `Command(resume=...)` 各自承担的职责，并解释它们怎样共同替代
TODO4 中相应的手工编排。每个子任务都有独立的纯函数或图结构验收；完成当前子任务后，
窄测试只反映当前边界，完整集成测试仍可能因其他 TODO 尚未完成而失败。

```powershell
python -m pytest tests/test_langgraph_workflow.py -k todo_6b
python -m pytest tests/test_langgraph_workflow.py -k todo_6c
python -m pytest tests/test_langgraph_workflow.py -k todo_6d
python -m pytest tests/test_langgraph_workflow.py -k todo_6e
python -m pytest tests/test_langgraph_workflow.py -k todo_6f
python -m pytest tests/test_langgraph_workflow.py -k todo_6a
```

每完成一步，只要求对应窄测试变绿；其他窄测试和完整工作流测试仍可因尚未完成的 TODO
失败。完成 6b—6f 并最后连接 6a 后，再运行完整工作流测试：

```powershell
python -m pytest tests/test_langgraph_workflow.py
```

完成后应能解释：为什么 retry_count 必须存在于显式 State，而不能藏在节点局部变量里？

这里的 LangGraph `MemorySaver` 是框架为 thread 暂停/恢复提供的 checkpointer；TODO 8
的 `JsonCheckpointStore` 是独立的持久化教学实现。两者本课不自动接线，也不能把
LangGraph Checkpoint 误当成长期 Memory。

### 第三站：跨边界、恢复和记忆

#### TODO 7：Azusa Adapter

##### 这是什么，为什么需要它

Azusa Adapter 是 Kaiji 与检索系统间的防腐层：它将 HTTP、旧版 vector/record 协议和远端失败翻译成稳定的 `SearchResponse`、`ChunkResponse` 与结构化错误。Kaiji 只需要可信证据，不应依赖 Azusa 内部检索实现；Azusa 也无需了解 Agent Loop、Trace 或审批。

没有 Adapter，网络与兼容细节会渗入 Registry、Runtime 和模型消息，接口演进会牵连整条 Agent 链。本课建立稳定的只读服务边界与旧接口兼容，不实现 Azusa 内部检索、缓存、并发调优或无限重试；完成后，人能替换 Mock、语义 HTTP 与 Legacy Adapter 而保持上层语义一致。

施工主线先建立两条类型流。正常数据链是：
`ToolCall.arguments → SearchAzusaArgs / GetChunkArgs → Registry handler → AzusaAdapter
→ SearchResponse / ChunkResponse（Pydantic 对象）→ model_dump(mode="json") → JSON
兼容 dict → ToolExecutor.result_model 再校验 → ToolResult.output`。失败链是：
`httpx / response validation / local dependency → AzusaAdapterError（retryable 必须是
真正的 bool）→ Registry 的 ToolExecutionError → ToolExecutor 的 attempts、timeout
和有限重试 → ToolResult.error`。

Adapter 返回 Pydantic 对象，handler 返回 JSON 兼容字典，只有 `ToolResult.output` 才是
Runtime 和模型消费的成功结果。Adapter 只分类，不在内部重试同一个 HTTP 操作。Semantic
search/get 各自只有一个计划内 HTTP 请求；Legacy search 是多阶段操作：一次 `/search`
加零到多次 `/record/{id}` 请求，不是“每个方法一次请求”。
Registry 不重新实现 HTTP；ToolExecutor 才负责 attempts、timeout 和有限重试。Semantic
Adapter 表达稳定网关，Legacy Adapter 是 vector/record 到稳定证据接口的兼容翻译层，
Mock Adapter 只用于离线确定性教学；本课没有实现 Azusa 内部 HNSW、RAG 或网关服务。

文件：

- `src/kaiji/adapters/azusa.py`
- `src/kaiji/azusa_tools.py`

稳定语义接口的传输契约是：

```text
POST /search_azusa
{"query": <str>, "k": <int>, "mode": <str>}

GET /chunks/{chunk_id}
```

`chunk_id` 作为路径段时必须做 URL 编码；网关返回的 `ChunkResponse.chunk_id` 必须与
请求值一致，否则不能把别的证据误配给本次调用。

错误分类统一为：

| 失败 | `retryable` |
|---|---:|
| 参数错误或 HTTP 3xx | `False` |
| HTTP 429 | `True` |
| 除 429 外的 HTTP 4xx | `False` |
| HTTP 5xx | `True` |
| timeout / network error | `True` |
| 非法 JSON 或响应 Schema 不匹配 | `True` |

3xx 在本课作为不可重试的 HTTP 错误处理。429 表示服务端限流，稍后再次调用可能恢复，
因此单独标记为可重试；除 429 外的其他 4xx 表示当前请求本身存在问题，标记为不可重试。
HTTP 5xx、timeout 和网络错误可能来自瞬时故障，因此标记为可重试。

这只是错误分类，不表示 Adapter 自己执行重试。Adapter 每次只发起一次计划内 HTTP
操作；真正的重试由 `ToolExecutor` 根据工具的 `max_retries=1` 执行，最多额外尝试一次。
当前版本不解析 `Retry-After`，也不等待或执行无界退避。

| 失败所在层 | 行为 |
|---|---|
| Pydantic 工具参数不合法 | Executor 在调用 Adapter 前拒绝，`attempts=0` |
| Legacy 本地 encoder/resolver 抛异常或返回非法值 | `retryable=False` |
| 远端 timeout/network/429/5xx/非法 JSON/Schema | `retryable=True` |
| 远端除 429 外的 4xx | `retryable=False` |

- Mock Adapter；
- 语义 HTTP Adapter；
- 当前 Rust `/search` 的兼容 Adapter；
- 4xx/5xx/timeout 的重试边界；
- AdapterError 到 ToolExecutionError 的转换；
- `search_azusa` 与 `get_chunk_by_id` 工具注册。

Legacy Adapter 只接受 `hnsw`，向 `/search` 发送 `vector`、`k` 和固定
`ef_search=16`，再逐条读取 `/record/{id}`。它必须保留 `score/source/page`，从
metadata 恢复 `chunk_id`，再用注入的 resolver 恢复正文。实际 Legacy 响应包含
`vector`；Kaiji 不消费它，因此允许缺失，但存在时必须是有限数值列表。encoder 必须返回非空
有限数值列表（允许 int/float，内部统一转成 float），其中元素必须有限且不能是布尔值；resolver 必须返回非空、非纯空白
字符串。metadata 中的 `chunk_id` 同样必须是非空、非纯空白字符串，不能把任意对象用
`str()` 强行伪装成标识。`record_id` 是兼容层保留字段，远端 metadata 已有该键时明确
失败；否则在副本上追加旧服务的 `record_id`，不修改远端字典。search hit ID 和 record
ID 必须类型和值都一致（例如 `7` 与 `"7"` 不匹配）。多命中采用全有或全无，任一 record
或 resolver 失败都不返回部分 SearchResponse。
本地依赖错误不可通过重复 HTTP 调用恢复；并发、缓存和连接池调优不在本课范围内。

两个 Registry 工具都必须原样传播参数：显式 `query/k/mode`、默认 `k=3/mode=hybrid`
以及 `chunk_id` 都不能被 handler 硬编码替换。search 和 get_chunk 的
`AzusaAdapterError.retryable` 都必须传入 `ToolExecutionError`，不能只修其中一个。

练习按编号推进：**7a 语义搜索 HTTP 边界 → 7b 按 ID 取证据 → 7c Legacy 兼容转换 →
7d Registry 工具接线**。7a 与 7b 共用错误分类但各自需要测试；7d 位于最后，因为它要
把已经稳定的 Adapter 方法接入 Executor，而不是在 handler 内重新实现网络逻辑。

每一步的窄验收命令如下；只完成当前步骤时，不应被后续步骤的未实现方法掩盖：

```powershell
python -m pytest tests/test_azusa_adapter.py -k todo_7a
python -m pytest tests/test_azusa_adapter.py -k todo_7b
python -m pytest tests/test_azusa_adapter.py -k todo_7c_resolver
python -m pytest tests/test_azusa_adapter.py -k todo_7c_encoder
python -m pytest tests/test_azusa_adapter.py -k todo_7c_search
python -m pytest tests/test_azusa_adapter.py -k todo_7c_get_chunk
python -m pytest tests/test_azusa_adapter.py -k todo_7d
```

7a 只验语义搜索，7b 只验语义取证；7c 依次验 resolver、encoder、Legacy search/record
转换和 Legacy `get_chunk_by_id()`；7d 才验参数 Schema、Registry、错误传播、timeout
和 Executor 重试。7a–7c 期间后续 Registry 不应成为反馈前置条件，全部完成后再运行
`python -m pytest tests/test_azusa_adapter.py`。

Registry 的两个工具默认 `timeout_seconds=4.0`，而 HTTP Adapter 默认网络 timeout 是
3.0 秒。httpx timeout 是网络层单次操作/阶段的限制，不是整个 Legacy handler 的总期限；
Executor timeout 是等待整个 handler 的外层期限，两者是不同范围的计时器，简单的 3 < 4
不能保证 Legacy 内层一定先结束。外层超时无法取消已开始的线程，重试可能与原调用重叠；
当前工具只读，因此 v0 接受该限制，有副作用工具不能照搬。`build_azusa_registry` 只
校验自身 timeout 为有限正数，不能自动知道或证明 Adapter 整体最大耗时。3 秒和 4 秒
只是教学默认值，不是普遍安全保证；Adapter timeout 增大时由调用方负责重新配置，但
本课不强制一个无法观察的组合大小关系。

```powershell
python -m pytest tests/test_azusa_adapter.py
```

这里先使用 MockTransport，不需要启动 Azusa。重点是让网络、检索和 Runtime 保持清晰
边界。

#### TODO 8：Checkpoint

##### 这是什么，为什么需要它

Checkpoint 是一次运行在某个时刻的完整恢复快照，回答“这条任务中断后从哪里继续”。例如审批前暂停的工作流需要保存问题、待办动作与已取得证据，之后才能在同一 thread 上继续，而不是从头猜测。

没有 Checkpoint，运行只能依赖内存；进程退出、服务重启或人工等待时，执行位置与状态都会丢失。原子写入和显式损坏报错避免半份快照伪装成正常状态。本课只实现单机文件快照，不解决多进程并发、冲突合并或通用业务 State 校验；完成后，人能区分它与 LangGraph MemorySaver、长期 Memory 的职责。

文件：`src/kaiji/persistence.py`

Checkpoint 是一次执行的完整恢复快照。JSON 第一层按 `thread_id` 隔离，第二层按
`checkpoint_id` 保存 `state`。相同线程和编号再次保存时覆盖旧状态，并把这次成功的
`save` 视为最新写入；因此覆盖旧编号后，该编号也必须成为 `latest()` 的结果。

`load()` 和 `latest()` 找不到目标时返回 `None`，恢复出的数据必须重新经过
`Checkpoint` 外壳校验。键不存在才表示“找不到”；键存在但值为 JSON `null`、列表或
其他非法结构属于存储损坏，必须报错，不能伪装成 `None`。这里的 `state` 类型只是
`dict[str, Any]`，Store 只能保证快照外壳可读，不能证明它符合某个具体 `AgentState`；
调用方恢复业务状态时仍须使用自己的 State Schema 校验。

写入采用同目录临时文件再 `replace` 目标；测试会模拟替换失败，确认旧文件和旧
`latest()` 不受影响。多进程锁、冲突合并和崩溃后临时文件清扫不属于本课范围。

练习按真实依赖顺序实现：**8a `_read_all()` 严格读取 → 8b `save()` 原子保存 →
8c `load()` 指定恢复 → 8d `latest()` 最近恢复**。后三个方法都复用 8a；不要按调用
入口的熟悉程度先写 `save()`，否则会被尚未建立的存储边界阻塞。

```powershell
python -m pytest tests/test_persistence.py -k todo_8a
python -m pytest tests/test_persistence.py -k todo_8b
python -m pytest tests/test_persistence.py -k todo_8c
python -m pytest tests/test_persistence.py -k todo_8d
python -m pytest tests/test_persistence.py -k checkpoint
```

#### TODO 9：Memory

##### 这是什么，为什么需要它

Memory 是调用方明确选择、跨运行长期保存的少量事实，例如用户偏好或稳定设置；它不是聊天全文，也不是精确恢复执行位置的 Checkpoint。它让未来任务使用经过选择和隔离的信息，而不必永久携带整段历史或内部状态。

没有这层区分，Message History、运行 State 和长期事实会混在一起，既难控制作用域，也会把临时状态误作用户偏好。本课只提供可靠存取、覆盖和用户隔离，不自动判断该记住什么，也不做向量记忆、摘要或召回；完成后，人能解释一条数据为何应写进 Memory 而非其他状态容器。

文件：`src/kaiji/persistence.py`

Memory 是调用方策略选择长期保留的少量事实，不是整段运行状态。Store 不负责猜测
什么值得记忆，只负责以下稳定行为：

- 使用 `user_id + entry.key` 隔离事实；
- 相同用户和 key 再次写入时覆盖值，但保持首次插入顺序；
- `get()` 找不到时返回 `None`，`list()` 找不到用户时返回空列表；
- 恢复的每条数据都重新经过 `MemoryEntry` 校验，`tags` 必须原样往返；
- 不同用户的数据不得串读。

Memory 同样严格区分“键不存在”和“键存在但记录损坏”：JSON `null`、空对象或字段
不合法都必须让 `get()` 与 `list()` 一致报错。外层存储 key 还必须等于记录内部的
`entry.key`，否则无法可靠判断覆盖和查询的是哪条事实。`put()` 在写入新事实前也必须
校验已有 Store；不能借一次看似成功的新写入，把旧损坏静默保留下去。Memory 与
Checkpoint 共用同目录临时文件再 `replace` 的原子写入边界，替换失败时旧文件不变。

练习同样从读取边界开始：**9a `_read_all()` 全量校验 → 9b `put()` → 9c `get()` →
9d `list()`**。这样写入、单条读取和列表读取会共享同一套损坏数据结论，不会各自长出
三种互相矛盾的解析规则。

```powershell
python -m pytest tests/test_persistence.py -k todo_9a
python -m pytest tests/test_persistence.py -k todo_9b
python -m pytest tests/test_persistence.py -k todo_9c
python -m pytest tests/test_persistence.py -k todo_9d
python -m pytest tests/test_persistence.py -k memory
```

完成后应能用自己的话区分：

- Message History；
- AgentState；
- Checkpoint；
- Memory。

### 第四站：证明系统可靠，而不只是“看起来能跑”

#### TODO 10：Trace 保存与重放

##### 这是什么，为什么需要它

Trace 是一次已结束 Agent 运行的可审计过程记录：模型做了什么、工具怎样返回、最终为何停止。它把“这次回答不对”的模糊印象变成可逐步查看和比较的事实，也是调试、事故分析与评测取证的基础。

没有 Trace，最终答案无法说明依据或失败位置，重放也可能因记录含糊产生不同理解。本课保证 Trace 的结构、持久化和稳定的人类可读回放，不替业务判断答案正确性，也不实现分布式链路追踪或并发 writer；完成后，人能用 run_id 还原停止原因和事件顺序。

文件：`src/kaiji/tracing.py`

完成 JSONL append、load、迭代和人类可读 replay。这里的字段职责是：

- `TraceRecord` 表示一次已经终止的完整运行；`metadata` 保存 run 级标签、实验配置等
  检索信息，不代替逐事件详情；
- `events` 保持 Runtime 产生的原始顺序；每个 `TraceEvent.data` 只描述该事件当时的
  结构化事实，不是 Message 正文，也不是 run 级 metadata；
- `TraceRecord.stop_reason` 是便于索引和统计的终止摘要，必须与唯一且位于末尾的
  `stop` Event 的 `data.reason` 相同；空 events、无 stop、多个 stop、stop 后仍有事件、
  缺失或非法 reason 都属于结构破坏；
- 文件中每个非空行只能包含一个完整 `TraceRecord` JSON；
- 追加到外部创建且最后一行没有换行的合法文件时，必须先补物理行分隔符，不能把两个
  JSON 对象粘在同一行；
- 缺失文件视为空存储，空白行忽略，非法非空行必须显式报错；
- `run_id` 在同一文件中必须唯一，重复 append 应拒绝；
- replay 每行固定包含 `step/event/data`，其中 `data` 使用排序键的紧凑 JSON，保证
  同一结构得到稳定文本。

Trace Store 负责结构一致性、存储与回放，不负责判断最终答案在业务上是否正确。追加前
扫描唯一 `run_id` 的实现只面向单进程教学；并发 writer 与文件锁不在本课范围内。

练习按依赖顺序实现：**10a TraceRecord 不变量 → 10b 严格迭代读取 → 10c 按 ID 加载 →
10d 唯一追加 → 10e 稳定回放**。其中 append 需要先调用 load 查重，load 又复用
iter_records；源码中的方法排列也遵循这一依赖顺序。

```powershell
python -m pytest tests/test_tracing.py -k todo_10a
python -m pytest tests/test_tracing.py -k todo_10b
python -m pytest tests/test_tracing.py -k todo_10c
python -m pytest tests/test_tracing.py -k todo_10d
python -m pytest tests/test_tracing.py -k todo_10e
python -m pytest tests/test_tracing.py
```

#### TODO 11：固定 Eval

##### 这是什么，为什么需要它

固定 Eval 是一组可重复运行的题目与统一评分口径，用于检验 Agent 是否持续完成应做的事，而不只是偶然演示出一段不错的答案。它将工具选择、参数、重试、引用、步骤和延迟变为可比较指标，使改动后能够定位具体哪项能力退化。

没有固定用例和稳定分母，“成功率”会随测试集合、记录顺序或缺失字段悄悄改变，团队也难区分模型选错、工具失败与统计错误。本课建立确定性 case/observation 聚合边界，不把金标 fixture 当作真实 Agent Eval，也不解决开放域质量或在线 A/B 实验；完成后，人能说明每项指标回答的问题及其分母为何固定。

文件：

- `src/kaiji/evaluation.py`
- `eval/cases.jsonl`

聚合 20 条固定 case，计算：

- Task Success Rate；
- Tool Selection / Argument Accuracy；
- Tool Success Rate；
- Retry Recovery Rate；
- 不必要工具调用率；
- 平均步骤数；
- P50 / P95；
- 停止原因分布；
- Citation Supported。

`EvalCase` 描述期望，`EvalObservation` 描述一次实际观测，`EvalReport` 只负责聚合。
报告中的 Task Success 要求观测本身成功、停止原因符合期望，而且工具调用数不超过
`max_tool_calls`；答案或业务内容是否正确仍由 runner 写入 `task_success`。用于测试的
`reference_observation()` 是指标管线的确定性金标，不是真实 Agent Eval。

runner 必须按下表把运行事实统一映射为 Observation，不能到写聚合器时再猜字段：

| 字段 | 唯一口径 |
|---|---|
| `selected_tool` | 模型在本 case 中的第一次工具选择；即使参数在执行前被拒也应记录 |
| `arguments_correct` | 上述选择的参数是否满足本 case 预期；case 不要求工具时为 `None` |
| `tool_calls` | 实际进入工具 handler 的执行尝试数；可累加 `ToolResult.attempts`，参数预校验失败可为 0 |
| `successful_tool_calls` | 上述执行尝试中产生校验后成功结果的次数 |
| `retry_recovered` | 先发生可恢复失败，随后经 Executor retry 或 query rewrite 最终恢复；Provider transport retry 不计入；不适用为 `None` |
| `unnecessary_tool_calls` | 按 case 契约判断本可省略的实际执行尝试数 |
| `steps` | 状态机消耗的逻辑迭代；Native 用 `RunResult.steps`，检索图用 `step_count`，非检索终止至少为 1 |
| `latency_ms` | runner 接受 case 到得到终止结果的墙钟毫秒，包含 Adapter 和内部重试时间 |
| `citation_supported` | 所有引用均来自成功且校验过的证据，并实际支持回答；不适用为 `None` |

覆盖契约要求 case ID 和 observation ID 各自唯一，而且两边集合完全相等。适用指标的
观测值如果为 `None`，按失败计入既定分母，不能静默缩小分母。分母固定为：

Observation 输入顺序不承担身份语义，聚合器必须先按 `case_id` 对齐，再依照 case 文件
顺序计算；反转 Observation 列表不得改变报告。当某项指标没有任何适用 case，或没有
发生实际工具调用时，对应比率明确为 `0.0`，不能出现除零、`NaN` 或通过跳过字段改变口径。

| 指标 | 分母 |
|---|---|
| Task Success | 全部 case |
| Tool Selection / Argument Accuracy | `expected_tool` 非空的 case |
| Retry Recovery | `expects_retry_recovery=True` 的 case |
| Citation Supported | `expects_citation=True` 的 case |
| Tool Success / Unnecessary Tool Call | 实际工具调用总次数 |
| Average Steps / P50 / P95 | 全部 observation |

P50/P95 使用排序后的 nearest-rank 定义。所有计数和延迟不得为负，成功或不必要调用数
不得超过实际调用数。测试必须使用混合成功/失败观测给出精确分数；全满分样本不能单独
证明公式正确。

`reference_observation()` 只构造与 case 预期停止状态内部一致的合成观测，用来证明
指标公式；它不得把预期失败的工具伪造成成功，也不能证明 20 条 Agent 行为已经跑过。
`run_eval(cases, runner)` 才是真实 runner 的注入点。把统一 Runtime、LangGraph、
Checkpoint 和 Memory 场景接入同一个真实黑盒 runner 属于后续 L3 扩展，而不是把金标
fixture 改名就宣称完成。

练习按依赖顺序实现：**11a Observation 跨字段不变量 → 11b 唯一 ID 索引 →
11c JSONL 用例加载 → 11d `_align_observations()` 严格对齐 →
11e `_aggregate_aligned()` 指标公式**。公开的 `aggregate()` 只负责按顺序组合 11b、11d
与 11e；这样每个子题都有不依赖后续实现的窄反馈。11a 的合法样例
必须继续可构造，所以它不能用无条件 `NotImplementedError` 占位；相应红灯会表现为
“非法计数组合没有被拒绝”。11b/11c 的重复 ID 红灯同理，是精确契约失败而非测试松散。

```powershell
python -m pytest tests/test_evaluation.py -k todo_11a
python -m pytest tests/test_evaluation.py -k todo_11b
python -m pytest tests/test_evaluation.py -k todo_11c
python -m pytest tests/test_evaluation.py -k todo_11d
python -m pytest tests/test_evaluation.py -k todo_11e
python -m pytest tests/test_evaluation.py
python -m kaiji.eval_demo
```

注意分母：没有要求工具调用的 case，不应进入工具选择准确率的分母。

#### TODO 12：真实 Azusa HTTP 联调

##### 这是什么，为什么需要它

真实 HTTP 联调让课程从 Mock 小闭环走向跨仓库事实闭环：Kaiji 真实请求 Azusa，获得检索命中，再按 `chunk_id` 取回同一份证据正文。它验证的不只是某个类在隔离测试中“像是正确”，而是双方关于 URL、Schema、ID 与证据语义的契约能跨进程共同成立。

没有这一步，Mock 测试全绿仍可能掩盖路由、端口或序列化不一致。本课只验证最小 BM25 数据闭环，不把它误当作真实语料质量、性能压测或生产部署验收；因此 live test 必须实际通过，不能以 skip 代替。完成后，人能证明成功引用来自 Azusa 实际返回且可按 ID 再次取得的证据。

这一课跨越两个仓库。普通全量测试在未设置 `AZUSA_GATEWAY_URL` 时会 skip live test；
因此 TODO 12 的完成条件不是“全量测试没有失败”，而是该文件明确显示 `1 passed`，不能
是 `1 skipped`。标准答案闭环使用 Azusa 的 `solution/azusa-v2` 先验证；人真正完成
课程时必须在自己的 `main` 上得到同样结果。两个分支都包含网关脚本，但命令仍须显式
切换分支，避免把答案态与课程态混淆。

本机 Windows 将 `2991..3090` 列为 TCP 排除端口，默认 `3010` 会触发
`WinError 10013`。下面统一使用已经实测通过的 `38117`；在其他机器也可以选择任意未占用、
未被系统排除的端口，只要启动参数与 `AZUSA_GATEWAY_URL` 保持一致。

先在 Azusa 完成练习：

```powershell
Set-Location F:\Repositories\Azusa
git switch main
Set-Location rag

python -m pytest tests/test_kaiji_gateway.py
```

测试全绿后启动语义网关：

```powershell
python run_kaiji_gateway.py --port 38117 --embedding-path tests/fixtures/kaiji_gateway_embedding.jsonl
```

在另一个终端回到 Kaiji：

```powershell
Set-Location F:\Repositories\Kaiji
$env:AZUSA_GATEWAY_URL = 'http://127.0.0.1:38117'

python -m pytest -q -rs tests/test_azusa_live_integration.py
```

这条测试默认使用 Azusa 教学分支中受 Git 跟踪的最小
`tests/fixtures/kaiji_gateway_embedding.jsonl`，因此新 clone 不需要本机模型、GPU 或先生成
`output/artifacts/embedding.jsonl`。该 fixture 只证明 HTTP 与 BM25 数据闭环，不代表真实
语料质量评测：

1. 真实 BM25 搜索 stromules；
2. 验证命中证据包含 `1962`；
3. 获取命中的 chunk_id；
4. 再按 chunk_id 取回完整正文；
5. 验证两次正文一致。

完成后可以清除当前终端的临时变量：

```powershell
Remove-Item Env:AZUSA_GATEWAY_URL
```

### 第五站：把 Eval 做成可复现的可靠性实验

TODO 13—15 是第一批课程之后的第二批训练。它们不是把 TODO 1—12 的功能机械重写
一遍，而是逐步撤掉脚手架：TODO13 练 Provider 协议边界，TODO14 练 Runner、Scorer
和 SQLite 生命周期，TODO15 练完整的故障对照、恢复和实验产物。三课默认都走离线
Mock/Scripted 路径，不读取 API key，也不能把替身结果描述成真实模型成绩。

详细的公开契约、非目标、字段追踪和反向验收记录分别见：
`docs/reliability-course-l1-l3.md`、`docs/reliability-eval-contract.md`、
`docs/reliability-eval-traceability.md` 和 `docs/reliability-course-validation.md`。
本节给出主线上的学习目标、依赖顺序和验收入口；如果本节与详细契约出现冲突，以
详细契约和测试中的明确边界为准，并应先修正文档冲突再继续实现。

#### TODO 13：Provider 协议边界

##### 这是什么，为什么需要它

Provider 是模型服务与 Kaiji Runtime 之间的协议边界：它把 System Prompt、完整消息历史
和工具 Schema 变成一次请求，再把服务响应解析成严格的 assistant 文本或单个工具动作。
本课的输入是 Kaiji 的消息、工具定义和 Provider 配置，输出是可供 Runtime 消费的成功
结果或结构化 Provider 失败；Provider 自己不得隐藏重试，也不得把鉴权信息带入请求记录、
Trace 或错误文本。

本课只实现 `src/kaiji/providers/openai_compatible.py` 已给出的接口，不新增 Provider
抽象，不做并行工具调用、流式协议、真实模型回归或服务压测。关键不变量是：每个模型回合
最多一个工具调用；assistant 工具调用后必须紧跟同 `tool_call_id`、同工具名的 tool
结果；关键字段严格校验；非法 JSON、非有限数值、协议错配和空白最终回答不得进入成功路径。

练习按依赖顺序实现：**13a 请求载荷 → 13b 消息与调用 ID 配对 → 13c 响应解析 →
13d 请求元数据与失败分类**。13d 还必须明确区分 timeout、transport、429、5xx、其他
4xx、非法 JSON 和非法协议，并只将前四类标记为可重试；真正的重试预算由上层实验控制。

```powershell
python -m pytest tests/test_openai_compatible_provider.py -k todo_13a
python -m pytest tests/test_openai_compatible_provider.py -k todo_13b
python -m pytest tests/test_openai_compatible_provider.py -k todo_13c
python -m pytest tests/test_openai_compatible_provider.py -k todo_13d
python -m pytest tests/test_openai_compatible_provider.py
```

完成后，人应能解释：为什么 Provider 只能报告“可重试”，而不能自行再次发送请求？
为什么兼容扩展字段可以忽略，但会被 Kaiji 消费的动作字段不能依赖隐式类型转换？

#### TODO 14：Runner、Scorer 与 SQLite

##### 这是什么，为什么需要它

TODO14 把一次真实 Runtime 运行封装成可评分的 Episode，并把 Runner、确定性 Scorer 和
官方 SQLite Checkpointer 的生命周期接起来。输入是固定 Case、Provider/Executor 工厂
和运行配置；输出是保存未经裁剪的 `RunResult`、模型调用元数据、评分结果以及可跨新连接
恢复的 Checkpoint。Runner 不能用自己的 `task_success` 自报成功，Scorer 必须从动作、
参数、回答、停止原因和成功证据重新计算。

本课必须为每个 case 隔离 Provider、Registry 和故障状态；`repeat_index` 只能是真正的
非负 `int`；每个 Episode 有唯一 `run_id`，并保留墙钟延迟。`max_steps` 和 duplicate
guard 来自 Case/Harness 契约，不能被 Runner 偷换成无限循环或隐式默认值。

`required_answer_facts` 只做确定性的字面量检查：回答和事实分别经过 NFKC、大小写折叠
和连续空白折叠后，事实必须作为连续片段出现；事实两端若为 ASCII 字母、数字或下划线，
不能与回答中的同类 ASCII 字符相邻，所以 `90` 不匹配 `190`，CJK 字符不参与该边界。
它不做语义蕴含、否定识别、同义词、翻译或数值等价判断；空白事实和重复事实在 Case
构造时拒绝。

`selected_tool` 表示模型第一次选择的工具，即使参数在 handler 前被拒也应记录；
`tool_calls` 只表示实际进入 handler 的执行尝试数，因此 `selected_tool` 非空而
`tool_calls=0` 并不矛盾。它不是 Message 数，也不是模型 action 数。`retry_recovered`
在 Native 中只观察同一工具调用内 `ToolResult.attempts` 体现的 Executor retry；LangGraph
的 query rewrite 读取自己的 `retry_count`/`step_count`；Provider transport retry
属于传输可靠性，不计入该业务恢复指标。

聚合前必须校验 case ID、observation ID、`run_id` 唯一，并按 ID 完全对齐；输入顺序不
承担身份语义。缺失 observation 不能缩小分母，适用指标为 `None` 时按既定失败口径处理，
没有适用项时报告 `0.0`。Tool Success、Citation Precision/Recall、Steps、Token、延迟、
停止原因和失败分布都必须使用文档规定的固定分母与 nearest-rank P50/P95。

SQLite 部分只实现官方同步 Saver 的正确生命周期：创建父目录、上下文退出关闭连接、
关闭第一个 Graph/连接后仍能由新 Graph/连接按同一 `thread_id` 恢复，并拒绝旧 Schema、
非真正 Python `bool` 审批值和跨线程状态泄漏。它不做分布式 Checkpoint 或并发写入。

练习按依赖顺序实现：**14a 隔离 Runtime Episode → 14b 单条确定性评分 → 14c 固定分母
聚合 → 14d 官方 SQLite Checkpointer**。

```powershell
python -m pytest tests/test_eval_runner.py -k todo_14a
python -m pytest tests/test_eval_scorer.py -k todo_14b
python -m pytest tests/test_eval_scorer.py -k todo_14c
python -m pytest tests/test_sqlite_checkpoint_resume.py -k todo_14d
python -m pytest tests/test_eval_runner.py tests/test_eval_scorer.py tests/test_sqlite_checkpoint_resume.py
```

完成后，人应能解释：为什么 Runner 的成功声明不能替代 Scorer？为什么一次参数预校验
失败可以留下工具选择，却不能增加 `tool_calls`？为什么 Provider 第一次网络失败不能
单独让 Episode 通过恢复评分？

#### TODO 15：Reliability Experiment

##### 这是什么，为什么需要它

TODO15 将固定数据、故障注入、Harness、Checkpoint 恢复和报告产物组织成一次可复现的
实验。输入是冻结的数据集、配置、Provider、重复次数和请求预算；输出是带 Manifest、
Episode JSONL、报告、坏案例和恢复账本的实验目录。L3 不预先规定内部类和辅助函数，
但必须保留状态不变量、故障边界和可审计产物。

四个 Harness 只能切换一次有界 retry 和 duplicate guard，其他 Schema、Prompt、Provider、
数据、温度、最大步骤和重复次数必须相同：

| Harness | Retry | Duplicate guard |
|---|---:|---:|
| `bounded_baseline` | 关 | 关 |
| `no_retry` | 关 | 开 |
| `no_duplicate_guard` | 开 | 关 |
| `full_kaiji` | 开 | 开 |

练习按依赖顺序实现：**15a 受控 Harness → 15b 实验矩阵与产物 → 15c Harness Ablation →
15d 可恢复 Formal**。

- 15a 必须保持所有实验有界，只允许切换上述两个机制。
- 15b 必须执行完整 `case × repeat` 矩阵，冻结数据/配置 hash，写出
  `episodes.jsonl`、`report.json`、`report.md` 和 `bad_cases.json`；已有实验目录不得覆盖。
  普通 Experiment 失败时不生成可被误读为正式成绩的 `incomplete` 报告。
- 15c 必须在相同输入下比较四组成功率、恢复率、Steps 和延迟，并解释 retry、重复调用
  保护及正常路径开销分别改变了什么。
- 15d 每条 Episode 立即追加到 flush/fsync 的 hash-chain JSONL；恢复以
  `(case_id, repeat_index)` 为唯一坐标，只能跳过已完成坐标。每次真实请求先记
  `started`，响应后记 Token；未决请求必须阻断自动恢复。完整报告要求
  `requests_started == responses_finished`，部分报告不得聚合；hash 链只能检测未被重算
  的损坏，不能证明真实性或作者身份。

普通练习最后运行：

```powershell
python -m pytest tests/test_harnesses.py -k todo_15a
python -m pytest tests/test_experiment.py -k todo_15b
python -m pytest tests/test_experiment.py -k todo_15c
python -m pytest tests/test_formal_runner.py -k todo_15d
python -m pytest tests/test_experiment.py tests/test_harnesses.py tests/test_formal_runner.py
python -m kaiji.experiment --provider scripted --suite smoke
python -m kaiji.experiment --provider scripted --suite reliability
```

真实 DeepSeek Formal 与 Azusa Live 不属于普通练习回归。只有人在完成全部离线题后，另行
明确凭据、请求、费用和数据发送授权，才可以运行它们；Scripted/Mock 的成绩只能证明
数据、动作、恢复和评分管线自洽，不能表述为真实模型或开放域 Benchmark 成绩。

## 6. 最终验收

Kaiji：

```powershell
Set-Location F:\Repositories\Kaiji
python -m pytest -rs
python -m kaiji.course_demo
python -m kaiji.eval_demo
```

第二批 Reliability Eval 的离线验收：

```powershell
Set-Location F:\Repositories\Kaiji
python -m pytest tests/test_openai_compatible_provider.py
python -m pytest tests/test_eval_runner.py tests/test_eval_scorer.py tests/test_sqlite_checkpoint_resume.py
python -m pytest tests/test_harnesses.py tests/test_experiment.py tests/test_formal_runner.py
python -m kaiji.experiment --provider scripted --suite smoke
python -m kaiji.experiment --provider scripted --suite reliability
```

Azusa 练习分支：

```powershell
Set-Location F:\Repositories\Azusa
git switch main
Set-Location rag
python -m pytest tests/test_kaiji_gateway.py
python run_kaiji_gateway.py --port 38117 --embedding-path tests/fixtures/kaiji_gateway_embedding.jsonl
```

保持网关运行，在另一个终端完成不能跳过的最终闭环：

```powershell
Set-Location F:\Repositories\Kaiji
$env:AZUSA_GATEWAY_URL = 'http://127.0.0.1:38117'
python -m pytest -q -rs tests/test_azusa_live_integration.py
# 必须看到 1 passed，随后执行 Remove-Item Env:AZUSA_GATEWAY_URL
```

代码全绿还不是最终完成。人还应当能够回答：

1. 一次工具调用从模型动作到成功证据经历了哪些校验？
2. 哪些失败可重试，为什么最多一次？
3. Runtime 和 LangGraph 分别在哪里阻止无限循环？
4. Interrupt 前后的 State 与 Checkpoint 如何变化？
5. History、State、Checkpoint、Memory 为什么不能混为一谈？
6. Kaiji 为什么只依赖 AzusaAdapter？
7. 失败工具结果为什么不能进入引用？
8. Eval 中各指标的分母是什么？
9. Provider 为什么不能隐藏网络重试，实验账本又为什么必须记录每次请求？
10. Runner、Scorer、Checkpoint 和 Formal ledger 分别负责什么，哪些状态不能跨 case 或跨线程泄漏？
11. 为什么 Scripted/Mock Formal 的成绩不能表述为真实模型成绩？

## 7. 下钻资料

- 快速命令索引：`docs/curriculum.md`
- 架构与仓库边界：`docs/architecture.md`
- 原生、LangChain、LangGraph 对照：`docs/framework-comparison.md`
- Azusa 网关作业（切到 `main` 或 `solution/azusa-v2`
  后可见）：`F:\Repositories\Azusa\rag\docs\kaiji_gateway_assignment.md`
- Azusa 主线对接说明：`F:\Repositories\Azusa\docs\kaiji_integration.md`
- TODO13—15 Reliability Eval L1/L2/L3：`docs/reliability-course-l1-l3.md`
- Reliability 课程答案绿灯/练习红灯记录：`docs/reliability-course-validation.md`
- Reliability Eval 固定契约：`docs/reliability-eval-contract.md`
- 强制要求到直接测试追踪：`docs/reliability-eval-traceability.md`

学习时以本文件为主线；需要复制命令时看 curriculum，需要理解边界时看 architecture，
需要比较框架时看 framework-comparison。


## 8. 一点笔记

### dump: 倾倒, 转储, 倾泻而出.

下面是Pydantic模型的用法:
model_dump()  返回 Python字典. 字典的值可能还是枚举对象, 或者其他合法的字典值.
model_dump(mode="json") 返回 JSON兼容的Python字典. 字典的值已经被转换为JSON可以表示的类型.
model_dump_json()  返回JSON字符串, 类型是str.
model_dump(mode="json") 和 model_dump_json()都不是严格有限JSON校验, 可能把NaN序列化为null. 如果需要严格验证, 应该使用 json.dumps(..., allow_nan=False)

下面是json库的用法:
json.dump(data, file) 把JSON内容直接写入已经打开的文件. 这里的对象指的是"可以被转换成JSON的Python对象". 在Python中, dict, list, str等几乎都是对象.
常见用法如下:
```
with open("data.json", "w", encoding="utf-8") as file:
    json.dump(data, file)
```
json.dumps(Python对象)  dumps的-s表示string. json.dumps()接收Python对象, 返回JSON字符串, 类型是str.  
json.dumps(Python对象, allow_nan=False) 明确禁止 NaN, Infinity 和 -Infinity

下面是组合用法:
record = TraceRecord(...)
json.dumps(record) 是不可行的, 因为record是pydantic模型, 是自定义对象.
json.dumps(record.model_dump(mode="json")) 可行
record.model_dump_json() 可行.

### validate:
validate是 Pydantic 的用法. 用来: 检查外部输入, 转换成符合模型定义的Python对象.
PydanticItem.model_validate(data)  把Python字典或Python对象转换成PydanticItem. 这个过程中会完成: 字段类型校验,字符串转换成枚举, 嵌套模型转换, Field约束检查, 和自定义model_validator的执行.
PydanticItem.model_validate_json(data)  输入的是JSON字符串, 转成PydanticItem.
PydanticItem.model_validate_strings(data)  输入的是一个字典, 字典中所有的值都是字符串. model_validate_strings会把这些字符串转换成目标类型.

field_validator 只关注指定字段名(可以是一个或者多个字段), 如@field_validator("字段名", "另一个字段名"), 但是每次调用只处理当前的字段值. 通俗的说, 对于这些指定的字段一个一个去校验, 不关注这些字段之间的关系.

model_validator 检查整个模型, 可以同时检查多个字段之间的关系.可以指定模型生成前/后校验, 如@model_validator(mode="after")

### 文件
一个Path对象, 形如 PathItem = Path("data/traces.jsonl")
PathItem的父目录是 PathItem.parent
若父目录不存在, 可以PathItem.parent.mkdir(parents=True, exist_ok=True)
PathItem.exists() 判存在
PathItem.stat().st_size() 判大小, 推断是不是空文件
with self._path.open("a", encoding="utf-8") as file: "a" 是 append，追加模式. "w"是擦除重写.
    ...with语句会自动完成close
    file.write("要往里写入的内容")

with self._path.open("rb") as file: "rb"是按二进制只读方式打开.
    file.seek(-1, 2) # 以文件末尾（2 = os.SEEK_END）为基准，向后倒退 1 个字节（-1）, 把文件指针放在这里.
    last_byte = file.read(1) # 读取文件指针开始的一个字节
