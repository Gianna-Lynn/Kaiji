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

真实 Azusa HTTP 回归是单独的可选测试。先在 Azusa 正确答案分支启动 3010 网关：

```powershell
Set-Location F:\Repositories\Azusa\rag
& $py run_kaiji_gateway.py --port 3010
```

再在 Kaiji 正确答案分支运行：

```powershell
Set-Location F:\Repositories\Kaiji
$env:AZUSA_GATEWAY_URL = 'http://127.0.0.1:3010'
& $py -m pytest tests/test_azusa_live_integration.py
```

该回归使用 Azusa 现有 `embedding.jsonl` 和真实 BM25 检索，随后按返回的 chunk id 再取
完整证据，验证两次响应正文一致。

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
