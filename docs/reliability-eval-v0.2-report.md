# Kaiji Reliability Eval v0.2 验证报告

## 结论边界

本报告记录 2026-08-12 在正确答案扩展分支上的验证结果。Scripted 实验用于验证
Runtime、故障机制、Scorer 和报告管线，不是模型能力成绩。DeepSeek Smoke 与
Azusa Live Pilot 是小规模受控验证，不是公开 Benchmark 或生产数据。

## 已执行结果

| 验证层 | 结果 | 可以证明 | 不能证明 |
|---|---:|---|---|
| Contract Suite 与全量单测 | 通过 | 当前直接断言与实现一致 | 未覆盖环境或真实模型行为 |
| Scripted Smoke | 6/6 | Runner 与 Scorer 小闭环可运行 | 真实模型工具能力 |
| Scripted Formal | 180 Episode 完整 | 60 条数据、重复、聚合与 Bad Case 管线完整 | Agent 成功率 |
| Harness Reliability Ablation | `full_kaiji` 0.90 | 重试和重复保护在固定故障集上的机制差异 | 对真实模型的普遍提升 |
| DeepSeek Real Smoke | 6/6 | 真实模型、类型化工具、Runtime、Trace 与 Scorer 闭环 | 大规模稳定性 |
| DeepSeek Guided Pilot | 20/20 | 20 条单次受引导协议路径可执行 | 自主工具选择、重复一致性或正式结论 |
| DeepSeek Autonomous Pilot 尝试 | 20 条已执行，结果无效 | 共享 Prompt、完整五工具和真实失败记录可运行 | 自主行为成功率；原题集不适合真实模型 |
| DeepSeek Autonomous Behavior Pilot v2 | 严格 19/20 | 同一 Prompt、五工具下的自主选择、参数、回答与引用 | 重复稳定性或 Formal 结论 |
| Formal Preflight | Behavior 120 完整；Reliability 60 完整 | 数据矩阵、逐条落盘、恢复与分报告管线 | 真实模型重复稳定性 |
| DeepSeek Real Behavior Formal | 严格 116/120 | 40 条行为在三次重复下的工具、参数、回答和引用表现 | 开放域或生产稳定性 |
| Azusa HTTP 契约 | 3 次搜索 + 3 次按 ID 读取通过 | fixture 上搜索与稳定 ID 一致 | Hybrid/Dense 效果 |
| DeepSeek + Azusa Live | 5 条最终通过 | 真实模型选择工具、HTTP Adapter、结果回填和引用约束 | 大规模 RAG Benchmark |
| SQLite 恢复 | 通过 | 跨 Graph/连接恢复、严格 bool 与线程隔离 | 分布式或高并发持久化 |

真实模型阶段共使用 22 次 DeepSeek 请求：连通验证 1 次、Real Smoke 10 次、
前四条 Azusa Live 8 次、第五条首次失败 1 次、预热后重跑 2 次。两份成功报告
分别记录 7,164 与 6,229 Token，第五条成功重跑记录 1,543 Token。按实验配置
估算的成功批次总成本约 0.00226 美元；连通请求与失败请求未进入这些批次报告，
因此不能把该数字表述为账户的精确扣费。

随后执行的 20 条真实 Guided Pilot 使用独立授权：最多 40 次请求、每次最多输出 512
Token、软费用闸门 0.02 美元、连续两次鉴权/余额/协议异常即停止。实际完成 20/20
Episode，使用 36 次请求，输入 25,621 Token、输出 1,674 Token，按缓存未命中
价格估算 0.00405566 美元。Task Success、Tool Selection、Argument Accuracy、
Citation Precision 与 Citation Recall 在受引导条件下最终均为 1.0；平均 1.8
Steps，P50/P95 Episode 延迟约 2.91/5.84 秒。该批次按场景使用了直接给出预期
工具和参数的 System Prompt，并只暴露相关工具子集，因此不能把 Tool Selection
1.0 表述为模型自主选择能力。本结果每条只运行一次，也不能表述为稳定成功率。

## Bad Case 复核

Scripted Formal 中 6 个失败 Episode 都来自两条空证据 case 的三次重复：模型替身
在没有成功证据时仍生成 `kaiji-001` 引用，Scorer 将其判为 Citation 失败。这是
刻意保留的负向样例。

第五条 Azusa Live 首次运行中，DeepSeek 正确选择 `search_azusa`，参数也完全
正确，但两次真实 handler 尝试都超过 4 秒工具超时，Runtime 以 `tool_failed`
停止，失败结果未进入成功证据。随后同一 HTTP 查询在预热状态下耗时 69 ms，使用
不变的超时契约重跑后通过。首次失败报告必须保留，不能只展示成功重跑。

真实 Pilot 初始评分曾出现 0.95，原因是 Scorer 映射错误地要求自然问候回答固定
协议标记；移除该标记后又发现 Scripted case 自带的固定短语仍不适用于真实自然
回答。最终真实 no-tool 契约只评分“没有工具调用且正常停止”。两次均只对原始
Episode 离线重评分，没有新增模型请求；原始回答、消息与 Trace 未被修改。

审查还发现 Guided Pilot 的场景专用 System Prompt 与工具子集会泄露金标。后续
Behavior Pilot 已改为所有 case 共享同一 System Prompt 和同一套五个工具，且提示
不包含预期工具、参数或停止原因。

该修正版随后按单独授权执行了 20 条、每条一次：共 32 次请求，输入 21,023 Token、
输出 1,258 Token，按缓存未命中价格估算 0.00329546 美元，未触发 40 次请求或
0.02 美元闸门。原始严格聚合为 Task Success 0.10、Tool Selection 0.30、
Argument Accuracy 0.15、Citation Precision/Recall 0.85；停止分布为 `completed` 5、
`tool_failed` 1、`duplicate_call` 1、`model_failed` 13。

独立复核后，这组聚合必须标记为**方法学无效，不能作为模型成绩**。其中 12 条由
真实网络层 `transport_error` 结束，1 条因一次返回多个工具调用触发单工具协议拒绝；
更根本的问题是，它错误复用了为 Scripted Provider 编写的行为题。诸如“折扣计算
样例三”“检测重复动作”“不要结束直到达到步数”没有提供可自主推导的业务参数，
而故意重复、持续不终止和故意非法参数本应属于 Reliability/Fault Suite。原始
Episode 与 0.10 严格分数均保留，不能删去失败后重新包装成有效成绩。

复核同时发现两个契约歧义：旧 `percent` 说明没有说清它表示“减免比例”，导致
“九折”被合理解析为 90，工具实际返回 10 后模型又自行回答 90；`local_search` 与
`search_azusa` 的旧说明也没有清楚区分不可引用的演示搜索和带稳定 chunk ID 的证据
搜索。Schema 说明已离线收紧。新的 `real_behavior_cases.jsonl` 使用五组各四条的
自然用户意图：无工具、折扣、本地搜索、资料读取与 Azusa 引用；循环和非法参数不再
混入真实 Behavior Pilot。

重新设计后的 20 条 Autonomous Behavior Pilot v2 随后按独立授权运行一次。请求硬
上限 50，实际使用 37；费用软上限 0.02 美元，按已报告的 42,823 输入 Token 与
1,667 输出 Token 估算为 0.00646198 美元。20 条都以 `completed` 停止，没有网络
重试或隐藏失败。严格 Task Success 为 0.95，Tool Selection 1.0、Argument Accuracy
0.95、Citation Precision/Recall 均为 1.0，平均 1.85 Steps，P50/P95 Episode 延迟
约 3.17/4.05 秒。

唯一严格 Bad Case 中，用户要求以 `Kaiji` 为检索关键词，模型选择了正确的
`search_azusa`，但把 query 扩写为 `Kaiji 工具调用方式`。Mock 仍返回正确证据，模型
正确回答“类型化工具调用”并引用 `kaiji-001`。因此可分别表述为“20/20 完成功能
路径”和“19/20 满足固定参数金标”；正式严格成绩仍保留 0.95。大橘没有在看到输出
后放宽参数 Scorer 将其改写成 1.0。

## Harness 机制映射

| 问题 | Kaiji 机制 | 直接指标 |
|---|---|---|
| 单次瞬时工具失败 | 一次有界重试 | Retry Recovery、handler 尝试数 |
| 相同业务调用仅改变 call ID | 名称与参数签名重复保护 | `duplicate_call` 与 Steps |
| 持续产生不同调用 | 最大步骤 | `max_steps` 分布 |
| 失败证据污染回答 | 仅保存成功且校验后的 ToolResult | Citation Precision / Recall |
| Provider 或协议失败 | 结构化失败与 `model_failed` | Provider Failure 分布 |

20 条 Reliability Case 上，`bounded_baseline`、`no_retry`、
`no_duplicate_guard`、`full_kaiji` 的 Task Success 分别为 0.60、0.70、0.80、
0.90。该结果只描述固定 Scripted 故障集；真实模型是否保持相同差异仍需正式实验。

## Formal Preflight

离线 Preflight 使用 `real_behavior_cases.jsonl` 与 `real_paraphrase_cases.jsonl` 组成
40 条真实行为契约并重复三次，生成 120 Episode；另将 20 条 `fault_cases.jsonl`
重复三次，生成独立 60 Episode Reliability 报告。Behavior Mock 结果为 1.0；
Reliability 结果为 0.90，6 个 Bad Case 仍来自两条空证据伪引用场景的三次重复。
这组结果验证数据、Runner、Scorer、逐条持久化和报告边界，不是模型成绩。

Formal Runner 已实现配置与数据指纹、System Prompt 与工具 Schema hash、逐请求预算
账本、Episode hash 链、部分报告、异常中断恢复、已完成坐标去重和篡改拒绝。发现
已开始但未完成记账的请求时会以 `unresolved_request` 停止，避免未知 Token 成本被
当作零。真实 DeepSeek Session 装配入口已实现，但本轮没有调用。

## DeepSeek Real Behavior Formal

在独立授权下，40 条真实行为题各运行三次，共完成 120/120 Episode。所有 case 使用
同一 System Prompt、同一五工具、`deepseek-v4-flash`、thinking disabled、
`temperature=0` 与 `max_tokens=512`。实际开始并完成 225 次请求，输入 261,842
Token、输出 11,206 Token；按缓存未命中单价保守估算 0.03979556 美元，低于 280
次请求与 0.06 美元闸门。没有网络重试、未决请求或异常停止。

严格 Task Success 为 116/120，即 0.9667。冻结审计发现旧聚合器把不适用样本当满分
稀释指标；按已落盘原始 Episode 离线重算后，要求工具的 96 个 Episode 中 Argument
Accuracy 为 92/96，即 0.9583，Tool Selection 为 1.0；24 个无工具 Episode 的
No-tool Accuracy 为 1.0，Citation Precision/Recall 均为 1.0。本 Formal 没有重试恢复
适用题，因此该项为 0.0 而不是伪造满分。120 条全部以 `completed` 停止，平均
1.875 Steps，P50/P95 Episode 延迟约 3.14/4.99 秒。三轮严格成功率分别为
39/40、38/40、39/40。

四个 Bad Case 全部是 Azusa query 未精确满足固定参数金标：

- `real-behavior-citation-01` 三次都把 `Kaiji` 扩写为 `Kaiji 工具调用方式`；
- `real-behavior-citation-04` 第二次使用 `Kaiji 调用方式` 并显式给出 `k=5`。

四条均选择正确工具、正常完成、使用成功证据、回答事实正确且引用受支持。因此可以
补充描述“功能路径 120/120”，但正式严格成绩仍是 116/120；不得在看到结果后放宽
参数 Scorer。该结果是受控 Mock 工具 fixture 上的真实模型行为实验，不是公开 Benchmark、
开放域检索或生产数据。

## 冻结独立审计

40 条真实行为 × 3 次的正式实验已经完成。20 条故障 × 3 次继续由受控
Provider/Fault Injector 执行，不计入真实模型 Episode。本轮已经从契约、危险输入、
强制要求直接断言、学习者子 TODO 和框架职责五个视角复审 TODO8—TODO12，并修复严格
类型、有限 JSON、空白身份、Trace 步序、未知字段、适用指标分母、Search 请求响应配对、
`tool_call_id` 复用与 RunResult 跨 artifact 矛盾。Azusa Live 只使用 3 条公开 fixture
的 BM25 模式。Dense/Hybrid 首次加载 Embedding 模型超过
默认 3 秒 Adapter 超时，因此没有形成 Hybrid Live 成绩。FastAPI、Docker、服务
压测、Multi-Agent、MCP 和复杂 Memory 均属于任务单明确暂缓范围。

重新设计后的 `real_behavior_cases.jsonl` 已形成一次有效单次 Pilot，但每条只运行
一次，仍不能说明重复一致性。此前 Guided Pilot 只能作为协议与执行路径证据；第一次
Autonomous Pilot 尝试虽然满足共享 Prompt 和完整工具集，却因题集不成立及大量传输
失败而无效。三者必须分别报告，不能只展示最好的一组数字。

当前答案实现的离线断言、书面契约、独立设计审计和框架横向职责已经收口；真实 Azusa
已于 2026-08-12 再次用受控 BM25 fixture 独立复验并得到 pytest 退出码 0，但结论仍只限
于这三条公开 fixture。下一阶段可以在同步上述契约后，从答案分支按
L1 Provider 边界、L2 Runner/Scorer/SQLite、L3 完整 Experiment 顺序挖教学 TODO；
不得把 TODO 挖掘后的练习分支绿/红灯验收倒推成新的真实模型或生产结论。
