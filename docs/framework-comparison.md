# 原生 Runtime、LangChain、LangGraph 对照

| 关注点 | 原生 Kaiji | LangChain | LangGraph |
|---|---|---|---|
| Message | `Message` Pydantic 模型 | `HumanMessage` / `AIMessage` / `ToolMessage` | 通常作为 State 字段 |
| Tool schema | `ToolDefinition` + Pydantic | `StructuredTool` | 可在 Node 内调用 Tool |
| 执行策略 | `ToolExecutor` 显式实现 | Tool/Runnable 封装调用 | Node 与 Edge 编排 |
| 参数/结果校验 | 输入输出均显式校验 | args schema 显式，输出需自行约束 | 取决于 Node 实现 |
| 重试与超时 | 代码中清晰可见 | 可通过 Runnable 策略包装 | 可用条件边表达恢复路径 |
| 循环上限 | `max_steps` | 调用方负责 | State 中的 `max_steps` 条件边 |
| Trace | 自定义 `TraceEvent` | Callback / LangSmith 接口 | State history / stream |
| Checkpoint | 自定义 JSON Store | 不属于核心 Runnable | 编译图时注入 checkpointer |
| Interrupt | 手写暂停协议 | 不属于核心 Tool | `interrupt` + `Command(resume=...)` |
| 隐藏复杂度 | 最少，代码量最多 | 中等 | 状态合并与恢复语义较复杂 |

学习顺序固定为原生 → LangChain → LangGraph。框架版本只改变表达方式，工具权限、
校验、错误边界、重试上限和成功证据规则仍由 Kaiji 负责。

横向审查的责任结论：LangChain 的 `StructuredTool` 不会替 Kaiji 校验返回值、证据归属
或 `tool_call_id` 生命周期；LangGraph 的 State/Checkpointer 不会自动保证状态版本、
严格审批类型、线程身份、停止原因与证据一致。原生 Runtime 显式承担这些边界，框架路径
必须在 Adapter、Node 和恢复入口分别补回，不能把“框架能运行”当成可靠性契约。

## 本课程的对照边界

- `to_langchain_messages()` 只比较角色、正文和工具结果的对应关系，是有损映射；Kaiji
  当前 `Message` 没有完整表达 LangChain 原生 `AIMessage.tool_calls`，因此不承诺往返还原。
- LangChain 的 `StructuredTool` 负责输入 schema，但 Adapter 返回值仍须由 Kaiji 的
  Pydantic 输出模型校验；Callback 只观察开始、成功和失败，不替代错误处理。
- LangGraph 的 `AgentState` 是一次运行的显式工作数据：`step_count` 是搜索执行次数，
  `retry_count` 是 query rewrite 次数，`max_steps` 限制前者。
- LangGraph Checkpoint 保存线程状态以支持暂停和恢复；它不自动变成 Kaiji 的长期
  Memory，也不能替代应用自行选择和隔离跨会话事实。TODO 6 的 `MemorySaver` 与
  TODO 8 的 `JsonCheckpointStore` 是两个独立教学实现，本课程没有把后者接成前者后端。
