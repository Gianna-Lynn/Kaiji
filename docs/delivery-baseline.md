> 历史记录：本文分支名、提交和测试数字属于记录当时。2026-11-13 本地已调整为 main/develop/solution，当前职责与验收见 [迁移说明](branch-migration.md)。

# Kaiji P0-C 交付基线与证据范围

记录日期：2026-10-30。本轮只做交付版本与证据收口，不宣告课程全部验收。

## 版本选择

交付核心实现固定为 `portfolio/kaiji-v0` 的
`54bdd1d83d0e678f7b55751555ea633819d9d5c8`。分支名会移动，复核必须同时看提交与源码散列。
P0-C 在该基线上增加离线核验脚本、澄清注释和本文档，作为独立阶段提交保留。
阶段提交包含验证结果；以各分支的 Git 历史定位该补丁，功能基底仍为上述固定提交。
核验报告的 `base_commit` 是基底，不代表工作区干净；`files_sha256` 标识实际受测源码、
题集、项目配置和核验脚本，不把未提交补丁冒充原始提交。

| 工作树 | 本轮核对的 HEAD | 用途与交付状态 |
|---|---|---|
| `main` | `2ebde6e7a42a3b37ad58d418e8d201f23d841396` | 人的学习与开发主线；另行验证，不合并答案 |
| `solution/kaiji-v0` | `091f775d067227ece396dad19499d3bdbbe3e2d5` | 参考实现加本地聊天/Web 开发；16 个原有变动文件不纳入本轮交付 |
| `portfolio/kaiji-v0` | `54bdd1d83d0e678f7b55751555ea633819d9d5c8` | 本次展示交付基底，包含服务与观测边界 |

不把 solution 当前未提交的聊天/Web 能力列为已交付，不自动合并、推送或打发布标签。

## 能力与恢复范围

| 能力 | 已实现边界 | 本轮证据 | 不能据此推出 |
|---|---|---|---|
| Native Runtime | 一次 `run(question)` 的有界工具循环、失败与成功证据隔离 | 离线折扣 Demo；Runtime/Executor/Runner/Scorer 回归 | 任意模型回合自动持久化恢复 |
| LangGraph/SQLite | Interrupt 后按 thread 恢复，严格 bool 审批、版本与线程隔离 | 关闭第一连接、以新 Saver/Graph 读取并批准；SQLite 直接测试 | 分布式一致性或真实发送/发布副作用已执行 |
| JSON Checkpoint | 显式快照存取 | 持久化直接测试 | Native Runtime 自动接入快照恢复 |
| Formal | 按 `(case_id, repeat_index)` 恢复实验矩阵；核对数据、配置、hash 链和账本 | Formal 恢复/拒绝恢复直接测试；120+60 条 Scripted Preflight | 恢复一个中断的模型请求、业务 exactly-once 或签名认证 |
| HTTP 服务 | 仅展示分支：Bearer、服务端工具权限收缩、每请求新 Runtime | portfolio 离线服务测试 | 本轮重建镜像或验证远端 CI |

线程超时只结束等待，不终止已开始的 handler。重试可能与旧 handler 重叠；当前 Azusa 工具
只读，这不能直接推广到有副作用的工具。LangGraph 副作用路由只是审批模拟。
Formal 出现未决请求时停止人工核账，不把未知成本当零继续；partial 报告没有正式 aggregate。

## 证据分类

1. **当前离线实现回归**：main 607 项、portfolio 588 项，均退出码 0；显式排除
   `tests/test_azusa_live_integration.py`。固定各自 `src` 导入，独立临时目录，不使用 editable
   install 的隐式来源。禁用缓存插件导致一条 `cache_dir` 配置警告；portfolio 另有
   Starlette/httpx 弃用警告，不属于断言失败。
2. **当前离线小闭环**：两个工作树均通过 Native Demo、新连接 SQLite 恢复和 Formal
   Preflight。Preflight 为 Scripted 行为 120 条、故障 60 条，真实请求为零；不能称为模型成绩。
3. **历史模型行为**：2026-08-12 的 Behavior Formal 使用统一提示、五工具和受控 Mock
   fixture，严格成功 `116/120`；工具选择 `96/96`、参数 `92/96`、无工具 `24/24`。
   `120/120` 表示矩阵完成，不是严格任务全成功。原始报告保留当时延迟、Token 和费用口径。
4. **有引导的 Azusa Live 集成**：历史 5 条模型 Live 的 `_system_prompt()` 直接读取
   `expected_arguments` 和 `allowed_citation_ids`，给出工具、query、k、mode 与目标引用。
   它验证真实模型协议、HTTP Adapter、结果回填和引用归属，不能证明自主选择或检索质量。
   历史 3 Search + 3 Get 是另外的网关 fixture 联调，不与模型 5 条合并计分。
5. **历史产物的当前离线复核**：在两个工作树用当前 Scorer 只读重算同一历史 Formal，
   校验数据指纹、矩阵、hash 链、225 次请求账本；严格成功率仍为 `116/120`。
   整个历史目录前后逐文件 SHA256 相同。它证明产物在当前规则下自洽，不证明来源真实性，
   也不是在当前模型/API 上重新实验。

引用 Precision/Recall 在分母为零时为 `null`；工具选择、参数、无工具和重试指标仍使用各自
适用分母及现有零分母规则，不能用其他样本稀释。历史数字不自动归属于本轮源码。

## 复现入口与本机产物

脚本提供于 main 和 portfolio；solution 中本文仅同步交付边界，不接入其聊天/Web 工作。
在**所选工作树根目录**执行，Python 3.11+ 且已安装项目依赖：

```powershell
python -B -s scripts/verify_delivery.py
```

脚本固定当前 `src`，记录导入位置、基底提交、Python/关键依赖版本和文件 SHA256。只用预设模型与 Mock 工具，
每次写入新的 `artifacts/delivery/<uuid>/verification.json`，不覆盖旧结果。
可选 `--historical-run <已有完整行为Formal目录>` 只读重算，不读取 API Key、不发送请求。
原始历史目录需自行提供，不包含在 Git clean clone 中；默认命令不依赖它。

完整离线回归还需单独运行；使用当前工作树的 Python 导入路径和唯一 pytest 目录：

```powershell
$previousPythonPath = $env:PYTHONPATH
$testRoot = '.cache/codex-pytest-' + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    $env:PYTHONPATH = (Join-Path (Get-Location) 'src') # 仅当前进程
    python -m pytest tests --ignore=tests/test_azusa_live_integration.py -o addopts= -q --basetemp=$testRoot -o "cache_dir=$testRoot/cache"
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
```

运行后恢复原先的进程级 `PYTHONPATH`；不修改用户级环境配置。本轮实际通过 `sys.path.insert`
在独立 Python 子进程固定来源，未设置持久环境变量。

本机可检查报告（均被 `.gitignore` 排除，未经公开发布）：

- main：`artifacts/delivery/6f87127dae954960b825d387a6a68ef7/verification.json`
- portfolio：`artifacts/delivery/60dec0d6479e49e19622d43bba89e5b5/verification.json`

本轮最初 portfolio pytest 因 `.cache` 父目录不存在而启动失败；创建项目内目录后换唯一
basetemp 重跑通过，未更改 ACL。旧启动失败不计为业务测试结论。

## 未在本轮复验

真实模型/API、Azusa 实时联调、Docker 重建、远端 CI、开放域检索指标和完整教学独立审计。
旧报告、旧课程红灯快照、当前测试结果分别保留日期与来源，不互相替代。
