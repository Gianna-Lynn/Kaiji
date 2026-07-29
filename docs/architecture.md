# Kaiji v0.1 教学架构

```text
Question
  │
  ├─ Native AgentRuntime ─ ToolRegistry ─ ToolExecutor
  │                                  │
  │                                  ├─ schema validation
  │                                  ├─ timeout / bounded retry
  │                                  └─ structured ToolResult
  │
  ├─ LangChain comparison ─ Message / Tool / Runnable / Callback
  │
  └─ LangGraph workflow ─ AgentState ─ Checkpoint
                         ├─ search → answer
                         ├─ recoverable → rewrite once
                         ├─ side effect → interrupt → resume
                         └─ failure / max steps

AzusaAdapter
  ├─ MockAzusaAdapter                 # 默认离线教学
  ├─ AzusaHttpAdapter                 # 稳定语义网关契约
  └─ LegacyAzusaVectorHttpAdapter     # 当前 Rust /search 兼容层

Persistence
  ├─ Message history   当前对话输入
  ├─ AgentState        一次运行中的工作数据
  ├─ Checkpoint        可恢复的完整执行快照
  └─ Memory            应用选择保存的跨会话事实

Observability
  ├─ JSONL Trace save / load / replay
  └─ 20 Eval Cases → metrics report
```

## 边界

Kaiji 不导入 `F:\Repositories\Azusa` 的 Python 或 Rust 内部实现。当前 Azusa Rust 服务
只提供向量级 `POST /search` 和 `GET /record/{id}`；因此兼容 adapter 需要注入 query
encoder 和 chunk text resolver，并且只支持 `hnsw`。

任务书约定的 `search_azusa(query, k, mode)` 与
`get_chunk_by_id(chunk_id)` 被定义为稳定的 `AzusaAdapter` 协议。未来 Azusa 增加薄
HTTP 网关时，Kaiji 只替换 adapter，不修改 Runtime 或 Workflow。
