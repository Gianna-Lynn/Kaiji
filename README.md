# Kaiji

Kaiji 是一个面向可靠工具调用的 Python 单 Agent Runtime 实验项目，围绕参数校验、有界重试、状态恢复、Trace 和评测组织执行流程。
它通过 HTTP/Tool Adapter 调用 [Azusa](https://github.com/Gianna-Lynn/Azusa) 的检索与证据接口。

**项目展示与运行指南：[`portfolio/kaiji-v0`](https://github.com/Gianna-Lynn/Kaiji/tree/portfolio/kaiji-v0)**
——集中查看架构、Demo、评测与失败分析。当前默认分支 `main` 保留作者的课程进度；展示分支的能力与历史结果需按对应版本阅读。

## 项目重点

- **工具执行边界**：Pydantic/JSON Schema 契约、工具输入与输出校验、超时、有界重试、重复调用保护和明确停止原因。
- **状态与恢复**：区分 Message、AgentState、Checkpoint 和 Memory，并对照 LangChain/LangGraph 的消息、工具与状态工作流。
- **证据与评测**：成功工具结果、回答引用和 Trace 分别保留；通过受控故障与固定样例观察行为、恢复和评分边界。

## 项目关系

```text
Question -> AgentRuntime -> ToolRegistry / ToolExecutor
                |                    |
                |             Azusa HTTP Adapter
                |                    |
         State / Trace / Eval   search -> chunk evidence
```

Azusa 负责 HNSW 与 RAG；Kaiji 通过 `search_azusa(query, k, mode)` 和 `get_chunk_by_id(chunk_id)` 使用检索与证据能力，
不导入 Azusa 的内部实现。[架构说明](docs/architecture.md)与[框架职责对照](docs/framework-comparison.md)记录接口和取舍。

## 最小体验

需要 Python 3.11+。在当前 `main` 的仓库根目录安装并执行：

```powershell
python -m pip install -e '.[dev]'
python -m kaiji.demo
```

该演示使用预设决策替身调用折扣工具，输出 JSON；应看到 `answer` 为 `The final price is 90.`、
`stop_reason` 为 `completed`，并保留工具调用与 Trace。无需 API Key、GPU 或运行中的 Azusa。
它只展示原生 Runtime 小闭环，不代表真实模型的自主工具选择成绩。

完整展示分支的安装、演示和真实调用边界见
[Demo 指南](https://github.com/Gianna-Lynn/Kaiji/blob/portfolio/kaiji-v0/docs/portfolio/demo-guide.md)。
该指南中的命令应在其对应分支运行。

## 深入阅读

- [展示分支架构与状态图](https://github.com/Gianna-Lynn/Kaiji/blob/portfolio/kaiji-v0/docs/portfolio/architecture.md)。
- [评测与失败案例](https://github.com/Gianna-Lynn/Kaiji/blob/portfolio/kaiji-v0/docs/portfolio/evaluation-and-bad-cases.md)：区分协议测试、受控故障实验、真实模型行为和 Azusa Live。
- [主张证据索引](https://github.com/Gianna-Lynn/Kaiji/blob/portfolio/kaiji-v0/docs/portfolio/evidence-index.md)：对应代码、测试与历史验证范围。
- [当前课程契约](LEARNING.md)、[课程路线](docs/curriculum.md)、[Reliability Eval 任务](docs/reliability-course-l1-l3.md)。

## 分支与结论边界

| 分支 | 用途 |
|---|---|
| `main` | 作者逐步完成课程 TODO 的学习进度；不以参考分支结果代替当前分支验收 |
| [`solution/kaiji-v0`](https://github.com/Gianna-Lynn/Kaiji/tree/solution/kaiji-v0) | 完整参考实现与历史审计基线 |
| [`portfolio/kaiji-v0`](https://github.com/Gianna-Lynn/Kaiji/tree/portfolio/kaiji-v0) | 求职展示、证据组织及服务化演示 |

历史测试与模型成绩仅适用于报告中的提交、数据和环境。`eval_demo` 使用合成金标观测演示聚合器，不能作为 Agent 成绩。
课程完成情况以 `main` 的实现与验收为准；展示分支的服务化能力见对应文档。受控实验结果不作为生产 SLA 或开放域效果保证。

Kaiji（かいじ，甲斐路号）的名字来自 JR 东日本中央本线特急列车，与 Azusa（あずさ，梓号）呼应。
首页整理前的课程背景与历史排期保留在[学习计划归档](docs/learning-plan-archive.md)。
