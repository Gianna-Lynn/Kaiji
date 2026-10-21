# Kaiji 首页课程计划归档

本页保留首页整理前的学习背景、历史排期和分级教学说明。排期不代表当前承诺；当前课程契约与进度以
[`LEARNING.md`](../LEARNING.md) 和[课程路线](curriculum.md)为准，项目入口见[仓库首页](../README.md)。

Kaiji (かいじ) 是 JR 东日本中央本线(特急)甲斐路号列车的英文名. 甲斐路号列车往返于新宿和甲府. 同属于中央本线(特急)的 Azusa (あずさ) 梓号列车往返于新宿和松本.

Kaiji 是 Azusa 之上的独立 Agent 应用工程项目。它不重新实现 HNSW 或 RAG，而是通过工具适配器调用 Azusa，学习并实现可靠的单 Agent Runtime。

Kaiji 的核心对象是 Message、Tool、AgentState、Workflow、Checkpoint、Trace 和 Eval Case。当前课程主线已接到 TODO 15：`main` 保留练习代码，`solution/kaiji-v0` 保存完整参考实现与 Reliability Eval 答案边界，验证记录见 `docs/`。

## 项目关系

```text
Kaiji Agent
  ├─ Tool Calling / State / Workflow / Checkpoint / Eval
  └─ search_azusa / get_chunk_by_id
       └─ Azusa HNSW + RAG
```

当前明确的 Azusa 接入面为 `search_azusa(query, k, mode)` 与 `get_chunk_by_id(chunk_id)`。Kaiji 不导入 Azusa 内部实现。

Azusa 提供检索与证据能力；Kaiji 负责工具选择、状态推进、有限重试、证据判断、回答和可观测性。Kaiji 不导入 Azusa 内部实现，优先通过 HTTP 或 tool adapter 集成。

## 2026-07-16—2026-08-31 目标

核心能力冻结线：**2026-08-23**。8 月 24—26 日为可移动的开题准备保护块；8 月 27—31 日用于收口、部署和缓冲。

### 阶段计划

- **07-28—08-02：原生 Agent v0**
  - 消息、Structured Output、Pydantic/JSON Schema
  - 三个安全工具、Tool Registry、Executor
  - 结果回填、最大步骤、超时、有界重试、停止原因、Trace
- **08-03—08-06：LangChain 对照**
  - 用 `search_azusa` 对照 Message、StructuredTool、Runnable 与 Callback
  - 记录原生实现和框架抽象的差异
- **08-07—08-13：LangGraph 状态工作流**
  - AgentState、Node、Edge、Conditional Edge
  - Checkpoint、Interrupt、恢复执行
  - 证据不足时最多改写并重试一次
- **08-14—08-18：可靠性、Memory、Azusa Adapter**
  - 区分 State、History、Checkpoint、Memory
  - 一个最小持久化 Memory Store
  - `search_azusa(query, k, mode)` 与 `get_chunk_by_id(chunk_id)`
  - 副作用操作的人工确认节点
- **08-19—08-23：Eval 与可观测性**
  - 20—30 个固定 case
  - Task Success、Tool Selection/Argument Accuracy、Retry Recovery、延迟、停止原因、Citation Supported
- **08-27—08-31：收口**
  - README、架构图、状态图、对照报告、Bad Case、面试问答
  - 有余力再做 FastAPI 与 Docker Compose

## 第二批课程：Reliability Eval v0.2

Kaiji 的第二批课程采用逐步撤除脚手架的三级训练制。这里的 L1—L3
表示学习任务提供多少工程脚手架，不是功能版本、能力高低评级，也与 SAE 自动驾驶
分级无关。

| 等级 | 大橘提供 | 人负责 | 主要训练能力 |
|---|---|---|---|
| L1：接口实现 | 文件位置、函数签名、类型契约和较完整单元测试 | 实现函数内部逻辑与失败分支 | 类型、控制流、算法和错误处理 |
| L2：模块实现 | 模块职责、公开契约和验收测试 | 设计类、函数、状态及模块内部结构 | API 设计、职责划分和状态建模 |
| L3：需求实现 | 自然语言需求、约束、黑盒验收与 benchmark | 决定功能位置、架构方案、实现和验证闭环 | 需求拆解、架构权衡和完整工程交付 |

使用原则：

- 第一次学习一种机制时优先采用 L1；掌握后，相近能力直接进入 L2；综合性新需求再进入 L3。
- 不要求同一个功能机械地重做 L1、L2、L3 三遍；等级应随人的掌握程度选择。
- 撤除的是代码脚手架，不是验收边界。L2、L3 仍必须给出明确行为、失败条件和验证命令。
- 人应先设计、实现、测试并记录失败，再按需查看分级提示，最后才对照 reference branch。
- L1 测试可以覆盖内部边界；L2 以公开契约测试为主；L3 以黑盒验收、Eval、benchmark 和 Demo 为主。

在 Kaiji 中，Tool Registry、有限重试和原生 Runtime 适合作为 L1；新的 Adapter、持久化
或可观测性模块适合作为 L2；同时要求 Trace、Eval、Demo 与文档闭环的完整新能力适合作为
L3。该模式用于未来拓展，不回头重构当前正在执行的 TODO 课程，也不得因此影响主线排期。

Reliability Eval v0.2 已把这套分级落成 TODO13—TODO15：L1 Provider、L2
Runner/Scorer/SQLite、L3 Harness 与可恢复实验。公开契约、非目标和逐子题窄命令见
[`docs/reliability-course-l1-l3.md`](reliability-course-l1-l3.md)。默认练习回归全部
离线运行，不会读取 DeepSeek Key 或发送模型请求。

## 非目标

在本阶段不做多 Agent、复杂长期记忆、任意代码执行、浏览器自动化、MCP 大型生态、Kubernetes、分布式 Runtime 或 vLLM 内核工作。

FastAPI、Docker Compose 与部署包装属于收口加分项，不得阻塞核心能力。

## 验收标准

- 可以不依赖框架解释并修改完整 Tool Agent Loop。
- 工具参数经过 Schema/Pydantic 校验。
- 可区分可重试与不可重试错误，并有最大步骤和停止条件。
- LangGraph 能表达条件边、循环、Checkpoint 和 Interrupt。
- 能保存并重放 trace，完成一次状态恢复。
- Kaiji 能通过 adapter 调用 Azusa，并将证据用于最终回答。

## 开发原则

- 没有验证命令，不标记完成。
- 任何单个环境或框架问题不得阻塞主线；必要时使用 Mock/本地 Adapter。
- Agent 生成的代码必须由项目作者阅读、解释、修改和测试。

推荐验证顺序：单元测试 → Runtime 小闭环 → Trace/Eval 回归 → Mock Adapter → Azusa Adapter → Demo。
