# Kaiji

Kaiji 是面向可靠工具调用的 Python 单 Agent Runtime 实验项目。它显式管理工具输入与输出校验、有界重试、循环停止、状态恢复、Trace 与可复核评测，通过 HTTP Adapter 使用 [Azusa](https://github.com/Gianna-Lynn/Azusa) 的检索与证据能力。

默认分支 `main` 提供可复现的稳定演示；`develop` 用于学习开发；`solution` 保存参考实现与扩展能力。本地与 GitHub 远端均已统一为这三个分支。

## 最小体验

需要 Git 和 Python 3.11+。在新目录克隆 main，并创建独立虚拟环境。以下命令适用于 Windows PowerShell：

```powershell
git clone --branch main https://github.com/Gianna-Lynn/Kaiji.git
cd Kaiji
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m kaiji.demo
.\.venv\Scripts\python.exe -m kaiji.course_demo
.\.venv\Scripts\python.exe -m kaiji.eval_demo
```

Linux/macOS 命令及共享环境的来源隔离见 [Demo 指南](docs/demo-guide.md)。这里使用独立环境的 editable 安装，让评测入口读取仓库内的 `eval/` 和公开样例；不要把共享 ChuoEnv 重新绑定到其他 worktree。

折扣 Demo 应返回 `The final price is 90.`、`completed`、2 步和 1 次工具执行。工具真实执行，动作与答案由 Scripted Provider 预设；无需模型密钥、GPU 或 Azusa 服务，不能作为模型自主成绩。

## 展示范围

| 能力 | 已有路径 | 结论边界 |
|---|---|---|
| 原生工具循环 | Registry、Executor、Runtime、Message 与 Trace | 一次 run 有界执行，不自动恢复持久化快照 |
| 框架对照 | LangChain Tool/Runnable、LangGraph State/Interrupt | 框架不替代工具权限、结果校验和业务失败策略 |
| 暂停与恢复 | 严格 bool 审批、SQLite 新连接恢复 | 只模拟副作用；JSON Store 与图 Checkpointer 独立 |
| Azusa 闭环 | BM25 search→实际 ID get→正文校验→引用 | 项目笔记、规则决策和真实本机 HTTP，不是自主模型检索成绩 |
| 可靠性实验 | Episode、Scorer、Harness、Formal、公开复核包 | 固定数据与故障，实验矩阵恢复不等于业务 exactly-once |

main 保留已实现的课程能力；源码中的编号 TODO 用于定位教学契约，不表示留有未实现占位。FastAPI 服务、span 投影、Docker/CI 与 Chat/Web 属于 solution 扩展，不列入 main 交付主张。

## 分支导航

| 分支 | 用途 |
|---|---|
| [main](https://github.com/Gianna-Lynn/Kaiji/tree/main) | 稳定展示、运行指南与证据索引 |
| [develop](https://github.com/Gianna-Lynn/Kaiji/tree/develop) | 学习开发、后续实现与在途工作 |
| [solution](https://github.com/Gianna-Lynn/Kaiji/tree/solution) | 参考实现、服务化与聊天扩展 |

## 阅读与复核

- [架构与职责](docs/architecture.md)、[框架对照](docs/framework-comparison.md)。
- [Demo 指南](docs/demo-guide.md)、[证据索引与失败边界](docs/evidence-index.md)。
- [展示验收结果](docs/migration-acceptance.md)、[仓库展示复核与剩余事项](docs/showcase-review.md)。
- 学习导航：[LEARNING.md](LEARNING.md)、[课程路线](docs/curriculum.md)。日常学习在 develop；不要把答案整体覆盖到人的实现。

历史模型数字和验证报告只归属于各自提交、数据与环境。`eval_demo` 是合成观测聚合；hash 链不证明来源真实性；线程超时不终止已开始的 handler。完整教学独立审计、真实模型复验与生产部署不由本轮展示验收替代。

Kaiji（かいじ，甲斐路号）与 Azusa（あずさ，梓号）的命名呼应。

仓库目前尚未声明代码许可证；公开可读不等于已授予复用许可。公开合成样例的 CC0 声明仅适用于对应数据。
