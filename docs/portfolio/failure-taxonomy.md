# Agent Failure Taxonomy

本表把 Kaiji 已实现的检测/缓解与尚未解决的生产风险放在一起。`已检测` 不等于风险被
彻底消除，尤其是副作用、Prompt Injection、并发恢复和外部服务状态。

| 层次 | 失败 | 检测 | 当前缓解 | 可观测信号 | 剩余风险 |
|---|---|---|---|---|---|
| Provider | timeout / network / 429 / 5xx | HTTP 异常与状态码分类 | 标记 retryable，由上层显式预算至多重试一次 | `ProviderFailure`、prior attempts、latency | 未实现退避与 `Retry-After` |
| Provider | 非法 JSON / Schema drift | 严格解析被消费字段 | `model_failed`，不执行工具 | failure code、净化后的 metadata | 上游兼容差异仍需维护 |
| Provider | 一回合多个工具调用 | 响应协议校验 | 拒绝整个回合 | `invalid_response` | 当前不支持安全并行工具 |
| Provider | 工具调用 ID 复用 | 历史配对和 Runtime 集合检查 | 协议失败停止 | `model_failure` Trace | 跨系统全局唯一不保证 |
| Tool | 未知工具 | Registry lookup | handler 前拒绝，attempts=0 | `unknown_tool` | 工具发现/版本协商未实现 |
| Tool | 参数缺失、错型、额外字段 | Pydantic + `extra=forbid` | handler 前拒绝 | `invalid_arguments` | 语义正确性仍依赖工具契约 |
| Tool | 非有限或不可序列化参数 | 严格 JSON 试序列化 | handler 前拒绝 | `invalid_arguments` | 大对象/深嵌套资源限制未实现 |
| Tool | handler 暂时失败 | `ToolExecutionError.retryable` | 最多一次额外尝试 | attempts、retry recovery | 重试可能重复只完成一半的外部动作 |
| Tool | handler timeout | future 等待上限 | 结构化 timeout，可按预算重试 | latency、attempts、`tool_failed` | Python 线程不能终止已开始 handler |
| Tool | 返回值 Schema 非法 | 结果模型与有限 JSON 校验 | 不进入成功证据 | `invalid_result` | 外部动作可能已发生但结果损坏 |
| Control | 相同业务调用换 call ID 重复 | `name + canonical arguments` 签名 | `duplicate_call` 停止 | stop reason、steps | 可能误拦截上下文不同但参数相同的合法重查 |
| Control | 持续产生不同调用 | `max_steps` | `max_steps` 停止 | stop distribution、steps | 不能判断“再走一步是否值得” |
| Control | 查询改写循环 | LangGraph `retry_count` | 最多改写一次 | state、route、step count | 固定一次不是自适应策略 |
| Control | 高风险动作未经批准 | Interrupt/Resume 路由 | 只接受真正 bool；拒绝则停止 | checkpoint、`human_rejected` | 当前只模拟副作用，没有审批队列与权限系统 |
| Evidence | 工具失败污染回答 | 成功结果与全部 Tool Message 分离 | 只有成功校验结果进入证据集合 | `successful_tool_results` | 模型仍可能忽略证据；需评分发现 |
| Evidence | 空或不足检索 | workflow evidence assessment | 一次 rewrite 后 `insufficient_evidence` | stop reason、retrieval Trace | 判断规则是受控教学策略 |
| Evidence | 伪造/不支持引用 | 实际引用 ∩ 允许引用 ∩ 成功证据 | Scorer 判失败并进入 Bad Case | citation precision/recall | 不验证来源本身的真实性 |
| Evidence | Search mode/ID 错配 | Adapter 响应模型校验 | 拒绝响应，不进入证据 | adapter failure | 上游语义变化需契约升级 |
| State | Checkpoint 文件损坏 | 严格 JSON、外壳和模型重验 | 显式失败，不伪装成不存在 | load exception | JSON Store 不支持并发事务 |
| State | 跨用户 Memory 污染 | 外层 user/key 身份核对 | 按 user 隔离并拒绝错配 | store record | 无权限认证和数据加密 |
| State | 重复 thread 启动 | thread 使用检查 | 拒绝覆盖既有线程 | `ValueError`、checkpoint state | 分布式竞争未覆盖 |
| Resume | 未知成本请求后恢复 | request ledger started/finished 不一致 | `unresolved_request` 阻断 | ledger、partial report | 需要人工核对 Provider 账单/状态 |
| Resume | Episode 重复执行 | `(case_id, repeat_index)` 坐标 | 已完成坐标跳过 | episode count、hash chain | 外部副作用 exactly-once 不成立 |
| Experiment | 数据/Prompt/Schema 偷换 | Manifest fingerprints | 恢复时拒绝不一致 | manifest diff | hash 无外部签名，可被有权限者整体重算 |
| Experiment | 失败 Episode 被省略 | 固定矩阵完整性 | 不完整报告无 Aggregate | `complete=false`、missing coordinates | 进程外数据丢失仍需备份 |
| Experiment | 指标不适用项稀释分母 | 按 case 适用集合聚合 | 不适用不记满分 | denominator counts | 指标本身仍可能不代表业务价值 |
| Experiment | 金标泄露/题集不成立 | 独立审计 Prompt、工具集与题目可推导性 | Behavior/Fault Suite 分离 | 无效实验保留并标注 | 仍需要人工题集治理 |
| Service | 未认证 API 调用 | Bearer token 恒定时间比较 | 未配置 fail closed；无效 token 返回 401 | HTTP 状态 | 无身份提供方、轮换和速率限制 |
| Service | 调用者/Prompt 尝试扩权 | 请求工具集合与服务端 allowlist 比较 | 只允许缩小，每请求重建 Registry | 403、授权工具集合 | 已授权工具内仍需业务级策略 |
| Observability | 敏感载荷进入 span | 属性白名单 | 不复制问题、参数、输出与 Provider 正文 | span 属性 | 原 Trace/artifact 仍需数据治理 |

## 面试时最值得主动说明的三个限制

1. **timeout 不等于取消。** 当前线程方案只能停止等待，不能杀死已经开始的 handler；
   只读 Azusa 可接受，有副作用工具必须增加幂等键、外部状态确认或进程级隔离。
2. **Checkpoint 不等于 exactly-once。** Kaiji 能避免重复 Episode 坐标和阻断未决请求，
   但不能替外部业务系统提供事务性副作用。
3. **Eval 分数不等于生产质量。** Formal 结果来自固定 Prompt、固定工具和受控 fixture；
   真正上线还需要在线观测、安全红队、漂移检测和用户反馈闭环。

## P2 后仍没有实现的安全能力

- Prompt Injection 内容检测与工具输出内容隔离策略；
- 基于外部身份、角色和租户的细粒度 Tool Authorization；
- PII 分类、脱敏、加密和数据保留策略；
- 审批队列、Reviewer 身份与不可抵赖审计；
- 沙箱化任意代码执行；
- 多租户资源与状态隔离。

P2 只实现单租户静态 allowlist 与传输层鉴权。这些剩余缺口属于生产路线，不能在简历中
表述为已经完成。
