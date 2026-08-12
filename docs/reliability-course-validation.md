# Reliability 课程反向验收记录

本记录区分答案绿灯、练习红灯、既有能力回归和默认全量状态。它不能替代学习者完成
TODO 后的再次验收，也不把 skip 当作 TODO12 通过。

## 答案分支

- 收集：546 tests；
- 结果：545 passed、1 skipped；
- 唯一 skip：未设置常驻 `AZUSA_GATEWAY_URL` 的 live integration；答案冻结审计已另行
  启动 BM25 fixture 网关并得到该测试退出码 0；
- Ruff format/check：通过；
- TODO13a—15d 的 12 个窄命令：全部退出码 0。

## 课程分支

既有能力回归：

- TODO1—7、Runtime、Adapter、LangChain、LangGraph：301 passed；
- Fault Matrix 支持设施：10 passed；
- Ruff format/check：通过。

默认全量初始状态：546 collected，400 passed、145 failed、1 skipped。145 个失败只分布在
尚未完成的课程文件：

| 文件 | 失败数 | 对应课程 |
|---|---:|---|
| `test_persistence.py` | 57 | TODO8—9 |
| `test_tracing.py` | 24 | TODO10 |
| `test_evaluation.py` | 14 | TODO11 |
| `test_openai_compatible_provider.py` | 15 | TODO13 |
| `test_eval_runner.py` | 5 | TODO14a |
| `test_eval_scorer.py` | 5 | TODO14b—14c |
| `test_sqlite_checkpoint_resume.py` | 3 | TODO14d |
| `test_harnesses.py` | 8 | TODO15a |
| `test_experiment.py` | 2 | TODO15b—15c |
| `test_formal_runner.py` | 12 | TODO15d 及其集成回归 |

## 逐子题矩阵

| 子题范围 | 答案 | 课程初始态 | 红灯语义 |
|---|---|---|---|
| 8a—9d | 全绿 | 各自失败 | 对应 Store 读取/写入/恢复 TODO |
| 10a | 绿 | `DID NOT RAISE` | Trace 时间线不变量尚未实现 |
| 10b—10e | 全绿 | 各自 `NotImplementedError` | 迭代/load/append/replay |
| 11a—11c | 全绿 | 精确负向断言失败 | 跨字段与唯一身份尚未实现 |
| 11d—11e | 全绿 | 各自 `NotImplementedError` | 对齐与公式已经拆开 |
| 13a—13d | 全绿 | 各自 `NotImplementedError` | Provider 四个协议层次 |
| 14a—14d | 全绿 | 各自 `NotImplementedError` | Runner/Scorer/Aggregate/SQLite |
| 15a—15d | 全绿 | 各自 `NotImplementedError` | Harness/Experiment/Ablation/Formal |

14b 的测试使用直接 Runtime fixture，不依赖 14a；15b 和 15d 使用已完成前置开关替身，
不会被 15a 遮挡。因此每个新子题都能单独给出准确反馈。

## 工作区边界

课程分支从 `main@14c0186` 创建于独立 worktree。主工作树中人的
`src/kaiji/adapters/azusa.py` 与 `src/kaiji/azusa_tools.py` 未提交修改没有被复制、覆盖、
暂存或删除。课程提交不应直接强推到 main；合并前必须先处理这两个文件的重叠差异。
