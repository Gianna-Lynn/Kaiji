# Kaiji 完整教学路线

标准答案分支：`solution/kaiji-v0-full-course`。

## 0. 环境和总验收

```powershell
$py = 'D:\Chuo_Data\conda_envs\ChuoEnv\python.exe'
& $py -m pytest
& $py -m kaiji.course_demo
& $py -m kaiji.eval_demo
```

全部测试与三个命令均不需要 API key、GPU 或正在运行的 Azusa。

练习版的 TODO 顺序与窄验证命令：

| TODO | 主题 | 完成后验证 |
|---|---|---|
| 1 | Tool Registry | `pytest tests/test_registry.py` |
| 2 | 工具解析与参数校验 | `pytest tests/test_executor.py -k "unknown or arguments"` |
| 3 | 结果校验、超时与一次重试 | `pytest tests/test_executor.py` |
| 4 | Agent Loop、重复调用与停止原因 | `pytest tests/test_runtime.py` |
| 5 | LangChain Message/Tool/Runnable/Callback | `pytest tests/test_langchain_compare.py` |
| 6 | LangGraph State/Edge/Checkpoint/Interrupt | `pytest tests/test_langgraph_workflow.py` |
| 7 | Mock、语义 HTTP 与当前 Azusa 兼容 Adapter | `pytest tests/test_azusa_adapter.py` |
| 8 | Checkpoint Store | `pytest tests/test_persistence.py -k checkpoint` |
| 9 | Memory Store 与会话恢复 | `pytest tests/test_persistence.py -k memory` |
| 10 | Trace 保存与重放 | `pytest tests/test_tracing.py` |
| 11 | 20-case Eval 指标聚合 | `pytest tests/test_evaluation.py` |
| 12 | Azusa TODO 分支完成后的真实 HTTP 对接 | `pytest tests/test_azusa_live_integration.py` |

每完成一行，该行测试即可独立转绿。最后再执行全量 `pytest`、`course_demo` 和
`eval_demo`。如果卡住，可用下列命令只查看标准答案差异，避免整分支切换：

```powershell
git diff main..solution/kaiji-v0-full-course -- src/kaiji/<对应文件>.py
```

## TODO 12：跨仓库验收

先在 Azusa 的 `todo/kaiji-semantic-gateway` 完成
`rag/docs/kaiji_gateway_assignment.md`，使其 5 个网关测试全绿，然后启动：

```powershell
Set-Location F:\Repositories\Azusa\rag
& $py run_kaiji_gateway.py --port 3010
```

在 Kaiji main 的另一个终端运行：

```powershell
Set-Location F:\Repositories\Kaiji
$env:AZUSA_GATEWAY_URL = 'http://127.0.0.1:3010'
& $py -m pytest tests/test_azusa_live_integration.py
```

验收必须使用 Azusa 现有 `embedding.jsonl` 和 BM25：先搜索 stromules，再按命中的
chunk id 回读正文并确认一致。未设置环境变量时该测试自动跳过，不阻塞前 11 课。

## 1. 原生 Tool Agent

代码：`models.py`、`tools.py`、`runtime.py`、`demo.py`。

掌握 Message、ToolCall、Schema、Registry、Executor、输入/输出校验、超时、一次重试、
结果回填、重复调用检测、最大步骤、停止原因和 Trace。

## 2. LangChain 对照

代码：`langchain_compare.py`。

用同一个 `AzusaAdapter` 对照 Message、StructuredTool、Runnable、结构化输出和
Callback。不要把框架封装误认为可靠性策略。

## 3. LangGraph 状态工作流

代码：`langgraph_workflow.py`。

掌握显式 AgentState、Node、Edge、Conditional Edge、最多一次 query rewrite、
Checkpoint、Interrupt、`Command(resume=...)` 和所有终止路径。

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

20 条 case 覆盖工具选择、参数错误、超时、重试、空结果、重复调用、最大步骤、人工
拒绝、Checkpoint、Memory、Azusa 与 Citation。指标包括任务成功率、工具选择/参数
准确率、工具成功率、重试恢复率、不必要调用率、步骤数、P50/P95、停止原因和引用支持率。
