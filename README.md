# Kaiji

Kaiji 是面向可靠工具调用的 Python 单 Agent Runtime 实验项目。它显式管理工具输入与输出校验、有界重试、循环停止、状态恢复、Trace 与可复核评测，通过 HTTP Adapter 使用 [Azusa](https://github.com/Gianna-Lynn/Azusa) 的检索与证据能力。

`main` 展示人的已实现能力；`develop` 保留学习开发与在途改动；`solution` 保存参考实现和扩展能力。当前只完成本地迁移，远端尚未更新，旧远端分支不代表本地分工。

## 最小体验

在独立环境安装 Python 3.11+ 与本分支依赖后运行：

```powershell
python -m pip install '.[dev]'
python -m kaiji.demo
python -m kaiji.course_demo
python -m kaiji.eval_demo
```

已有共享 ChuoEnv 不要重新安装其他工作区；固定源码来源的命令见 [Demo 指南](docs/demo-guide.md)。折扣 Demo 应返回 `The final price is 90.`、`completed`、2 步和 1 次工具执行。工具真实执行，但动作与答案由 Scripted Provider 预设，不能作为模型自主成绩。

## 展示范围

| 能力 | 已有路径 | 结论边界 |
|---|---|---|
| 原生工具循环 | Registry、Executor、Runtime、Message 与 Trace | 一次 run 有界执行，不自动恢复持久化快照 |
| 框架对照 | LangChain Tool/Runnable、LangGraph State/Interrupt | 框架不替代工具权限、结果校验和业务失败策略 |
| 暂停与恢复 | 严格 bool 审批、SQLite 新连接恢复 | 只模拟副作用；JSON Store 与图 Checkpointer 独立 |
| Azusa 闭环 | BM25 search→实际 ID get→正文校验→引用 | 项目笔记、规则决策和真实本机 HTTP，不是自主模型检索成绩 |
| 可靠性实验 | Episode、Scorer、Harness、Formal、公开复核包 | 固定数据与故障，实验矩阵恢复不等于业务 exactly-once |

main 保留人的源码实现与已完成的课程能力；编号 TODO 是课程定位，不代表留有未实现占位。FastAPI 服务、span 投影、Docker/CI 与 Chat/Web 属于 solution 扩展，不列入 main 交付主张。

## 阅读与复核

- [架构与职责](docs/architecture.md)、[框架对照](docs/framework-comparison.md)。
- [Demo 指南](docs/demo-guide.md)、[证据索引与失败边界](docs/evidence-index.md)。
- [本地分支迁移与验收](docs/branch-migration.md)。
- 学习导航：[LEARNING.md](LEARNING.md)、[课程路线](docs/curriculum.md)。日常学习在 develop；不要把答案整体覆盖到人的实现。

历史模型数字和验证报告只归属于各自提交、数据与环境。`eval_demo` 是合成观测聚合；hash 链不证明来源真实性；线程超时不终止已开始的 handler。完整教学独立审计、真实模型复验与生产部署不由本轮展示验收替代。

Kaiji（かいじ，甲斐路号）与 Azusa（あずさ，梓号）的命名呼应。
