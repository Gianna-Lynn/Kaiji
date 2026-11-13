# 迁移前答案工作区入口说明

以下保留迁移前 README 的完整文本；旧分支名属于历史导航，当前分支使用 main/develop/solution。

# Kaiji

Kaiji 是 Azusa 之上的独立 Agent 应用工程项目。它不重新实现 HNSW 或 RAG，而是通过工具适配器调用 Azusa，学习并实现可靠的单 Agent Runtime。

Kaiji 的核心对象是 Message、Tool、AgentState、Workflow、Checkpoint、Trace 和 Eval Case。当前课程已经搭建到 TODO 15：`main` 保留练习代码，`solution/kaiji-v0` 保存经过验证的完整参考实现与 Reliability Eval 基线。

## DeepSeek 终端对话

答案分支提供一个最小连续对话客户端。Kaiji 在本地运行 Runtime、演示工具和会话历史，并通过 OpenAI-compatible API 调用远程 DeepSeek 服务；DeepSeek 模型不会下载或运行在本机。

安装项目依赖后，在 PowerShell 中设置 API Key 环境变量并启动：

```powershell
python -m pip install -e .
$env:DEEPSEEK_API_KEY = '<你的 DeepSeek API Key>'
python -m kaiji.chat
```

默认使用 `https://api.deepseek.com` 和 `deepseek-flash`，每次模型请求最多生成 512 个 Token。可以通过 `--base-url`、`--model` 覆盖服务地址和模型；`--api-key-env` 只接收环境变量名称，Provider 从该变量读取 Key。终端支持连续提问、`reset` 清空当前会话历史、`exit`/`quit` 退出；历史只保存在本次进程内，只有成功完成的回合会被提交。当前工具限于安全演示注册表中的折扣计算、本地小型搜索和模拟资料读取。

终端入口本身不提供流式输出、持久化会话、并发会话或 Web API，也不允许运行任意代码。它是一个本地聊天客户端，模型推理和 API 计费发生在远程 DeepSeek 服务。

## 本地聊天网页与审计日志

安装依赖后，在 PowerShell 中设置 Key 所在环境变量并启动本地页面：

```powershell
python -m pip install -e .
$env:KAIJI_DS_SMOKE_CREDENTIAL = '<你的 DeepSeek API Key>'
python -m kaiji.web --api-key-env KAIJI_DS_SMOKE_CREDENTIAL
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。页面使用原生 HTML、CSS 和 JavaScript，服务默认为 `127.0.0.1`、单进程、非流式；每次模型请求最多生成 512 个 Token。会话只保存在服务进程内存中，浏览器用 `localStorage` 记住会话标识，不能在服务重启后恢复聊天历史。重置只清空当前会话。

每轮运行摘要追加到服务启动目录下的 `artifacts/web/chat-log.jsonl`，也可通过服务器命令行的 `--log-path` 改路径。日志默认只保留会话标识、时间、状态、模型、Token、工具名称和费用估算，不保存用户问题与回答；仅当服务器环境变量严格等于 `KAIJI_LOG_CONTENT=true` 时才把正文写入日志。内容日志会长期留下聊天文本，并会遮蔽可识别的 API Key、Bearer 值和常见鉴权头；请只在确有需要时启用。日志写入失败时，已生成的回答仍会返回，接口以 `207` 和 `logging_warning` 明确告警。

费用字段是峰值估算，采用 DeepSeek Flash 未命中缓存的输入 ¥2/百万 Token、输出 ¥8/百万 Token 单价，实际费用可能不同。服务不保存 API Key，不向浏览器返回鉴权信息或 Trace 原始内容。

默认只绑定 loopback。`--host` 指向非 loopback 地址时必须额外指定 `--allow-remote`；该服务没有认证，只能在可信网络内使用。此入口不提供流式输出、账户系统、持久化会话、多进程并发或公网安全配置。

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

## 后续能力拓展：L1—L3 脚手架等级

Kaiji 在完成当前课程后，新增能力采用逐步撤除脚手架的三级训练制。这里的 L1—L3
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

## Reliability Eval v0.2 扩展分支

`solution/kaiji-v0` 在课程答案基线上包含真实 OpenAI-compatible Provider、
Agent Behavior/Fault Suite、确定性 Scorer、四 Harness 对照和官方 SQLite 恢复。
现有 `eval/cases.jsonl` 仍是组件 Contract Suite，不能与真实 Agent 成绩混用。
`eval/real_behavior_cases.jsonl` 专供真实自主 Behavior Pilot；要求模型故意重复、越界
或不终止的 Scripted/Fault 场景不得计入真实模型行为成功率。

离线验证命令：

```powershell
python -m pytest
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -m kaiji.experiment --provider scripted --suite smoke
python -m kaiji.experiment --provider scripted --suite reliability --ablation
python -m kaiji.experiment --provider scripted --suite formal --repeats 3
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -m kaiji.formal_runner --preflight
```

Formal Preflight 把 40 条真实行为契约重复三次，与 20 条确定性故障契约重复三次，
分别生成 120 与 60 Episode 报告。它只使用 Scripted/Mock Provider，不调用模型 API。
真实 Formal 使用逐请求预算账本、逐 Episode hash 链和断点续跑；部分矩阵不生成
Aggregate，也不得与 Reliability 报告合并成一个“180 条真实模型成绩”。

真实模型和 Azusa 命令默认不属于日常回归，必须显式提供临时凭据、网关地址与调用
预算。验证结果、不能夸大的结论和要求追踪分别见
`docs/reliability-eval-v0.2-report.md`、`docs/reliability-eval-contract.md` 与
`docs/reliability-eval-traceability.md`；冻结范围与独立审计证据见
`docs/freeze-audit-v0.2.md`。

当前 DeepSeek Real Behavior Formal 已完成 40 条 × 3 次：严格 Task Success
116/120；按适用分母，工具选择 96/96、参数 92/96、无工具 24/24，引用
Precision/Recall 均为 1.0。该成绩来自受控 Mock 工具 fixture，不等于
开放域 Benchmark 或生产表现；详细请求、Token、费用和 Bad Case 见验证报告。
