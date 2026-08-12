# Kaiji Reliability Eval v0.2 课程任务

本课程在 TODO1—TODO12 之后继续，但不要求把同一功能机械重写三遍。L1 提供详细协议
与函数签名，训练边界实现；L2 提供数据模型与黑盒契约，训练职责组织；L3 只给完整需求、
产物和验收边界，训练一次可交付的实验工程。答案实现已经独立冻结，练习分支只保留
脚手架和预期红灯。

## 共同边界

- 只做单 Agent Runtime、工具调用、Eval、故障注入、SQLite 恢复与实验报告。
- 不做 Multi-Agent、Subagent、MCP、复杂 Memory、LLM-as-a-Judge、FastAPI、Docker、
  服务压测、Kubernetes 或分布式 Runtime。
- 默认测试不得访问模型 API。所有 Provider 测试使用 `httpx.MockTransport`。
- API Key 只能在显式真实实验时从调用方指定的环境变量读取；不得写入源码、fixture、
  Trace、Manifest 或失败文本。
- Scripted/Mock 成绩不得表述为真实模型成绩；三条 BM25 fixture 不得表述为开放域
  Benchmark 或生产表现。

## L1：TODO13 Provider 协议边界

文件：`src/kaiji/providers/openai_compatible.py`

已提供 Config、Pydantic 响应外壳、HTTP Transport 注入点、Registry 和完整直接测试。
人只实现函数内部逻辑，不需要设计新 Provider 抽象。

### 13a 请求载荷

- 把可选 System Prompt、完整 Message 历史和全部 Tool Schema 放入请求；
- 固定 `stream=false`、`tool_choice=auto`、模型、温度；
- `thinking` 与 `max_tokens` 只在配置非空时发送；
- 不得把 API Key 或环境变量名放入 JSON。

```powershell
python -m pytest tests/test_openai_compatible_provider.py -k todo_13a
```

### 13b 消息转换与调用 ID 配对

- user 和普通 assistant 保留文本；
- assistant tool call 保留 `call_id`、工具名、稳定 JSON 参数与可选文本；
- tool 结果必须使用同一个 `tool_call_id`；
- 每个 assistant 回合只表达一个工具调用。

```powershell
python -m pytest tests/test_openai_compatible_provider.py -k todo_13b
```

### 13c 响应解析

- 必须恰好一个 assistant choice；
- 工具路径只能有一个 `function` call，参数必须是 JSON 对象；
- `finish_reason` 必须与工具或最终回答路径一致；
- 非工具路径必须包含非空最终文本。

```powershell
python -m pytest tests/test_openai_compatible_provider.py -k todo_13c
```

### 13d 请求、元数据与失败分类

- 不做隐藏重试，每次 `decide()` 最多发送一次请求；
- timeout、transport、429、5xx、其他 4xx、非法 JSON、非法协议分别结构化；
- timeout、transport、429、5xx 可重试，其余不可重试；
- 成功时保存模型、延迟、请求 ID 和 Token；失败不得泄露请求/响应正文或鉴权头。

```powershell
python -m pytest tests/test_openai_compatible_provider.py -k todo_13d
python -m pytest tests/test_openai_compatible_provider.py
```

## L2：TODO14 Runner、Scorer 与 SQLite

L2 已提供公开模型、工厂类型、受控 Case、黑盒样例和追踪表。人可以增加私有辅助函数，
但不得改变公开字段含义或用 Runner 自报的成功替代确定性评分。

### 14a 隔离 Runtime Episode

- `repeat_index` 只能是真正的非负 `int`；
- 每个 case 分别调用 Provider/Executor 工厂，故障状态不得跨 case 泄漏；
- 保存唯一 run ID、墙钟延迟和未经裁剪的 `RunResult`；
- `max_steps` 与 duplicate guard 来自 case/Harness 契约。

```powershell
python -m pytest tests/test_eval_runner.py -k todo_14a
python -m pytest tests/test_eval_runner.py
```

### 14b 单条确定性评分

- 工具选择取第一次模型工具动作；参数使用 JSON 子集语义；
- 最终回答必须包含所有要求事实；
- 引用只在“实际引用 ∩ 允许 ID ∩ 成功证据 ID”中受支持；metadata 同名字段不算证据；
- stop、工具、参数、事实、引用、恢复均进入 `task_success` 和失败分类；
- Token 未报告时按零保存，不能丢掉该 Episode。

```powershell
python -m pytest tests/test_eval_scorer.py -k todo_14b
```

### 14c 固定分母聚合

- case ID、run ID 唯一；Episode 必须属于已知 case 且 suite 一致；
- 每个 case 至少有一条 Episode，缺失不能缩小分母；
- 工具、无工具、重试只使用各自适用 Episode；
- Citation Precision 分母是全部实际引用，Recall 分母是全部允许引用；
- P50/P95 使用 nearest-rank，并聚合 Steps、Token、延迟、停止与失败分布。

```powershell
python -m pytest tests/test_eval_scorer.py -k todo_14c
python -m pytest tests/test_eval_scorer.py
```

### 14d 官方 SQLite Checkpointer

- 创建父目录并通过 `SqliteSaver.from_conn_string()` 打开官方同步 Saver；
- context 退出后必须关闭连接；不得使用全局连接；
- 第一个 Graph/连接关闭后，同一 `thread_id` 能由新 Graph/连接恢复；
- 审批只接受真正的 Python bool，不同线程不得泄漏状态；旧 Schema 必须显式失败。

```powershell
python -m pytest tests/test_sqlite_checkpoint_resume.py -k todo_14d
python -m pytest tests/test_sqlite_checkpoint_resume.py
```

## L3：TODO15 Reliability Experiment

L3 不规定内部类和辅助函数。已提供 60 条受控数据、故障 fixture、CLI 外壳和报告模型；
人负责把它们组织成可恢复、可复现、可审计的完整实验。

### 15a 受控 Harness

四个 Harness 只能切换一次有界 retry 与 duplicate guard：

| Harness | Retry | Duplicate guard | max_steps |
|---|---:|---:|---:|
| `bounded_baseline` | 关 | 关 | 保留 |
| `no_retry` | 关 | 开 | 保留 |
| `no_duplicate_guard` | 开 | 关 | 保留 |
| `full_kaiji` | 开 | 开 | 保留 |

```powershell
python -m pytest tests/test_harnesses.py -k todo_15a
python -m pytest tests/test_harnesses.py
```

### 15b 实验矩阵与产物

- 执行 `case × repeat` 完整矩阵，每次运行 ID 唯一；
- 冻结数据与配置 hash，记录模型、温度、Harness、Suite、Token、成本和延迟；
- 写 `episodes.jsonl`、`report.json`、`report.md`、`bad_cases.json`；
- 已有实验目录不得覆盖；部分矩阵不得标为 complete 或生成聚合成绩。

```powershell
python -m pytest tests/test_experiment.py -k todo_15b
```

### 15c Harness Ablation

- 四组实验使用相同数据、模型动作、温度、最大步骤和重复次数；
- 数据 hash 必须一致；
- JSON/Markdown 必须比较成功率、恢复率、Steps 与延迟；
- 分析 Retry 改善了什么、Duplicate Guard 避免了什么，以及正常路径开销。

```powershell
python -m pytest tests/test_experiment.py -k todo_15c
```

### 15d 可恢复 Formal

- 每条 Episode 立即追加到 hash 链 JSONL，并 flush、fsync；
- `(case_id, repeat_index)` 是唯一矩阵坐标，恢复只能跳过，不能重复消费；
- 每次真实请求先记 started，响应后记 Token；未决请求必须阻断自动恢复；
- 数据、配置、hash 链、坐标、run ID 变化均拒绝恢复；
- 中断与预算停止写 incomplete 报告，完整矩阵才允许 Aggregate；
- 离线重评分默认只读，显式迁移前先保存旧报告。

```powershell
python -m pytest tests/test_formal_runner.py -k todo_15d
python -m pytest tests/test_formal_runner.py
```

最后才运行：

```powershell
python -m pytest tests/test_experiment.py tests/test_harnesses.py tests/test_formal_runner.py
python -m kaiji.experiment --provider scripted --suite smoke
python -m kaiji.experiment --provider scripted --suite reliability
```

真实 DeepSeek Formal 与 Azusa Live 不属于普通练习回归。它们需要人在完成全部离线题后，
另行明确凭据、请求、费用和数据发送授权。

## 预期红灯与完成定义

初始练习分支中，13a—13d、14a—14d、15a—15d 的窄命令应分别失败，并精确指向同名
`NotImplementedError`。完成当前子题后，其窄测试应变绿；后续子题仍可保持精确红灯。
只有测试、本文契约、状态不变量和代码审查同时通过，才可宣告该子题完成。
