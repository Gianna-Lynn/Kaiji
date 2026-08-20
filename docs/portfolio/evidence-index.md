# Kaiji 求职主张证据索引

本索引用于约束 README、简历和面试表述。每条主张必须能落到实现、契约、测试或历史报告；
只有代码存在但作者尚未亲手掌握的能力，不应写成个人已独立实现。

| 可公开主张 | 实现 | 直接测试/契约 | 历史结果 |
|---|---|---|---|
| 类型化 Tool Calling | `models.py`、`tools.py` | `test_registry.py`、`test_executor.py` | 冻结测试记录 |
| 未知/额外参数拒绝 | `ToolExecutor.execute()` | Executor 参数对抗测试 | Contract Suite |
| 工具输出二次校验 | `ToolExecutor.execute()` | 非法结果、NaN/Infinity 测试 | Contract Suite |
| 有界 retry | `ToolDefinition.max_retries`、Executor attempt loop | timeout/retry 测试 | Reliability Ablation |
| 重复业务调用保护 | `AgentRuntime.seen_calls` | 不同 call ID、相同 name+arguments 测试 | Reliability Ablation |
| 明确停止原因 | `StopReason`、`AgentRuntime.run()` | Runtime 各退出路径测试 | stop distribution |
| 失败证据隔离 | `RunResult.successful_tool_results` | 消息/Trace/证据一致性测试 | Citation 指标与 Bad Case |
| LangGraph HITL | `build_agent_graph()`、`resume_graph()` | Interrupt、严格 bool、线程隔离测试 | SQLite Resume |
| Checkpoint/Memory 分离 | `persistence.py` | 损坏、身份、原子写与隔离测试 | 冻结审计 |
| Azusa 稳定 Adapter | `adapters/azusa.py`、`azusa_tools.py` | MockTransport 与 Live Integration | 3 Search + 3 Get、5 条模型 Live |
| OpenAI-compatible Provider | `providers/openai_compatible.py` | 请求、历史配对、响应、错误分类测试 | DeepSeek Smoke/Formal |
| Behavior/Fault Suite 分离 | `eval/*.jsonl`、`experiment.py` | Runner/Scorer/Experiment 测试 | Pilot 方法学修正 |
| 确定性 Scorer | `eval_scorer.py` | 适用分母、引用与字面事实测试 | Formal 离线重算 |
| 可恢复 Formal | `formal_runner.py` | Manifest、账本、hash、resume 对抗测试 | 120/120 完整 Formal |
| Token/费用边界 | `ModelUsage`、`PersistentRequestLedger` | 请求预算与账本不变量测试 | 225 请求、历史费用估算 |
| HTTP 服务与 fail-closed 鉴权 | `service.py` | `test_service.py` 直接契约 | Uvicorn live 200；缺配置 503 |
| 服务端工具权限收缩 | `ServiceSettings.allowed_tools`、每请求 Registry | allow/deny 与新 Runtime 测试 | P2 窄测试 10/10 |
| 脱敏 span 投影 | `observability.py` | 层级、失败状态与敏感字段负向测试 | 离线回归通过；未接真实 Collector |
| 容器与 clean-clone CI | `Dockerfile`、`.github/workflows/ci.yml` | 首次远端 CI 待验证 | 本地镜像构建、非 root 与健康检查通过 |

## 文件入口

- 核心模型：[`src/kaiji/models.py`](../../src/kaiji/models.py)
- 工具执行：[`src/kaiji/tools.py`](../../src/kaiji/tools.py)
- 原生循环：[`src/kaiji/runtime.py`](../../src/kaiji/runtime.py)
- LangGraph：[`src/kaiji/langgraph_workflow.py`](../../src/kaiji/langgraph_workflow.py)
- 持久化：[`src/kaiji/persistence.py`](../../src/kaiji/persistence.py)
- Trace：[`src/kaiji/tracing.py`](../../src/kaiji/tracing.py)
- Provider：[`src/kaiji/providers/openai_compatible.py`](../../src/kaiji/providers/openai_compatible.py)
- Runner/Scorer：[`src/kaiji/eval_runner.py`](../../src/kaiji/eval_runner.py)、[`src/kaiji/eval_scorer.py`](../../src/kaiji/eval_scorer.py)
- Experiment/Formal：[`src/kaiji/experiment.py`](../../src/kaiji/experiment.py)、[`src/kaiji/formal_runner.py`](../../src/kaiji/formal_runner.py)
- 服务：[`src/kaiji/service.py`](../../src/kaiji/service.py)
- span 投影：[`src/kaiji/observability.py`](../../src/kaiji/observability.py)

## 契约与历史报告

- [`docs/reliability-eval-contract.md`](../reliability-eval-contract.md)
- [`docs/reliability-eval-traceability.md`](../reliability-eval-traceability.md)
- [`docs/reliability-eval-v0.2-report.md`](../reliability-eval-v0.2-report.md)
- [`docs/freeze-audit-v0.2.md`](../freeze-audit-v0.2.md)
- [`docs/portfolio/p2-validation.md`](p2-validation.md)

## 原始 artifacts 的发布边界

本机求职 worktree 保留了历史 `artifacts/`，用于核对报告、Episode、Manifest 和请求账本。
该目录被 `.gitignore` 排除，当前没有提交或推送：

- 原始模型回答可能包含不适合直接公开的实验文本；
- 请求账本和 Manifest 需要在发布前再次做敏感信息与体积审查；
- 高层结果已经由受 Git 跟踪的冻结报告提供；
- 若将来公开，只选择可复现、必要、去敏且有明确许可的数据子集。

## 简历使用门槛

| 状态 | 可以怎样写 |
|---|---|
| `main` 上本人已完成并能解释 | 可写“实现/设计”，并准备源码追问 |
| 只存在于答案/portfolio，尚未亲手完成 | 只能写“项目包含/基线提供”，不能声称独立实现 |
| 已亲手复现、修改并验证答案能力 | 可升级为“实现/完善”，同时说明自己的具体改动 |
| 只有文档设想或 P2 计划 | 不写成已经完成 |
