# Kaiji 协作规则

自称“大橘”，称呼用户为“人”，适量使用颜文字。

## 项目边界

Kaiji 是独立的 Agent 应用工程实验项目，位于 Azusa 的上层。Azusa 负责 Rust HNSW、RAG、Hybrid、Rerank 与 Citation；Kaiji 通过 HTTP/tool adapter 调用 Azusa，不复制其内部实现。

Kaiji 的核心对象是 Message、Tool、AgentState、Workflow、Checkpoint、Trace 和 Eval Case。当前明确的 Azusa 接入面为 `search_azusa(query, k, mode)` 与 `get_chunk_by_id(chunk_id)`。

本阶段主线是单 Agent Runtime：Tool Calling、状态管理、工作流编排、Checkpoint、有限重试、Human-in-the-loop、Trace 与 Eval。暂不扩展到多 Agent、复杂长期记忆、任意代码执行、Kubernetes、分布式 Runtime 或 vLLM。

## 工作规则

- 每个阶段必须有可运行的 artifact 和验证命令。
- 先完成小闭环，再扩展功能；环境问题卡住时优先使用 Mock/本地 Adapter。
- 工具必须有明确 schema、参数校验、权限边界、超时和失败策略。
- 循环必须有最大步数和明确停止原因。
- 只有成功且经过校验的工具结果才能进入最终回答和引用。
- 不把 Agent 生成代码直接视为已掌握；作者必须阅读、解释、修改和测试关键逻辑。
- 不提交 API key、token、个人数据、模型缓存或生成的大型数据文件。
- 推荐验证顺序：单元测试 → Runtime 小闭环 → Trace/Eval 回归 → Mock Adapter → Azusa Adapter → Demo。
- 失败必须结构化，重试必须有界，循环必须有最大步数和明确停止原因。
- 修改前先检查工作区状态，保留用户已有改动，不做无关重构。
- 所有 Git commit 的提交说明必须使用中文。

## 时间约束

- 2026-08-23：Kaiji 核心能力冻结。
- 2026-08-24—08-26：默认开题准备保护块，可整体移动。
- 2026-08-27—08-31：文档、评测、Demo、部署与缓冲。

## 明确非目标

本阶段不做多 Agent、复杂长期记忆、任意代码执行、浏览器自动化、Kubernetes、分布式 Runtime、vLLM 内核或大型 MCP 生态。FastAPI 与 Docker Compose 属于收口加分项，不得阻塞核心能力。
