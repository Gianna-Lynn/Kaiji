# TODO8—TODO12 学习路径审计

本审计只回答一个问题：学习者只完成当前子 TODO 时，是否有独立 artifact、窄命令和
准确反馈。它不把答案测试全绿等同于练习分支已经完成，也不提前挖新的 L1/L2/L3 题。

## 验证方法

- 绿色路径：在答案工作树逐项运行 `python -m pytest <file> -k todo_<编号>`。
- 红色路径：保持主工作树只读，用答案测试文件配合主分支 `src`，逐项运行同一命令。
- 隔离原则：测试用最小替身满足已经完成的前置能力，并让后续 TODO 不参与当前断言。
- 精确红灯：失败必须是当前能力缺失；后续 `NotImplementedError`、导入错分支或 fixture
  缺失均不能冒充有效红灯。

## 逐项结果

| 子 TODO | 当前能力 | 答案窄测试 | 主分支只读红灯 | 结论 |
|---|---|---|---|---|
| 8a | Checkpoint 严格读取 | `todo_8a` 绿 | `_read_all()` 未实现 | 精确 |
| 8b | 原子保存 | `todo_8b` 绿 | `save()` 未实现 | 精确；load/latest 已隔离 |
| 8c | 指定恢复 | `todo_8c` 绿 | `load()` 未实现 | 精确；save/latest 已隔离 |
| 8d | 最近恢复 | `todo_8d` 绿 | `latest()` 未实现 | 精确；save/load 已隔离 |
| 9a | Memory 严格读取 | `todo_9a` 绿 | `_read_all()` 未实现 | 精确 |
| 9b | 写入与覆盖 | `todo_9b` 绿 | `put()` 未实现 | 精确；get/list 已隔离 |
| 9c | 单条读取 | `todo_9c` 绿 | `get()` 未实现 | 精确；put/list 已隔离 |
| 9d | 有序列表 | `todo_9d` 绿 | `list()` 未实现 | 精确；put/get 已隔离 |
| 10a | TraceRecord 时间线不变量 | `todo_10a` 绿 | 非法时间线未被拒绝 | 精确 |
| 10b | 严格 JSONL 迭代 | `todo_10b` 绿 | `iter_records()` 未实现 | 精确；后续方法已隔离 |
| 10c | 按 run ID 加载 | `todo_10c` 绿 | `load()` 未实现 | 精确；读文件已隔离 |
| 10d | 唯一且物理分行追加 | `todo_10d` 绿 | `append()` 未实现 | 精确；replay 已隔离 |
| 10e | 稳定文本回放 | `todo_10e` 绿 | `replay()` 未实现 | 精确；文件读取已隔离 |
| 11a | Observation 跨字段不变量 | `todo_11a` 绿 | 非法组合未被拒绝 | 精确 |
| 11b | 唯一 ID 索引 | `todo_11b` 绿 | 重复 ID 未被拒绝 | 精确；对齐/公式已隔离 |
| 11c | JSONL 唯一用例加载 | `todo_11c` 绿 | 重复 ID 未被拒绝 | 精确 |
| 11d | case/observation 身份对齐 | `todo_11d` 绿 | 缺少 `_align_observations()` 骨架 | 同步骨架后才可挖题 |
| 11e | 已对齐观测的指标公式 | `todo_11e` 绿 | 缺少 `_aggregate_aligned()` 骨架 | 同步骨架后才可挖题 |

11d/11e 的主分支红灯是有价值的审计发现，但不是合格练习反馈：`AttributeError` 只说明
脚手架尚未同步，不能告诉学习者应实现哪段逻辑。因此答案冻结后整理练习分支时，必须先
同步两个带中文契约的函数骨架，再分别留下 TODO；不能直接复制答案测试并宣称题目可用。

## TODO12 的独立门槛

TODO12 没有可由 Mock 替代的完成条件。普通全量测试在未设置 `AZUSA_GATEWAY_URL` 时会
跳过，因此必须单独启动 Azusa `solution/kaiji-semantic-gateway` 的 BM25 fixture 网关，
并看到 `tests/test_azusa_live_integration.py` 明确为 `1 passed`。静态 Adapter 契约测试只
证明响应边界，不能替代这个跨仓库 HTTP 闭环。

## 后续挖题边界

本轮只验收现有 TODO8—TODO12。新的 Reliability Eval 课程应按 AGENTS.md 分成：L1
Provider 协议边界、L2 Runner/Scorer/SQLite、L3 完整实验。只有答案冻结、11d/11e 骨架
同步且每个新子题都完成相同的答案绿灯与练习精确红灯后，才可以宣称教学分支验收完成。
