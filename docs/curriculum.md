# Kaiji 完整教学路线

标准答案分支：`solution/kaiji-v0`。

## 0. 环境和总验收

```powershell
$py = 'D:\Chuo_Data\conda_envs\ChuoEnv\python.exe'
& $py -m pytest
& $py -m kaiji.course_demo
& $py -m kaiji.eval_demo
```

未设置 `AZUSA_GATEWAY_URL` 时，前述离线验收不需要 API key、GPU 或正在运行的 Azusa；
live test 会明确跳过。若环境变量仍然存在，则全量测试会真实连接对应网关。

本页是课程路线和运行命令的索引，不代替教学契约。每个 TODO 的字段含义、状态流转、
失败边界、非目标和测试矩阵都写在 `LEARNING.md`；实现前必须先读对应章节。仅凭测试
通过不能证明课程完成，还要逐项核对文档中的正向、失败、边界和不变量。

真实 Azusa HTTP 回归默认从离线全套中跳过，因此不阻塞日常练习；但它是 TODO 12 的
必做最终验收。验证参考答案时，先显式切到 Azusa 正确答案分支并启动网关。本机 Windows
排除了 `2991..3090` 端口段，因此课程命令使用已实测的 `38117`：

```powershell
Set-Location F:\Repositories\Azusa
git switch solution/kaiji-semantic-gateway
Set-Location rag
& $py run_kaiji_gateway.py --port 38117 --embedding-path tests/fixtures/kaiji_gateway_embedding.jsonl
```

人完成 Azusa 练习时，把上面的分支换成 `todo/kaiji-semantic-gateway`；只有该练习分支
测试通过并实际完成联调，才算人完成 TODO 12。`solution` 只用于先验证标准答案闭环。

再找到或创建 Kaiji 正确答案的独立 worktree；不要为此覆盖带有人改动的 `main`：

```powershell
Set-Location F:\Repositories\Kaiji
git worktree list
# 若已列出 solution/kaiji-v0，进入它上方显示的 worktree 路径。
# 若尚未列出，执行下面两行创建并进入示例路径：
git worktree add F:\Repositories\Kaiji-solution solution/kaiji-v0
Set-Location F:\Repositories\Kaiji-solution
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& $py -c "import kaiji; print(kaiji.__file__)"
$env:AZUSA_GATEWAY_URL = 'http://127.0.0.1:38117'
& $py -m pytest -q tests/test_azusa_live_integration.py
```

同一个 Conda 环境的 editable install 只能指向一个工作树。pytest 会按本项目配置优先加载
当前 `src`，但 `python -m kaiji.course_demo` 等普通模块命令可能仍导入此前安装的 main。
因此在答案 worktree 验收期间临时设置上面的 `PYTHONPATH`，并用打印出的 `kaiji.__file__`
确认路径确实位于该 worktree；结束后执行 `Remove-Item Env:PYTHONPATH`。这不是代码依赖，
而是多个工作树共用一个 Python 环境时的来源隔离。

该回归使用 Azusa 教学分支受 Git 跟踪的最小 Embedding fixture 和真实 BM25 检索，随后按
返回的 chunk id 再取完整证据，验证两次响应正文一致。它不依赖本机被忽略的
`output/artifacts/embedding.jsonl`，新 clone 也能复现。TODO 12 的验收输出必须是
`1 passed`；如果显示 `skipped`，只说明离线课程没有被阻塞，不表示跨仓库对接已经完成。

## 1. 原生 Tool Agent

代码：`models.py`、`tools.py`、`runtime.py`、`demo.py`。

掌握 Message、ToolCall、Schema、Registry、Executor、输入/输出校验、超时、一次重试、
结果回填、重复调用检测、最大步骤、停止原因和 Trace。

## 2. LangChain 对照

代码：`langchain_compare.py`。

用同一个 `AzusaAdapter` 对照 Message、StructuredTool、Runnable、结构化输出和
Callback。`Message` 到 LangChain 消息是有意设计的有损映射，不负责还原 LangChain
原生 `tool_calls`；框架封装也不等于参数校验、结果校验或可靠性策略。

## 3. LangGraph 状态工作流

代码：`langgraph_workflow.py`。

掌握显式 AgentState、Node、Edge、Conditional Edge、最多一次 query rewrite、
Checkpoint、Interrupt、`Command(resume=...)` 和所有终止路径。这里的 `step_count`
统计搜索执行次数，`retry_count` 统计 query rewrite 次数；二者不能混用。

## 4. State、History、Checkpoint、Memory

代码：`persistence.py`。

分别保存运行工作状态、消息历史、完整恢复快照和应用选择的跨会话事实。禁止把四者
混成一个无限增长的 JSON。

## 5. Azusa Adapter

代码：`adapters/azusa.py`、`azusa_tools.py`。

先用 Mock 跑通，再用 MockTransport 验证 HTTP 契约；最后才启动真实 Azusa。
当前 Rust 服务与任务书语义接口的差距记录在 `architecture.md`。

## 6. Trace 与 Eval

代码：`tracing.py`、`evaluation.py`、`eval/cases.jsonl`。

20 条 `EvalCase` 规范覆盖工具选择、参数错误、超时、重试、空结果、重复调用、最大步骤、人工
拒绝、Checkpoint、Memory、Azusa 与 Citation。指标包括任务成功率、工具选择/参数
准确率、工具成功率、重试恢复率、不必要调用率、步骤数、P50/P95、停止原因和引用支持率。
`EvalCase` 是预期契约，`EvalObservation` 是实测事实，`EvalReport` 只能由二者逐条对齐后
计算；不得用全为满分的样例掩盖错误公式。当前 `reference_observation()` 是聚合器金标，
不是 20 条真实 Agent 行为回归；真实 runner 必须另行提供观测。

## 7. TODO 与验收入口

每完成一行，该行测试应能独立转绿；最后再执行全量测试与两个 Demo。

| TODO | 主题 | 完成后验证 |
|---|---|---|
| 1 | Tool Registry | `pytest tests/test_registry.py` |
| 2 | 工具解析与参数校验 | `pytest tests/test_executor.py -k "unknown or arguments"` |
| 3 | 结果校验、超时与一次重试 | `pytest tests/test_executor.py` |
| 4 | Agent Loop、Message/Trace 与停止原因 | `pytest tests/test_runtime.py` |
| 5 | LangChain 消息、Tool、Runnable、Callback | `pytest tests/test_langchain_compare.py` |
| 6 | LangGraph State、路由、恢复、Interrupt | `pytest tests/test_langgraph_workflow.py` |
| 7 | Azusa HTTP 契约、错误分类、有限重试 | `pytest tests/test_azusa_adapter.py` |
| 8 | 文件 Checkpoint、覆盖保存与 latest | `pytest tests/test_persistence.py -k checkpoint` |
| 9 | Memory 隔离、覆盖与查询 | `pytest tests/test_persistence.py -k memory` |
| 10 | JSONL Trace、唯一 run_id 与稳定 replay | `pytest tests/test_tracing.py` |
| 11 | Eval 对齐、混合样例与指标公式 | `pytest tests/test_evaluation.py` |
| 12 | 真实 Azusa 跨仓库闭环 | `pytest -q -rs tests/test_azusa_live_integration.py` |
