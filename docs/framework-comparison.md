# 原生 Runtime、LangChain、LangGraph 对照

| 关注点 | 原生 Kaiji | LangChain | LangGraph |
|---|---|---|---|
| Message | `Message` Pydantic 模型 | `HumanMessage` / `AIMessage` / `ToolMessage` | 通常作为 State 字段 |
| Tool schema | `ToolDefinition` + Pydantic | `StructuredTool` | 可在 Node 内调用 Tool |
| 执行策略 | `ToolExecutor` 显式实现 | Tool/Runnable 封装调用 | Node 与 Edge 编排 |
| 参数/结果校验 | 输入输出均显式校验 | args schema 显式，输出需自行约束 | 取决于 Node 实现 |
| 重试与超时 | 代码中清晰可见 | 可通过 Runnable 策略包装 | 可用条件边表达恢复路径 |
| 循环上限 | `max_steps` | 调用方负责 | State 条件 + recursion limit |
| Trace | 自定义 `TraceEvent` | Callback / LangSmith 接口 | State history / stream |
| Checkpoint | 自定义 JSON Store | 不属于核心 Runnable | 编译图时注入 checkpointer |
| Interrupt | 手写暂停协议 | 不属于核心 Tool | `interrupt` + `Command(resume=...)` |
| 隐藏复杂度 | 最少，代码量最多 | 中等 | 状态合并与恢复语义较复杂 |

学习顺序固定为原生 → LangChain → LangGraph。框架版本只改变表达方式，工具权限、
校验、错误边界、重试上限和成功证据规则仍由 Kaiji 负责。
