# P1 最小集成与公开复核材料

日期：2026-10-30。P0-C 阶段提交为 main `d88a632`、展示分支 `d632729`、
答案分支说明同步 `cc22e21`。本轮 P1 补丁位于 main 和 portfolio，不包含 solution
已有聊天/Web 工作，也不改变 P0-C 固定核心基底的历史归属。

## 一条真实 HTTP 路径

在所选 Kaiji 工作树根目录运行，使用已安装依赖的 Python：

```powershell
python -B -s scripts/verify_azusa.py --azusa-root F:/Repositories/Azusa
```

入口自动启动一个独立 Azusa 进程，仅监听 `127.0.0.1`，使用系统分配端口；启动等待有
120 秒上限，正常或异常结束都会终止本次子进程，不占用用户现有服务。通过 Adapter 的
HTTP 接口完成：

`项目问题 → BM25 搜索 → 取实际命中的 ID → get 回读 → 正文一致校验 → 摘录与引用`

示例问题为“How does Kaiji resume a workflow with SQLite?”，证据来自仓库自写的
`examples/knowledge_notes.jsonl`。该笔记说明新 Saver、相同 thread_id 和 Native
Runtime 不自动恢复的边界。零向量仅兼容 EmbeddingRecord 外壳，BM25 不使用它们。
本轮没有编码模型或真实 LLM 请求；`EvidenceDecisionProvider` 是固定三步规则，不能
称为自主 Agent 成绩。Provider 不读取 case 金标或预设引用 ID；最终答案只是证据摘录。

第二个 Runtime 故意读取未知 chunk，确认 `tool_failed` 且成功证据为空。每次运行保存
唯一的 `artifacts/integration/<uuid>/integration.json`、网关日志，报告包含双方基底提交、
实际源码/fixture SHA256、完整成功与失败 Trace，并在结束前核对源码未变化。

最终本机通过报告（分别相对于各工作树）：

- main：`artifacts/integration/930e960bcfec49549d0b34b9d29afead/integration.json`
- portfolio：`artifacts/integration/8d3a9136215241b687128d942b2579ab/integration.json`

这与旧的 5 条金标 Guided Live 分开，不追加到历史模型分母中。

## 可携带的离线证据包

`examples/public_evidence_v1/` 是约 30 KB 的**新造合成契约样例**，不从历史模型回答、
个人数据、密钥或账单截取内容。样例数据声明为 CC0-1.0；这不是对整个仓库代码改许可。
文件按 LF 固定，并由 `.gitattributes` 防止跨平台 checkout 改换行后损坏包摘要。

包中包含已冻结 `cases.jsonl`、完整和 partial 两套 Manifest、请求账本、hash 链 Episode、
报告和 Bad Case，以及包内逐文件 SHA256。生成代码为 `kaiji.public_evidence`，记录
生成基底与当时源码散列；生成时的基底不表示当时工作区干净。无密钥 hash 不认证作者或来源。

|样例|预期复核结果|体现的边界|
|---|---|---|
|public-success|折扣结果 90，严格通过|真实 Runtime→Executor→Episode→Scorer 路径|
|public-arguments|结果 90，但参数判失败|字符串 `120.0` 与冻结金标 `120` 不相同，不事后放宽|
|public-timeout|两次有界尝试后 tool_failed|无成功证据；线程超时不代表 handler 被取消|
|partial / resume|初始 1/3、aggregate=null，恢复只执行后两条|第一条 run_id 不变，矩阵完成后才聚合|

在当前工作树的单独 Python 进程中复核，不依赖 editable install 或 API Key：

```powershell
python -B -s -c 'import sys; sys.path.insert(0,"src"); from kaiji.public_evidence import main; main()'
```

命令只读原包，在新的 `artifacts/public-replay/<uuid>/` 复制 partial 再续跑，校验：原包
文件字节不变、完整矩阵当前重评分、参数失败单独分类、超时尝试及证据隔离、已完成坐标
跳过。失败返回非零。原始包完整性失败时不会创建续跑目录。

最终本机复核报告（分别相对于各工作树）：

- main：`artifacts/public-replay/305fecaf475e4e8b985594ab6a986ce1/replay.json`
- portfolio：`artifacts/public-replay/15d625f797094de7af815d12385d78a6/replay.json`

已包含恢复后结果的逐项判定。两个分支的独立 CLI 入口均通过。
重新生成必须显式使用 `--generate <不存在的新目录>`，不原地改写冻结包。

## 验证范围

新增四项直接测试覆盖只读复核、包损坏拒绝、实际返回 ID 的引用、search/get 正文不一致
拒绝回答。固定各自 `src`、唯一 `--basetemp` 和独立 cache 后，全量离线回归为
main **611 passed**、portfolio **592 passed**（后者一条既有 Starlette/httpx 弃用警告）；
显式排除 `test_azusa_live_integration.py`，其旧 fixture 不与本轮项目笔记混用。
本轮的真实网关验证由上述独立进程入口完成。P0-C 的 607/588 仍只属于当时提交。

Azusa 的 P1 同时完成增量 KB 和小规模 Flat/HNSW 实验，见其 `docs/p1-validation.md`。
本轮未调用真实模型，未发布站点、推送仓库、重建 Docker 或合并聊天/Web；教学课程整体
独立审计也没有由这些运行验证替代。

## 注释检查与阶段提交

已检查新增函数与类的中文说明，补充规则 Provider 的消息前提和失败映射、Adapter
生命周期、网关子进程清理、复核包的输入输出和失败边界。main 与 portfolio 同名代码
注释同步；本轮共核对两个项目 16 份 Python 文件，去除 docstring 后 AST 均与补注释前一致。
补注释后离线回归仍为 main **611 passed**、portfolio **592 passed**，实时旧 fixture
测试仍显式排除。公开包由上述回归重新执行，只读原件后在唯一临时目录恢复副本。
旧 HTTP 运行报告和冻结样例保留原始字节及生成源码散列，不把补注释后的散列回填到旧产物；
本轮没有新增真实模型或性能测量结论。P1 作为阶段提交保留，solution 原有改动不纳入提交。
