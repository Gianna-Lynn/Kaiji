# Eval、实验结论与 Bad Case

## 证据口径

本页整理答案基线 `47cd4a5` 在 2026-08-12 已完成的历史验证。P1 只做静态包装，没有
重新运行测试、真实模型或 Azusa Live。原始口径以
[`reliability-eval-v0.2-report.md`](../reliability-eval-v0.2-report.md) 和
[`freeze-audit-v0.2.md`](../freeze-audit-v0.2.md) 为准。

## 四类证据不能混用

| 证据 | 用途 | 不能证明 |
|---|---|---|
| Contract Suite | 实现满足当前直接断言 | 真实模型质量 |
| Scripted/Fault Suite | 机制在可控故障下的行为差异 | 对真实模型的普遍增益 |
| Real Behavior Formal | 固定 Prompt/工具/数据下的模型行为 | 开放域、用户流量或生产 SLA |
| Azusa Live | 小型 fixture 上的跨仓库 HTTP 闭环 | 大规模 Dense/Hybrid 检索效果 |

## 历史结果摘要

| 实验 | 结果 | 正确表述 |
|---|---:|---|
| 冻结测试记录 | 541 passed，1 live skipped | 当前答案与已覆盖契约一致；skip 不等于 Live 通过 |
| Azusa BM25 Live | 3 次搜索 + 3 次按 ID 读取通过 | 三条公开 fixture 的语义接口一致 |
| Scripted Smoke | 6/6 | Runner/Scorer 小闭环可运行 |
| Reliability Formal | 60 Episode，`full_kaiji` 0.90 | 固定故障集上的机制结果 |
| DeepSeek Behavior Pilot v2 | 19/20 严格通过 | 单次自主行为 Pilot，不证明重复稳定性 |
| DeepSeek Real Behavior Formal | 116/120 严格通过 | 40 条行为 × 3 次的受控 Mock 工具实验 |
| DeepSeek + Azusa Live | 5 条最终通过 | 真实模型、HTTP Adapter、证据回填与引用闭环 |
| SQLite Resume | 通过 | 新连接、线程隔离和严格审批值；不证明分布式一致性 |

## Formal 指标

真实 Behavior Formal 共 120 个 Episode：

| 指标 | 分子/分母 | 值 |
|---|---:|---:|
| Task Success | 116/120 | 0.9667 |
| Tool Selection | 96/96 个要求工具的 Episode | 1.0000 |
| Argument Accuracy | 92/96 个要求工具的 Episode | 0.9583 |
| No-tool Accuracy | 24/24 个无需工具的 Episode | 1.0000 |
| Citation Precision | 全部实际引用 | 1.0000 |
| Citation Recall | 全部允许引用 | 1.0000 |
| 平均步骤 | 120 Episode | 1.875 |
| 延迟 P50/P95 | 120 Episode | 3.14 / 4.99 秒 |

三轮严格成功分别为 `39/40`、`38/40`、`39/40`。共开始并完成 225 次真实请求，输入
261,842 Token、输出 11,206 Token；按实验配置的缓存未命中价格估算约 0.0398 美元。
这不是账户账单，只是可复核的实验估算。

## Reliability Ablation

四个 Harness 保持相同 Provider、数据、工具 Schema、最大步骤和校验，仅切换一次工具
重试与重复调用保护：

| Harness | Retry | Duplicate guard | 固定故障集 Task Success |
|---|---:|---:|---:|
| `bounded_baseline` | 关闭 | 关闭 | 0.60 |
| `no_retry` | 关闭 | 开启 | 0.70 |
| `no_duplicate_guard` | 开启 | 关闭 | 0.80 |
| `full_kaiji` | 开启 | 开启 | 0.90 |

这里只能得出“两个机制在该受控 Scripted 故障集上形成差异”。它不能推出在任意真实模型、
真实工具或生产流量中稳定提升 30 个百分点。

## 关键 Bad Case

### 1. 无效 Autonomous Pilot

初始 Autonomous Pilot 的严格分数为 0.10，但 12 条由真实网络 `transport_error` 结束，
且题集混入“故意重复”“故意非法参数”等无法从自然用户意图自主推导的要求。项目保留
原始 Episode，同时把这组实验判定为方法学无效，没有挑选成功样例重新包装。

修正：把正常 Behavior 与可控 Reliability/Fault Suite 分离；真实模型题目必须给出
推导必填工具参数所需的信息。

### 2. Guided Pilot 泄露金标

Guided Pilot 按场景暴露工具子集，并在 System Prompt 中给出预期工具和参数。20/20 只
证明协议路径可以执行，不能证明模型自主选择。

修正：Behavior Pilot v2 使用同一 System Prompt 和同一套五工具。

### 3. Scorer 分母稀释

旧聚合器把指标不适用的 Episode 当作满分，导致 Argument Accuracy 被普通题稀释。

修正：Tool Selection/Argument Accuracy 只以要求工具的 Episode 为分母，No-tool、Retry
Recovery 和 Citation 分别使用自己的适用集合；没有适用项报告 0.0，不伪造满分。

### 4. Azusa 冷态超时

第五条 Live Case 首次运行中，两次只读 handler 都超过 4 秒，Runtime 以 `tool_failed`
停止，失败结果没有进入成功证据。预热后相同查询约 69 ms 并成功重跑。

结论：一次成功不能覆盖冷态失败；线程 timeout 也不能证明已开始的 handler 被终止。

### 5. Formal 参数金标不满足

四个严格 Bad Case 都选择了正确工具、获得正确证据、回答正确且引用受支持，但模型将
精确 query `Kaiji` 扩写为 `Kaiji 工具调用方式` 或显式增加 `k=5`。

结论：可以报告功能路径 120/120，但严格成绩仍是 116/120；看到结果后不能放宽金标。

## 指标设计规则

- `EvalCase` 是预期契约，`AgentEpisode`/`EvalObservation` 是实测事实。
- case ID 和 run ID 必须在各自作用域唯一，不能靠字典覆盖重复项。
- 缺失或失败 Episode 不能通过省略缩小分母。
- 引用必须同时属于 case 允许集合和本次运行成功证据集合。
- P95 使用固定 nearest-rank 规则；小样本 P95 只能描述该样本，不能冒充尾延迟 SLA。
- Scripted 机制实验、真实模型行为实验和 Azusa 检索实验必须分开报告。

## 尚未覆盖

- 开放域人工标注 Benchmark；
- 多模型显著性检验和置信区间；
- 生产流量在线 Eval；
- LLM-as-a-judge 与人工一致性校准；
- Prompt Injection、越权工具和敏感数据红队；
- Dense/Hybrid 的 Recall@K、MRR、nDCG 与冷启动基准。
