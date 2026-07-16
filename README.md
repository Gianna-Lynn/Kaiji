# Kaiji

Kaiji 是 Azusa 之上的独立 Agent 应用工程项目。它不重新实现 HNSW 或 RAG，而是通过工具适配器调用 Azusa，学习并实现可靠的单 Agent Runtime。

## 项目关系

```text
Kaiji Agent
  ├─ Tool Calling / State / Workflow / Checkpoint / Eval
  └─ search_azusa / get_chunk_by_id
       └─ Azusa HNSW + RAG
```

Azusa 提供检索与证据能力；Kaiji 负责工具选择、状态推进、有限重试、证据判断、回答和可观测性。Kaiji 不导入 Azusa 内部实现，优先通过 HTTP 或 tool adapter 集成。

## 2026-07-16—2026-08-31 目标

核心能力冻结线：**2026-08-23**。8 月 24—26 日为可移动的开题准备保护块；8 月 27—31 日用于收口、部署和缓冲。

### 阶段计划

- **07-28—08-02：原生 Agent v0**
  - 消息、Structured Output、Pydantic/JSON Schema
  - 三个安全工具、Tool Registry、Executor
  - 结果回填、最大步骤、超时、有界重试、停止原因、Trace
- **08-03—08-06：LangChain 对照**
  - 用 LangChain 重建同一组工具与 case
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

## 非目标

在本阶段不做多 Agent、复杂长期记忆、任意代码执行、浏览器自动化、MCP 大型生态、Kubernetes、分布式 Runtime 或 vLLM 内核工作。

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
