# DeepSeek API 故障消融

四组各运行同一批 20 条用例一次，保持工具、故障序列、用例步数上限及评分规则一致。每组只切换工具重试和重复调用保护。临时故障及模拟 Azusa 响应均在本地注入。

用例来源为 [fault_cases.jsonl](../fault_cases.jsonl)，SHA-256 为 `23a83e0851508e78d21f9b58345ba37c73460c406edcb2bc74e5d089d0346bf3`。

模型为 `deepseek-flash`，关闭思考模式，每次最多输出 128 tokens。四组均使用相同的用例级系统提示，引导模型触发故障并自主生成工具动作和回答。

## 四组结果

| 配置 | 工具重试 | 重复调用保护 | 严格通过 | 重试恢复 | 平均步数 | API 请求 |
|---|---|---|---:|---:|---:|---:|
| `bounded_baseline` | 关 | 关 | 12/20 (60%) | 0/4 | 1.50 | 30 |
| `no_retry` | 关 | 开 | 14/20 (70%) | 0/4 | 1.40 | 28 |
| `no_duplicate_guard` | 开 | 关 | 16/20 (80%) | 4/4 | 1.70 | 34 |
| `full_kaiji` | 开 | 开 | 18/20 (90%) | 4/4 | 1.60 | 32 |

整张矩阵使用 124 次 API 请求，83,098 输入和 5,994 输出 tokens，按峰时无缓存价估算 $0.032122。本次新增 92 次请求，估算 $0.023975。 [DeepSeek 官方定价](https://api-docs.deepseek.com/quick_start/pricing/)

完整组复用同日已运行的记录，逐条核对了原用例、系统提示、模型和评分。

## 机制影响

- 只开工具重试，较基线新增通过 `fault-transient-01`、`fault-http-429-01`、`fault-http-503-01`、`fault-transient-a7`。
- 只开重复调用保护，较基线新增通过 `fault-duplicate-01`、`fault-duplicate-z9`。
- 完整组的 `fault-empty-01` 未通过。实际回答为「未找到与 "Kaiji" 相关的证据：检索返回 0 条结果（hits 为空），因此没有可引用的内容，也不列出 Citations。」。
- 完整组的 `fault-empty-a7` 未通过。实际回答为「未找到证据：search_azusa 对查询 "Kaiji" 返回 0 条命中（hits 为空），因此没有可引用的 chunk_id，标识 a7 的引用无法成立，属于空证据伪引用。 Citations: 无（检索结果为空）」。

空证据用例的原评分要求答案包含「使用工具」，且至少给出一条有成功工具证据支持的引用。搜索返回空结果时，这项引用要求无法达成。表格保留原严格评分。

## 逐条结果

每格列出实际停止原因及严格评分。未通过项括号内列出扣分字段。

| 用例 | 预期停止 | 基线 | 只开重复保护 | 只开重试 | 完整配置 |
|---|---|---|---|---|---|
| `fault-transient-01` | `completed` | `tool_failed` （stop、answer_facts、retry_recovery） | `tool_failed` （stop、answer_facts、retry_recovery） | `completed` 通过 | `completed` 通过 |
| `fault-persistent-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-permanent-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-timeout-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-empty-01` | `completed` | `completed` （answer_facts、citation） | `completed` （answer_facts、citation） | `completed` （answer_facts、citation） | `completed` （answer_facts、citation） |
| `fault-http-429-01` | `completed` | `tool_failed` （stop、answer_facts、citation、retry_recovery） | `tool_failed` （stop、answer_facts、citation、retry_recovery） | `completed` 通过 | `completed` 通过 |
| `fault-http-503-01` | `completed` | `tool_failed` （stop、answer_facts、citation、retry_recovery） | `tool_failed` （stop、answer_facts、citation、retry_recovery） | `completed` 通过 | `completed` 通过 |
| `fault-network-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-invalid-json-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-schema-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-chunk-mismatch-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-duplicate-01` | `duplicate_call` | `max_steps` （stop） | `duplicate_call` 通过 | `max_steps` （stop） | `duplicate_call` 通过 |
| `fault-max-01` | `max_steps` | `max_steps` 通过 | `max_steps` 通过 | `max_steps` 通过 | `max_steps` 通过 |
| `fault-invalid-args-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-failed-evidence-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-transient-a7` | `completed` | `tool_failed` （stop、answer_facts、retry_recovery） | `tool_failed` （stop、answer_facts、retry_recovery） | `completed` 通过 | `completed` 通过 |
| `fault-persistent-z9` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-empty-a7` | `completed` | `completed` （answer_facts、citation） | `completed` （answer_facts、citation） | `completed` （answer_facts、citation） | `completed` （answer_facts、citation） |
| `fault-duplicate-z9` | `duplicate_call` | `max_steps` （stop） | `duplicate_call` 通过 | `max_steps` （stop） | `duplicate_call` 通过 |
| `fault-max-k2` | `max_steps` | `max_steps` 通过 | `max_steps` 通过 | `max_steps` 通过 | `max_steps` 通过 |

## 复现

在仓库根目录运行 `python eval/fault_ablation/run.py deepseek --output-dir .cache/fault-ablation-repro-deepseek`。运行前在进程环境中配置 `DEEPSEEK_API_KEY`。

逐条原始记录见 [deepseek.jsonl](deepseek.jsonl)，包含用例、模型动作、工具结果、停止原因、评分和 Token 计数。

在仓库根目录运行 `python eval/fault_ablation/run.py deepseek --output-dir eval/fault_ablation --report-only` 可从本目录原始记录重建报告。
