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
- 可选的真实 Azusa BM25 HTTP 联调路径。

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
| `solution/native-agent-v0-tool-loop` | 只包含前四课的早期答案 |
| `solution/kaiji-v0-full-course` | 12 课完整答案与真实 Azusa 集成 |

Azusa：

| 分支 | 用途 |
|---|---|
| `main` | 稳定主线和对接说明 |
| `todo/kaiji-semantic-gateway` | 人完成语义网关 TODO |
| `solution/kaiji-semantic-gateway` | 可运行的语义网关答案 |

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
$py = 'D:\Chuo_Data\conda_envs\ChuoEnv\python.exe'

& $py -m pytest tests/test_scaffold.py
& $py -m kaiji.demo
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
& $py -m pytest tests/test_registry.py
```

完成后应能解释：为什么未知工具由 Executor 转成结构化失败，而不是让 Registry 抛出
运行时异常？

#### TODO 2：调用前校验

文件：`src/kaiji/tools.py`

完成：

- 查找工具；
- 在 handler 运行前校验参数；
- 未知工具与参数错误的 `attempts` 都是 0；
- 参数错误不可重试。

```powershell
& $py -m pytest tests/test_executor.py -k 'unknown or arguments'
```

完成后应能解释：为什么“没有真正调用 handler”不能算一次 attempt？

#### TODO 3：执行、结果校验与有界重试

文件：`src/kaiji/tools.py`

完成：

- 超时；
- Pydantic 结果校验；
- 可重试与不可重试错误；
- 最多一次重试；
- 非法结果永远不能伪装成成功。

```powershell
& $py -m pytest tests/test_executor.py
```

完成后应能画出：第一次失败 → 是否可重试 → 第二次尝试 → 成功或停止。

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
& $py -m pytest tests/test_runtime.py
& $py -m kaiji.demo
```

第一站验收：

```powershell
& $py -m pytest tests/test_registry.py tests/test_executor.py tests/test_runtime.py
```

此时人应该能脱离代码解释完整 Tool Agent Loop。

### 第二站：理解框架究竟替你做了什么

#### TODO 5：LangChain 对照

文件：`src/kaiji/langchain_compare.py`

完成：

- Kaiji Message 与 LangChain Message 的映射；
- `StructuredTool`；
- `RunnableLambda` 组合；
- 结构化输出校验；
- Callback 记录。

```powershell
& $py -m pytest tests/test_langchain_compare.py
```

重点不是记 API，而是回答：原生版本中哪些逻辑被框架包装了？哪些可靠性责任仍然属于
Kaiji？

#### TODO 6：LangGraph 状态工作流

文件：`src/kaiji/langgraph_workflow.py`

实现状态图：

```text
问题 → Router → Search → Assess
                    ├─ 有证据 → Answer
                    ├─ 可恢复 → Rewrite → Search（最多一次）
                    ├─ 达到上限 → Max Steps
                    └─ 不可恢复 → Failure

副作用请求 → Interrupt → 人工批准 / 拒绝 → Resume
```

完成 AgentState、Node、Edge、Conditional Edge、Checkpoint、Interrupt 和
`Command(resume=...)`。

```powershell
& $py -m pytest tests/test_langgraph_workflow.py
```

完成后应能解释：为什么 retry_count 必须存在于显式 State，而不能藏在节点局部变量里？

### 第三站：跨边界、恢复和记忆

#### TODO 7：Azusa Adapter

文件：

- `src/kaiji/adapters/azusa.py`
- `src/kaiji/azusa_tools.py`

完成：

- Mock Adapter；
- 语义 HTTP Adapter；
- 当前 Rust `/search` 的兼容 Adapter；
- 4xx/5xx/timeout 的重试边界；
- AdapterError 到 ToolExecutionError 的转换；
- `search_azusa` 与 `get_chunk_by_id` 工具注册。

```powershell
& $py -m pytest tests/test_azusa_adapter.py
```

这里先使用 MockTransport，不需要启动 Azusa。重点是让网络、检索和 Runtime 保持清晰
边界。

#### TODO 8：Checkpoint

文件：`src/kaiji/persistence.py`

Checkpoint 是一次执行的完整恢复快照：

```powershell
& $py -m pytest tests/test_persistence.py -k checkpoint
```

#### TODO 9：Memory

文件：`src/kaiji/persistence.py`

Memory 是应用选择长期保留的少量事实，不是整段运行状态：

```powershell
& $py -m pytest tests/test_persistence.py -k memory
```

完成后应能用自己的话区分：

- Message History；
- AgentState；
- Checkpoint；
- Memory。

### 第四站：证明系统可靠，而不只是“看起来能跑”

#### TODO 10：Trace 保存与重放

文件：`src/kaiji/tracing.py`

完成 JSONL append、load、迭代和人类可读 replay：

```powershell
& $py -m pytest tests/test_tracing.py
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

```powershell
& $py -m pytest tests/test_evaluation.py
& $py -m kaiji.eval_demo
```

注意分母：没有要求工具调用的 case，不应进入工具选择准确率的分母。

#### TODO 12：真实 Azusa HTTP 联调

这一课跨越两个仓库。

先在 Azusa 完成练习：

```powershell
Set-Location F:\Repositories\Azusa
git switch todo/kaiji-semantic-gateway
Set-Location rag

& $py -m pytest tests/test_kaiji_gateway.py
```

测试全绿后启动语义网关：

```powershell
& $py run_kaiji_gateway.py --port 3010
```

在另一个终端回到 Kaiji：

```powershell
Set-Location F:\Repositories\Kaiji
$env:AZUSA_GATEWAY_URL = 'http://127.0.0.1:3010'

& $py -m pytest tests/test_azusa_live_integration.py
```

这条测试会使用 Azusa 现有 `embedding.jsonl`：

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
& $py -m pytest
& $py -m kaiji.course_demo
& $py -m kaiji.eval_demo
```

Azusa：

```powershell
Set-Location F:\Repositories\Azusa\rag
& $py -m pytest tests/test_kaiji_gateway.py
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
- Azusa 网关作业：`F:\Repositories\Azusa\rag\docs\kaiji_gateway_assignment.md`
- Azusa 主线对接说明：`F:\Repositories\Azusa\docs\kaiji_integration.md`

学习时以本文件为主线；需要复制命令时看 curriculum，需要理解边界时看 architecture，
需要比较框架时看 framework-comparison。
