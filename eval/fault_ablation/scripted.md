# 离线 Scripted 故障消融

四组各运行同一批 20 条用例一次，保持工具、故障序列、用例步数上限及评分规则一致。每组只切换工具重试和重复调用保护。临时故障及模拟 Azusa 响应均在本地注入。

用例来源为 [fault_cases.jsonl](../fault_cases.jsonl)，SHA-256 为 `23a83e0851508e78d21f9b58345ba37c73460c406edcb2bc74e5d089d0346bf3`。

Provider 按脚本返回工具动作和回答，API 请求数为 0。

## 四组结果

| 配置 | 工具重试 | 重复调用保护 | 严格通过 | 重试恢复 | 平均步数 | API 请求 |
|---|---|---|---:|---:|---:|---:|
| `bounded_baseline` | 关 | 关 | 12/20 (60%) | 0/4 | 1.50 | 0 |
| `no_retry` | 关 | 开 | 14/20 (70%) | 0/4 | 1.40 | 0 |
| `no_duplicate_guard` | 开 | 关 | 16/20 (80%) | 4/4 | 1.70 | 0 |
| `full_kaiji` | 开 | 开 | 18/20 (90%) | 4/4 | 1.60 | 0 |

## 机制影响

- 只开工具重试，较基线新增通过 `fault-transient-01`、`fault-http-429-01`、`fault-http-503-01`、`fault-transient-a7`。
- 只开重复调用保护，较基线新增通过 `fault-duplicate-01`、`fault-duplicate-z9`。
- 完整组的 `fault-empty-01` 未通过。实际回答为「使用工具 Citations: kaiji-001」。
- 完整组的 `fault-empty-a7` 未通过。实际回答为「使用工具 Citations: kaiji-001」。

空证据用例的原评分要求答案包含「使用工具」，且至少给出一条有成功工具证据支持的引用。搜索返回空结果时，这项引用要求无法达成。表格保留原严格评分。

## 逐条结果

每格列出实际停止原因及严格评分。未通过项括号内列出扣分字段。

| 用例 | 预期停止 | 基线 | 只开重复保护 | 只开重试 | 完整配置 |
|---|---|---|---|---|---|
| `fault-transient-01` | `completed` | `tool_failed` （stop、answer_facts、retry_recovery） | `tool_failed` （stop、answer_facts、retry_recovery） | `completed` 通过 | `completed` 通过 |
| `fault-persistent-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-permanent-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-timeout-01` | `tool_failed` | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 | `tool_failed` 通过 |
| `fault-empty-01` | `completed` | `completed` （citation） | `completed` （citation） | `completed` （citation） | `completed` （citation） |
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
| `fault-empty-a7` | `completed` | `completed` （citation） | `completed` （citation） | `completed` （citation） | `completed` （citation） |
| `fault-duplicate-z9` | `duplicate_call` | `max_steps` （stop） | `duplicate_call` 通过 | `max_steps` （stop） | `duplicate_call` 通过 |
| `fault-max-k2` | `max_steps` | `max_steps` 通过 | `max_steps` 通过 | `max_steps` 通过 | `max_steps` 通过 |

## 复现

在仓库根目录运行 `python eval/fault_ablation/run.py scripted --output-dir .cache/fault-ablation-repro-scripted`。

逐条原始记录见 [scripted.jsonl](scripted.jsonl)，包含用例、模型动作、工具结果、停止原因、评分和 Token 计数。

在仓库根目录运行 `python eval/fault_ablation/run.py scripted --output-dir eval/fault_ablation --report-only` 可从本目录原始记录重建报告。
