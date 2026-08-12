# Kaiji Reliability Eval v0.2 冻结审计

## 审计范围与结论

本轮只审计答案实现、书面契约、测试与学习路径，不扩大到 Multi-Agent、MCP、服务部署、
开放域检索或生产负载。答案分支在以下四类证据上已经收口：

- 测试结果：542 条测试中 541 passed、1 skipped；全量回归的 skip 是未设置常驻 live URL
  时的真实 Azusa 集成测试。该文件已另行连接临时 BM25 网关复验并得到退出码 0；Ruff
  检查和格式检查通过。
- 契约一致性：TODO8—TODO12 与 v0.2 的持久化、Trace、Eval、Formal、Azusa 边界已补入
  中文契约和“要求 → 实现 → 直接测试”追踪表。
- 独立设计审计：先从身份、权限、序列化、恢复、证据与指标分母推导不变量，再审查实现；
  没有把答案实现本身当作判断标准。
- 学习路径：答案分支的 8a—11e 每个子题均有独立窄测试并已变绿；答案测试反跑现有
  主分支时，8a—11c 精确落在对应缺失能力，11d/11e 则准确暴露出两个辅助函数骨架尚未
  同步。该骨架同步和新的 L1/L2/L3 练习红绿灯仍属于下一阶段，详见
  `docs/todo8-12-learning-path-audit.md`。

因此，v0.2 答案可以冻结并进入教学 TODO 整理；这不等于 TODO 练习分支已经同步完成，
也不扩大为开放域、线上生产或官方 Benchmark 结论。

## 独立推导的不变量

1. 安全决定只接受真正的 bool；预算、步骤和计数不接受 bool 或数字字符串。
2. 参数、结果、证据、Trace、Checkpoint 与 Formal 产物只允许有限标准 JSON。
3. thread、checkpoint、memory、run、case 与 tool call 身份必须非空白且在各自作用域唯一。
4. 未声明的输入、输出和持久化字段不得被 Pydantic 静默丢弃。
5. Trace 步号非递减且唯一 stop 位于末尾；RunResult 的回答、停止原因、步骤、模型回合、
   工具消息和成功证据必须互相一致。
6. Search 的请求 mode 与响应 mode 配对，命中 chunk ID 唯一；失败或错配数据不进入证据。
7. 指标只在适用样本上计算；缺失、失败和不适用是三种不同语义。
8. 每次真实请求先记账，未决请求阻断恢复；Episode 坐标和 hash 链跨重启保持唯一。

## 反向构造与已修复缺陷

| 可骗过旧测试的错误实现 | 风险 | 修复与直接断言 |
|---|---|---|
| `retryable="false"`、`steps=True` | truthy/coercion 改变重试和预算行为 | StrictBool/StrictInt 与参数化负向测试 |
| JSON 文件或 metadata 含 `NaN` | 非标准值穿过读取边界，写盘或 hash 时才失败 | `allow_nan=False`、`parse_constant` 拒绝及读写测试 |
| Trace 使用 2→1 的步号 | 回放顺序与步骤统计矛盾 | 正整数、非递减时间线直接测试 |
| Pydantic 丢弃未知参数/结果字段 | Schema 对模型说一套，Executor 实际执行另一套 | `additionalProperties=false` 与输入输出 `extra=forbid` |
| 重试与引用给不适用样本满分 | 大量普通题稀释真正失败 | 适用 Episode/引用总数分母精确测试 |
| Search 回错 mode 或重复 ID | 错误检索配置或冲突证据被接受 | 请求响应配对、ID 唯一和 Schema Drift 测试 |
| 不同回合复用 `tool_call_id` | 后续消息无法可靠配对 | 第二次 handler 前以协议失败停止 |
| 篡改 RunResult 顶层字段 | 报告、Trace、消息和证据可互相矛盾 | RunResult 跨 artifact 模型不变量测试 |

## TODO8—TODO12 审计结果

- TODO8 Checkpoint：严格读、原子写、指定恢复、latest 的依赖顺序成立；补齐有限 JSON、
  空白身份和外部损坏文件边界。
- TODO9 Memory：严格读、put/get/list 共用同一损坏结论；补齐用户/key 身份与未知字段拒绝。
- TODO10 Trace：唯一终止结构、严格 JSONL、唯一 run ID、稳定 replay 均有直接断言；补齐步序
  和有限 JSON。
- TODO11 Eval：唯一对齐、固定分母、nearest-rank 与混合样本精确分数成立；补齐严格类型、
  跨字段状态和 v0.2 Scorer 的适用分母。
- TODO12 Azusa：从 Azusa 答案分支只读导出临时副本，启动三条公开 fixture 的 BM25
  网关并复验真实 HTTP 用例，得到 pytest 退出码 0；随后停止进程并删除临时副本。该验证
  不访问外网。静态审计另补齐 mode 配对、重复证据 ID、metadata 序列化与未知字段边界。

## 原生与框架横向职责

LangChain 替代 Tool/Runnable/Callback 的机械封装，但不替代输出 Schema、证据归属、失败
分类和调用 ID 生命周期。LangGraph 替代节点、边、Interrupt 与 Checkpointer 的机械编排，
但不替代 State 版本、严格审批、线程隔离、停止原因和成功证据不变量。SQLite Saver 只证明
本地同步跨连接恢复，不证明并发或分布式一致性。

## Formal 复核

离线 Preflight 重新运行得到 Behavior 120/120 complete、Reliability 60/60 complete，
不发送 API 请求。已落盘的 DeepSeek 120 Episode 经修正版 Scorer 离线重算：Task Success
116/120（0.9667）、Tool Selection 1.0、Argument Accuracy 92/96（0.9583）、No-tool
Accuracy 1.0、Citation Precision/Recall 1.0。原报告将参数错误除以全部 120 Episode 得到
0.9667，属于分母稀释；离线迁移已先保存 `report.before-rescore.json`，再改写正式报告。
原始 Episode、225/225 请求账本、Token 与费用均不变；dry-rescore 再次复核一致且不构造
Provider。
