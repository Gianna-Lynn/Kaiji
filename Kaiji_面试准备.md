# Kaiji 面试准备

整理日期：2026-11-18。以当前 `develop` 的实现和仓库内验收记录为依据。
本文是复习提纲，不是本日重新运行测试的报告。文内文件链接均相对于仓库根目录。

## 1. 先记住项目定位

Kaiji 是一个面向可靠工具调用的 Python 单 Agent Runtime 实验项目。
核心目标是把模型动作变成可校验、有界执行、可追踪、可评测的过程。
Azusa 负责检索；Kaiji 通过 HTTP Adapter 使用它的搜索与证据读取接口。

当前分支职责：`develop` 用于学习开发，`main` 用于稳定展示与验收，`solution` 保存参考和扩展实现。
面试陈述应区分项目已有能力与自己能够解释、修改和验证的部分。

## 2. 架构图：三条路径分别理解

### 原生执行与评测

```text
AgentEvalCase：问题、运行限制、评分预期
       |
       v
AgentEvalRunner：按用例创建 Provider / Executor
       |
       v
AgentRuntime.run(question) <----> DecisionProvider.decide(messages)
       |                           返回动作或结构化失败
       | 工具调用
       v
ToolExecutor ---- 按名称查找 ----> ToolRegistry
       |                           保存 ToolDefinition
       | 输入校验 -> 超时等待/有界重试 -> 输出校验
       v
handler
       +--> 本地计算等工具
       +--> AzusaAdapter --> Azusa HTTP 网关 --> 检索/读取正文
       |
       v
ToolResult --> Runtime 回填消息、记录 Trace、保存成功结果
                       |
                       +--> 下一轮决策，或明确停止
                       v
                   RunResult
                       |
                       v
AgentEpisode：完整结果、运行 ID、用例 ID、耗时
                       |
AgentEvalCase ----------+--> Scorer --> AgentScore / 聚合报告
```

Runner 是可选的评测入口；直接运行 Demo 时，由 Demo 组装 Runtime。
Provider 的真实 HTTP 请求与失败重试位于 Provider 实现中；Runtime 处理其返回的动作或最终失败。
Registry 本身不执行工具，Executor 按注册契约执行。Runtime 的 `completed` 表示收到了最终答案，
不保证答案内容正确；正确性由独立 Scorer 按用例契约评估。

入口：[Demo](src/kaiji/demo.py)、[Runtime](src/kaiji/runtime.py)、[工具执行](src/kaiji/tools.py)、
[Azusa 工具注册](src/kaiji/azusa_tools.py)、[Adapter](src/kaiji/adapters/azusa.py)、
[Runner](src/kaiji/eval_runner.py)、[Scorer](src/kaiji/eval_scorer.py)。

### LangGraph 工作流与人工审批

```text
START -> router
           +--> search --> 有证据 ------------------------> answer -> END
           |       +----> 无证据且达到检索上限 -----------> max_steps -> END
           |       +----> 可恢复且尚未改写 -> rewrite --回到 search
           |       +----> 其余无证据情况 -----------------> failure -> END
           |
           +--> confirm -> interrupt 暂停
                              |
                     同一 thread_id 恢复，提交严格 bool
                              +--> True  -> approved -> END
                              +--> False -> rejected -> END

节点共享 AgentState <----> LangGraph Checkpointer <----> SQLite 文件
```

这是 Kaiji 内另一条确定性工作流：检索节点直接调用 Adapter，绕过原生 Runtime、Registry 和 Executor。
路由采用关键词规则，回答采用证据摘录；批准分支只模拟操作获批，不执行真实删除、发送或发布。
框架负责节点调度、状态更新和检查点机制，业务路由、严格审批、查询改写和停止规则由应用代码规定。
默认 MemorySaver 仅存内存；显式注入 SqliteSaver 才使用 SQLite 持久化。

入口：[工作流](src/kaiji/langgraph_workflow.py)、[SQLite 生命周期](src/kaiji/checkpointing.py)、
[新连接恢复测试](tests/test_sqlite_checkpoint_resume.py)、[框架对照](docs/framework-comparison.md)。

### Formal 实验恢复

```text
冻结题集与配置 -> Manifest / 数据指纹
                         |
                         v
Formal Runner -> AgentEvalRunner -> Episode 逐条落盘 -> Scorer / 报告
      |                                  |
      +---- 请求账本、预算与未决请求检查 --+
      +---- 重启后核对历史记录，跳过已完成的 (case_id, repeat_index)
```

这里只恢复实验矩阵进度，不是恢复一个中断的模型请求，也不保证业务副作用 exactly-once。
JSON Checkpoint Store 是独立的显式快照存取实现，没有自动接进 Native Runtime 或成为 SQLite Saver 后端。

了解职责即可，暂不新增源码精读任务：[Formal](src/kaiji/formal_runner.py)、[JSON 持久化](src/kaiji/persistence.py)。

## 3. 三分钟讲解稿

下面是表达参考；讲述时只声称自己实际掌握或完成的工作。

> Kaiji 是一个面向可靠工具调用的 Python 单 Agent Runtime 实验项目。它位于检索项目 Azusa 上层：
> Azusa 负责找资料，Kaiji 负责组织模型与工具的交互、控制执行边界，并保留过程用于评测。
>
> 我从原生 Agent Loop 开始实现了工具注册、参数校验和执行器。Provider 根据消息返回工具调用或最终答案，
> Runtime 限制最大执行步数，对相同工具名和参数的重复调用提前停止；
> Registry 保存允许调用的工具，Executor 负责输入校验、执行、超时等待、有界重试和输出校验。
> 工具结果会回填消息并记录 Trace，只有成功且通过校验的结果进入成功结果集合。
> 这样模型输出不能直接变成任意 Python 函数执行，工具失败也有明确分类。
>
> 状态恢复方面，另有一条独立的路径, 我用 LangGraph 的 interrupt 暂停工作流并等待人工审批，用 SQLite Checkpointer 保存检查点。
> 测试会关闭第一次连接，再创建新的 Saver 和 Graph，以同一 thread_id 恢复。
>
> 评测方面，我实现了独立的评测模块。Case 保存输入和预期，Runner 执行 Runtime 并生成 Episode，
> Scorer 检查工具、参数、事实文本、引用和停止原因。我用脚本注入瞬时失败、超时等故障，
> 在相同的 20 条用例上做了四组消融，分别开关工具重试和重复调用保护。
> 两项机制都开启时，严格故障回归通过率从基线的 12/20（60%）提高到 18/20（90%）。
> Formal Runner 进一步管理固定实验矩阵、逐条落盘、请求账本和恢复。

## 4. 核心对象：不要混淆职责

| 对象 | 保存或负责什么 | 面试中的一句话 |
|---|---|---|
| Message | 用户、assistant 调用、tool 结果等消息 | 模型能看到什么；当前原生最终答案单独保存在 RunResult.answer |
| ToolCall / ToolResult | 一次调用请求及其成功输出或结构化失败 | 请求与结果通过 call_id 配对 |
| AgentState | 图节点共享的执行状态 | 工作流走到哪里、下一步怎么走 |
| Checkpoint | 可恢复的执行快照及相关框架信息 | 中断后从哪个进度继续 |
| Saver / SQLite | 检查点存取组件 / 磁盘存储后端 | Saver 负责存取，SQLite 提供持久化 |
| Memory | 应用明确选择保存的跨会话事实 | 与一次运行的检查点分开管理 |
| Trace | 动作、工具结果、失败和停止事件 | 过程发生了什么，失败也要留下记录 |
| Case / Episode / Score | 预期 / 实际执行记录 / 规则判定 | 执行完成与任务成功分开判断 |

## 5. 最容易被追问的执行边界

| 问题 | 应答重点 |
|---|---|
| 为什么自己写 Runtime，又使用 LangGraph？ | 原生实现显式理解控制流；框架路径对照状态编排与恢复机制。两条路径没有自动叠加能力 |
| 为什么输入输出都校验？ | 输入校验在 handler 前阻止非法参数；输出校验阻止错误结果成为成功载荷。类型标注本身不执行运行时检查 |
| 最大步数怎样计数？ | 原生 Runtime 按决策轮数；图工作流按 search 尝试次数；工具 attempts 按 handler 尝试次数，三者不同 |
| 重试在哪里？ | Executor 处理工具重试，当前 max_retries 只允许 0 或 1；Adapter 分类错误、不循环重试；图中另有一次查询改写 |
| 可重试就一定重试吗？ | 还要看开关和剩余预算。retryable 是错误分类，预算耗尽不改变分类 |
| 去重看 call_id 吗？ | 业务去重看工具名与排序后的参数 JSON；另行拒绝复用 call_id，视为模型协议失败 |
| 工具最终失败后会怎样？ | 写工具消息与 Trace，以 tool_failed 停止，不把失败加入成功结果集合；当前原生 Runtime 不让模型继续纠错 |
| 超时后 handler 停了吗？ | 只结束等待，运行中的线程可能继续；重试可能重叠。有副作用工具需额外设计幂等键、去重或对账 |
| 审批为什么不接受 1？ | 这是权限决定，只接受真正的 bool，不能依赖 truthy/falsy，字符串 false 也可能被当作真 |
| 为什么选 SQLite？ | 本地文件、无需独立服务、官方 Saver；适合单机演示，不声称已解决分布式恢复 |
| 为什么恢复还检查版本？ | 当前 resume_graph 只接受 state_schema_version=1；update_state 能写入 0，但恢复入口会拒绝。没有自动迁移 |
| 新图怎么恢复旧运行？ | 重新构建相同节点与连边，用新 Saver 连接同一数据库，以原 thread_id 找回检查点 |

相关直接测试：[Runtime](tests/test_runtime.py)、[Executor](tests/test_executor.py)、
[SQLite](tests/test_sqlite_checkpoint_resume.py)。

## 6. 评分和故障实验：必须说清分母

单条任务成功要求停止原因、工具选择、参数、目标事实、引用和要求的重试恢复全部通过。
当前工具与参数判定聚焦首次工具调用；字典参数按期望子集比较，列表按完整值比较。
事实检查是规范化后的字面匹配，不做同义理解、否定识别或自然语言蕴含判断。
预期失败的用例也可能“评测成功”，因为它正确触发了所要求的安全停止。

| 指标 | 计算范围 |
|---|---|
| 任务成功率 | 全部 Episode |
| 工具选择 / 参数准确率 | 要求工具的 Episode；工具选错时参数项也失败 |
| 无工具准确率 | 要求不调用工具的 Episode |
| 重试恢复率 | 要求重试恢复的 Episode |
| 引用 Precision | 全局受支持引用数 / 全局实际引用数 |
| 引用 Recall | 全局受支持引用数 / 全局允许引用数 |

受支持引用 = Case 允许的 ID ∩ 回答引用的 ID ∩ 本次成功工具证据 ID。
每条 Episode 内 ID 去重，再跨 Episode 累加计数；引用指标不平均单条比例，分母为零记 None。
布尔类聚合指标没有适用样本时按当前规则记 0.0。
引用 ID 合法不等于正文支撑所有陈述；Precision=1 也不保证引用完整、答案正确。
当前 Token 汇总把未报告值按 0 累加，不能把这一汇总解释为精确账单。

[faults.py](src/kaiji/faults.py) 按队列制造 handler 或 HTTP 故障；每次调用消费一步。
它不决定重试，恢复策略在 Executor、Provider 或工作流自己的边界内实现。
工具层 attempts 与 HTTP 请求次数分别计数，不能混用。

## 7. 数字怎么讲：带日期、范围和来源

| 证据 | 可陈述的结果 | 结论边界 |
|---|---|---|
| 2026-11-13 迁移验收 | main/develop 离线各 611 passed；独立真实网关 fixture 各 1 passed | 离线回归排除 live fixture，二者分开运行；不作为 11 月 18 日重跑结果 |
| 同轮 Formal Preflight | Scripted 行为 120 条、故障 60 条 | 真实模型请求为 0，不能称为模型成绩 |
| 2026-08-12 历史模型行为 | 严格成功 116/120；工具选择 96/96；参数 92/96；无工具 24/24 | 固定模型、提示、五工具和受控 Mock；120/120 仅表示矩阵完成 |
| 项目笔记 HTTP 联调 | BM25 搜索、取实际 ID、回读正文、校验和引用 | 规则 Provider 与真实 HTTP；没有真实 LLM 自主规划 |

来源：[迁移验收](docs/migration-acceptance.md)、[历史交付基线](docs/delivery-baseline.md)、
[P1 验证](docs/p1-validation.md)。历史旧分支名按其记录时点理解，当前导航看 README。
历史 5 条模型 Live 提供过工具参数和目标引用引导，不能用来宣称自主检索质量；
不与后续规则 HTTP 联调合并分母。hash 链与源码散列用于一致性核验，不认证来源真实性。

## 8. 已有边界和后续改进

- Native Runtime 尚未自动接入持久化恢复；JSON 快照、图线程恢复和 Formal 矩阵恢复分别存在。
- 真实副作用执行、业务 exactly-once、旧状态自动迁移未由当前审批示例实现。
- 受控评分不能替代开放域回答质量评测；可增加语义判定，但需要额外评估误判、成本和稳定性。
- 线程超时后取消任务、幂等与未知执行结果对账仍需针对具体工具设计。
- 服务、Chat/Web、Docker 和 CI 属于 solution 扩展，不能把它们算成当前 develop 已验收能力。
- 按 11 月 13 日展示复核记录，main 缺少本分支自动回归工作流；wheel 不包含外层题集，完整展示依赖源码 checkout。
- TODO8—12 尚未完成新版教学独立审计；编号 TODO 的存在不能直接作为功能未实现的依据。
- 多 Agent、分布式 Runtime、Kubernetes、vLLM 和复杂长期记忆属于本阶段非目标。

具体改进与已知限制来源：[展示复核](docs/showcase-review.md)、[当前协作规则](AGENTS.md)。

## 9. 面试前最后一轮练习

1. 不看源码，用三分钟讲完第 3 节，并用架构图指出每个组件的位置。
2. 解释一次成功、一次未知工具失败、一次重复调用和一次步数耗尽；分别指出消息、Trace 和成功结果的变化。
3. 解释“工具执行一次，Runtime 为什么走两步”“新图为什么能恢复旧状态”“completed 为什么还可能判失败”。
4. 用一个例子解释引用 Precision 与 Recall，再说明两者都高仍不能证明什么。
5. 对每个数字补上日期、数据与环境限制；记不清时回查来源，不补造结果。

需要现场演示时，在本地 ChuoEnv 中先核对导入路径，再运行最小 Demo：

```powershell
python -c "import kaiji; print(kaiji.__file__)"
python -m kaiji.demo
```

导入应指向当前主目录的 src/kaiji。预期为答案 90、completed、2 步、一次工具执行；
动作与答案均由脚本预设。此处提供复现入口，本次文档编写未重新运行它。

阅读不足时按 [最小路线](重新捡起Kaiji.md) 回查已有文件；剩余交付基线可配合第 7、8 节阅读，先完成讲述和追问练习。

## 10. 本次面试复盘

1. **重点追问：为什么只有 20 条用例？为什么没有在更大的数据集上评测？** 四组消融用的是同一批 20 条故障用例。这个规模能检查预设故障下重试和判重有没有起作用，目前还缺大样本上的覆盖和泛化证据。
2. **脚本怎么注入失败和超时？** 面试官细问了故障怎么触发、工具超时后发生什么，以及怎么从结果和 Trace 判断 Agent 是恢复了还是停了。这块目前主要靠受控脚本验证。
3. **循环为什么最多两轮？** 我在面试里讲的设置把上限设成了两轮，能展示一次工具调用后给出回答，对多步工具链来说还是偏简单。代码里的 `max_steps` 可以配置，更长任务上的循环和停止策略还缺验证。
4. **上下文怎么组织？** 这块我在 Kaiji 里几乎没展开。面试官给了一个具体场景：用户 Prompt、第三方 API 返回和本地知识库检索结果同时放到 Agent 面前，它应该怎么理解三者？追问涉及怎么区分用户意图和外部证据、记录来源，以及遇到信息冲突怎么处理。
5. **工具怎么重试，怎么防止重复调用？** 当前只做了有限重试和同名同参判重。超时后原调用是否还在执行、带副作用的工具能不能安全重试，以及参数写法不同但业务意图相同的调用怎么处理，现有约束覆盖得还少。

整体感受：这个项目还比较稚嫩。面试里最明显的短板是评测规模、多步任务、上下文组织和工具故障处理。
