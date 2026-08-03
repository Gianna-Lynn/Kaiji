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
- 默认不阻塞离线测试、但 TODO 12 必须实际完成的 Azusa BM25 HTTP 联调路径。

人需要完成：

- `main` 中标记为 `TODO 1`—`TODO 12` 的关键逻辑；
- 每完成一课，运行对应窄测试；
- 在看答案前，先解释自己的设计和失败边界；
- 最后完成 Kaiji 与 Azusa 的真实跨仓库验收。

这不是填空题。测试规定外部行为，但控制流、错误边界和状态推进需要人自己理解后实现。

## 3. 分支怎么使用

Kaiji：

| 分支 | 用途 |
|---|---|
| `main` | 人实际写 TODO 的练习分支 |
| `solution/kaiji-v0-full-course` | 12 课完整答案与真实 Azusa 集成 |

Azusa：

| 分支 | 用途 |
|---|---|
| `main` | 稳定主线和对接说明 |
| `todo/kaiji-semantic-gateway` | 人完成语义网关 TODO |
| `solution/kaiji-semantic-gateway` | 可运行的语义网关答案 |

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

课程被分成四站。必须按顺序前进，因为后面的 Adapter、Workflow 和 Eval 都依赖前面的
工具执行语义。

### 第一站：亲手建立可靠的原生 Tool Agent

#### TODO 1：Tool Registry

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

文件：`src/kaiji/langgraph_workflow.py`

先明确 State 字段的职责：

`AgentState` 使用 `TypedDict(total=False)`：每个节点只返回自己负责的局部更新，
LangGraph 再把它合并进当前 State；节点返回字典不是整份状态的替代品。因此 rewrite
只更新查询与重试计数，审批节点也不能顺手清空证据、原问题或其他无关字段。
这里的 `total=False` 只约束节点可以返回局部更新，不表示运行中的 State 可以随意缺少
基础字段；`_initial_state()` 会在首次进入图时建立完整状态，后续节点依赖这项不变量。

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

实现状态图：

```text
问题 → Router → Search → Assess
                    ├─ 有证据 → Answer
                    ├─ 可恢复 → Rewrite → Search（最多一次）
                    ├─ 达到上限 → Max Steps
                    └─ 不可恢复 → Failure

副作用请求 → Interrupt → 人工批准 / 拒绝 → Resume
```

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

完成 AgentState、Node、Edge、Conditional Edge、Checkpoint、Interrupt 和
`Command(resume=...)`。

`main` 的练习子任务按依赖顺序编号：**6a 先连接图 → 6b Router → 6c Assess →
6d Rewrite → 6e Interrupt → 6f 审批条件边**。每个子任务都有独立的纯函数或图结构
验收；完成当前子任务后，窄测试应只反映当前边界，后续集成测试仍可能因后续 TODO 失败。

```powershell
python -m pytest tests/test_langgraph_workflow.py -k todo_6a
python -m pytest tests/test_langgraph_workflow.py -k todo_6b
python -m pytest tests/test_langgraph_workflow.py -k todo_6c
python -m pytest tests/test_langgraph_workflow.py -k todo_6d
python -m pytest tests/test_langgraph_workflow.py -k todo_6e
python -m pytest tests/test_langgraph_workflow.py -k todo_6f
```

完成 6a 后应先看到图结构测试通过，而不是执行未完成节点；完成 6b—6f 后再运行完整
工作流测试。完整测试命令如下：

```powershell
python -m pytest tests/test_langgraph_workflow.py
```

完成后应能解释：为什么 retry_count 必须存在于显式 State，而不能藏在节点局部变量里？

这里的 LangGraph `MemorySaver` 是框架为 thread 暂停/恢复提供的 checkpointer；TODO 8
的 `JsonCheckpointStore` 是独立的持久化教学实现。两者本课不自动接线，也不能把
LangGraph Checkpoint 误当成长期 Memory。

### 第三站：跨边界、恢复和记忆

#### TODO 7：Azusa Adapter

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
| 参数或其他 HTTP 4xx | `False` |
| HTTP 5xx | `True` |
| timeout / network error | `True` |
| 非法 JSON 或响应 Schema 不匹配 | `True` |

Adapter 只负责分类并抛出 `AzusaAdapterError`，自身不循环重试；`ToolExecutor` 在
`max_retries=1` 的工具定义下决定是否再执行一次。完成：

| 失败所在层 | 行为 |
|---|---|
| Pydantic 工具参数不合法 | Executor 在调用 Adapter 前拒绝，`attempts=0` |
| Legacy 本地 encoder/resolver 抛异常或返回非法值 | `retryable=False` |
| 远端 timeout/network/5xx/非法 JSON/Schema | `retryable=True` |
| 远端 4xx | `retryable=False` |

- Mock Adapter；
- 语义 HTTP Adapter；
- 当前 Rust `/search` 的兼容 Adapter；
- 4xx/5xx/timeout 的重试边界；
- AdapterError 到 ToolExecutionError 的转换；
- `search_azusa` 与 `get_chunk_by_id` 工具注册。

Legacy Adapter 只接受 `hnsw`，向 `/search` 发送 `vector`、`k` 和固定
`ef_search=16`，再逐条读取 `/record/{id}`。它必须保留 `score/source/page`，从
metadata 恢复 `chunk_id`，再用注入的 resolver 恢复正文。encoder 必须返回非空
`list[float]`，其中元素是有限数值且不能是布尔值；resolver 必须返回非空、非纯空白
字符串。metadata 中的 `chunk_id` 同样必须是非空、非纯空白字符串，不能把任意对象用
`str()` 强行伪装成标识。命中的完整 metadata 原样保留，并追加旧服务的 `record_id`。
本地依赖错误不可通过重复 HTTP 调用恢复；并发、缓存和连接池调优不在本课范围内。

两个 Registry 工具都必须原样传播参数：显式 `query/k/mode`、默认 `k=3/mode=hybrid`
以及 `chunk_id` 都不能被 handler 硬编码替换。search 和 get_chunk 的
`AzusaAdapterError.retryable` 都必须传入 `ToolExecutionError`，不能只修其中一个。

练习按编号推进：**7a 语义搜索 HTTP 边界 → 7b 按 ID 取证据 → 7c Legacy 兼容转换 →
7d Registry 工具接线**。7a 与 7b 共用错误分类但各自需要测试；7d 位于最后，因为它要
把已经稳定的 Adapter 方法接入 Executor，而不是在 handler 内重新实现网络逻辑。

```powershell
python -m pytest tests/test_azusa_adapter.py -k semantic_http
python -m pytest tests/test_azusa_adapter.py -k legacy
python -m pytest tests/test_azusa_adapter.py -k registry
```

```powershell
python -m pytest tests/test_azusa_adapter.py
```

这里先使用 MockTransport，不需要启动 Azusa。重点是让网络、检索和 Runtime 保持清晰
边界。

#### TODO 8：Checkpoint

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
python -m pytest tests/test_persistence.py -k checkpoint_read_boundary
python -m pytest tests/test_persistence.py -k checkpoint
```

#### TODO 9：Memory

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
python -m pytest tests/test_persistence.py -k memory_read_boundary
python -m pytest tests/test_persistence.py -k memory
```

完成后应能用自己的话区分：

- Message History；
- AgentState；
- Checkpoint；
- Memory。

### 第四站：证明系统可靠，而不只是“看起来能跑”

#### TODO 10：Trace 保存与重放

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
python -m pytest tests/test_tracing.py
```

#### TODO 11：固定 Eval

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
| `retry_recovered` | 先发生可恢复失败，随后经 Executor retry 或 query rewrite 最终恢复；不适用为 `None` |
| `unnecessary_tool_calls` | 按 case 契约判断本可省略的实际执行尝试数 |
| `steps` | 状态机消耗的逻辑迭代；Native 用 `RunResult.steps`，检索图用 `step_count`，非检索终止至少为 1 |
| `latency_ms` | runner 接受 case 到得到终止结果的墙钟毫秒，包含 Adapter 和内部重试时间 |
| `citation_supported` | 所有引用均来自成功且校验过的证据，并实际支持回答；不适用为 `None` |

因此 `selected_tool` 非空而 `tool_calls=0` 并不矛盾：模型可能选了工具，但参数 Schema
在 handler 前拒绝了它。`tool_calls` 不是 Message 数，也不是模型 action 数。

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
11c JSONL 用例加载 → 11d case/observation 严格对齐 → 11e 指标聚合**。11a 的合法样例
必须继续可构造，所以它不能用无条件 `NotImplementedError` 占位；相应红灯会表现为
“非法计数组合没有被拒绝”。11b/11c 的重复 ID 红灯同理，是精确契约失败而非测试松散。

```powershell
python -m pytest tests/test_evaluation.py
python -m kaiji.eval_demo
```

注意分母：没有要求工具调用的 case，不应进入工具选择准确率的分母。

#### TODO 12：真实 Azusa HTTP 联调

这一课跨越两个仓库。普通全量测试在未设置 `AZUSA_GATEWAY_URL` 时会 skip live test；
因此 TODO 12 的完成条件不是“全量测试没有失败”，而是该文件明确显示 `1 passed`，不能
是 `1 skipped`。标准答案闭环使用 Azusa 的 `solution/kaiji-semantic-gateway` 先验证；
人真正完成课程时必须在自己的 `todo/kaiji-semantic-gateway` 上得到同样结果。Azusa
`main` 当前不保证含有网关脚本，因此任何命令都必须先显式切分支。

本机 Windows 将 `2991..3090` 列为 TCP 排除端口，默认 `3010` 会触发
`WinError 10013`。下面统一使用已经实测通过的 `38117`；在其他机器也可以选择任意未占用、
未被系统排除的端口，只要启动参数与 `AZUSA_GATEWAY_URL` 保持一致。

先在 Azusa 完成练习：

```powershell
Set-Location F:\Repositories\Azusa
git switch todo/kaiji-semantic-gateway
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

## 6. 最终验收

Kaiji：

```powershell
Set-Location F:\Repositories\Kaiji
python -m pytest -rs
python -m kaiji.course_demo
python -m kaiji.eval_demo
```

Azusa 练习分支：

```powershell
Set-Location F:\Repositories\Azusa
git switch todo/kaiji-semantic-gateway
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

## 7. 下钻资料

- 快速命令索引：`docs/curriculum.md`
- 架构与仓库边界：`docs/architecture.md`
- 原生、LangChain、LangGraph 对照：`docs/framework-comparison.md`
- Azusa 网关作业（切到 `todo/kaiji-semantic-gateway` 或 `solution/kaiji-semantic-gateway`
  后可见）：`F:\Repositories\Azusa\rag\docs\kaiji_gateway_assignment.md`
- Azusa 主线对接说明：`F:\Repositories\Azusa\docs\kaiji_integration.md`

学习时以本文件为主线；需要复制命令时看 curriculum，需要理解边界时看 architecture，
需要比较框架时看 framework-comparison。
