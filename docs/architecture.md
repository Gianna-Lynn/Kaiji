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
  └─ 20 EvalCase 规范 + runner 观测 → metrics report
```

## 边界

Kaiji 不导入 `F:\Repositories\Azusa` 的 Python 或 Rust 内部实现。当前 Azusa Rust 服务
只提供向量级 `POST /search` 和 `GET /record/{id}`；因此兼容 adapter 需要注入 query
encoder 和 chunk text resolver，并且只支持 `hnsw`。

任务书约定的 `search_azusa(query, k, mode)` 与
`get_chunk_by_id(chunk_id)` 被定义为稳定的 `AzusaAdapter` 协议。Azusa 的薄 HTTP
网关进入稳定主线时，Kaiji 只替换 adapter，不修改 Runtime 或 Workflow。

Azusa 的稳定双分支都保留该薄网关：`solution/azusa-v2` 是完整参考答案，
`main` 是人的课程与 TODO 主线。两者共享同一 HTTP 契约，Kaiji 不依赖 Azusa 的内部实现。
HTTP 契约不依赖固定端口。本机 Windows 排除了 `2991..3090`，因此课程使用已验证的
`http://127.0.0.1:38117`，而不是脚本原默认值 `3010`。Kaiji 的
`test_azusa_live_integration.py` 使用真实 BM25
完成 `query → hits → chunk_id → 完整 chunk` 跨仓库回归；未设置
`AZUSA_GATEWAY_URL` 时该测试跳过，不阻塞离线课程，但跳过不代表 TODO 12 已验收。
验证参考答案时启动 `solution` 网关；人完成课程时必须让自己的 `todo` 网关通过测试，
并确认 live test 实际显示 `1 passed`。

## 状态与记录的职责边界

| 对象 | 回答的问题 | 生命周期 |
|---|---|---|
| `Message` history | 模型看到了哪些对话与工具结果 | 一次对话，可裁剪 |
| `AgentState` | 当前工作流走到哪里、下一步往哪里 | 一次运行或一次线程恢复 |
| `Checkpoint` | 如何完整恢复某个中断点 | 由 checkpoint id/thread id 标识 |
| `Memory` | 应用明确选择保留哪些跨会话事实 | 跨运行，按 user 隔离 |
| `TraceEvent.data` | 某个事件发生时的结构化观测详情 | 追加写、用于审计 |
| `Message.content` | 发送给模型或用户阅读的正文 | 随消息历史流转 |

字段字面相近并不表示可以互换。尤其不能把 Trace 当成模型上下文，也不能把整个
AgentState 或 Message history 无筛选地保存为长期 Memory。各 TODO 的精确字段契约和
失败语义见 `LEARNING.md`。
