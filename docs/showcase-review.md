# main 展示复核与剩余事项

复核日期：2026-11-13。范围为首页、分支导航、当前与历史文档、安装入口、依赖元数据及现有离线回归；不代替完整源码安全审计或教学独立审计。

## 已修正

- 本地与远端已经统一为 main、develop、solution；删除“只完成本地迁移”“远端尚未更新”等当前状态说明。迁移前报告的数字、提交和数据来源继续保留为历史证据。
- README 提供克隆、独立虚拟环境与源码 editable 安装；Demo 指南区分新读者与共享 ChuoEnv 维护者，不再要求外部读者先有个人机器路径。
- 已复现普通 wheel 的 Eval Demo 因缺少仓库外层 eval/cases.jsonl 而失败。当前完整展示明确要求源码 checkout；源码安装后三个 Demo 均通过，不再把普通 wheel 安装描述为完整实验部署。
- Pydantic 下限由 2.7 修正为 2.12，因为 Executor 使用 model_validate 的 extra 参数。该参数的引入见 [Pydantic 2.12 发布记录](https://docs.pydantic.dev/latest/changelog/#v2120-2025-10-07)。未改动 Runtime 或评分逻辑。
- 更正包描述中的 framework-free、课程分支“仅有脚手架”等表述；LangChain 文档区分单工具 Message 模型与有损转换函数。
- solution 历史服务文档补充后续 CI 结果，保留历史本地实验的时点与原数字。

## 本轮验证

| 验证 | 结果与限制 |
|---|---|
| main 离线回归 | 611 passed |
| develop 离线回归 | 611 passed；人的原有未提交文件继续保留 |
| solution 离线回归 | 657 passed，1 条既有 Starlette 弃用警告 |
| 独立环境源码安装 | 新 venv 有独立 editable 记录，导入指向导出的 main 源码；第三方依赖复用 ChuoEnv，不代表从 PyPI 全新解析所有依赖 |
| 三个 Demo | 独立环境的 Native、框架对照与合成 Eval 均运行成功 |
| 文档链接 | 核对仓库内 Markdown 文件目标及当前分支链接；不宣称已视觉检查所有 GitHub 页面 |
| 远端结构 | main/develop/solution，默认分支 main |

本轮离线回归显式排除 live fixture；本轮没有重新调用模型或启动 Azusa。HTTP、SQLite、Formal 与公开包的上一轮完整运行证据见 [迁移验收](migration-acceptance.md)，不把旧结果冒充本轮重跑。

2026-11-13 远端 solution@66b50fa 的 [GitHub Actions](https://github.com/Gianna-Lynn/Kaiji/actions/runs/43450774878) 已成功完成 test 与 container 两个 job。该结论只属于该答案提交；main 没有这份工作流，不展示跨分支的绿色徽章。

## 仍需明确的事项

| 事项 | 对展示的影响 | 当前处理 |
|---|---|---|
| 代码许可证尚未选择 | 外部读者无法从仓库找到明确的代码许可文本 | README 如实说明，未代选许可证；数据样例 CC0 不扩展到代码 |
| main 尚无自动回归工作流 | 展示依赖本地验收记录，缺少本分支持续验证 | 保留为后续工程改进，不把 solution 的 CI 当成 main 的成绩 |
| wheel 不携带外层题集与样例 | 不能任意复制安装后运行所有实验 CLI | 当前支持源码 checkout；若要发布独立包，应另做资源打包与安装后回归 |
| 依赖范围未形成完整版本矩阵 | 不能承诺所有允许组合都已测试 | 报告实际环境，未改共享环境、未虚构最低版本全套测试 |
| TODO8—12 的新版教学独立审计未完成 | 不能用测试绿灯宣称课程全部完善 | 保留既有审计边界；已实现能力不重新挖空 |

在当前已声明的源码复现与单 Agent 演示范围内，复核未发现阻断三个首页 Demo 的剩余问题。上述范围之外的打包、许可证和自动化事项仍应分别处理。
