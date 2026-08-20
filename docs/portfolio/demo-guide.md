# Kaiji 命令行 Demo 指南

本指南用命令行展示可靠 Agent 的关键边界，不依赖 GIF、录屏或桌面自动化。默认路径全部
离线；真实模型与 Azusa Live 必须单独授权、配置预算并明确开启。

## 环境

- Python 3.11+
- 从 `portfolio/kaiji-v0` 分支根目录执行
- 多 worktree 共用环境时显式设置 `PYTHONPATH`，避免导入另一个分支的 editable install

```powershell
python -m pip install -e '.[dev]'
$env:PYTHONPATH = (Resolve-Path 'src').Path
python -c "import kaiji; print(kaiji.__file__)"
```

打印路径应位于当前 `kaiji-portfolio/src`。结束后可执行：

```powershell
Remove-Item Env:PYTHONPATH
```

## 五分钟推荐演示

### 1. 原生 Runtime 小闭环

```powershell
python -m kaiji.demo
```

讲解重点：

- 模型只产生类型化 `ModelAction`，实际工具执行权在 `ToolExecutor`；
- 参数在 handler 前校验，工具返回值在进入证据前再次校验；
- Runtime 通过 `max_steps`、重复签名和明确 `StopReason` 保证循环有界；
- 失败 Tool Message 可以被审计，但不会进入成功证据集合。

### 2. 固定 Eval 聚合

```powershell
python -m kaiji.eval_demo
```

讲解重点：

- `EvalCase` 是金标契约，`EvalObservation` 是观测事实；
- 聚合前按唯一 case ID 对齐，不允许遗漏、多余或重复；
- Task Success、Tool、Retry、Citation 和延迟使用不同口径。

### 3. Scripted Provider/Scorer 小闭环

```powershell
python -m kaiji.experiment --provider scripted --suite smoke
```

讲解重点：

- 这是 Runner、Scorer 和报告管线的机制演示，不是模型能力成绩；
- 每个 Case 使用隔离的 Runtime/Provider/Tool 状态；
- 报告由真实 `RunResult`/`AgentEpisode` 计算，不从金标复制满分。

### 4. Reliability Ablation

```powershell
python -m kaiji.experiment `
  --provider scripted `
  --suite reliability `
  --ablation
```

讲解重点：

- 对照只切换一次工具 retry 和 duplicate guard；
- 四种 Harness 都保留 Schema、最大步骤和同一故障集；
- 结果只说明固定 Scripted 故障集，不外推真实模型。

### 5. Formal Preflight

```powershell
python -m kaiji.formal_runner --preflight
```

讲解重点：

- 数据、Prompt、工具 Schema 和配置会冻结进 Manifest；
- 每个 Episode 逐条追加并形成 hash 链；
- 固定矩阵不完整时只生成 partial report，不聚合正式成绩；
- Scripted Preflight 不发送模型请求，请求账本固定为零。

## 面试演示顺序

1. 先用 README 架构图解释为什么项目只做单 Agent。
2. 打开 `runtime.py` 展示有界循环和停止原因。
3. 打开 `tools.py` 展示执行前后两次 Schema 门禁。
4. 运行离线 Demo，指出 Message、Trace、证据三者职责不同。
5. 打开 Eval 报告，主动讲一个无效实验和一个冷态超时 Bad Case。
6. 最后说明 P2 缺口：服务接口、CI、容器、生产权限与 Prompt Injection 防护。

## 真实 Provider 路径

真实调用不是默认 Demo。执行前必须确认：

- 临时 API Key 只存在于明确指定的环境变量；
- 请求硬上限、费用软上限和输出 Token 上限；
- 使用哪组公开 fixture；
- 失败产物也要保留；
- 结果只在该 Prompt、模型、工具和数据范围内成立。

示例形状如下，变量名必须由操作者明确选择：

```powershell
python -m kaiji.real_smoke `
  --api-key-env '<TEMP_KEY_ENV>' `
  --request-limit 19 `
  --output-dir artifacts/real-smoke
```

本指南不要求、也不建议为了招聘演示重新消耗真实模型额度。优先展示已经冻结的报告、
机制代码和 Bad Case 判断。

## Azusa Live 路径

Azusa 是独立仓库。Kaiji 只依赖稳定语义接口，不导入其内部 Python/Rust 实现。Live
演示需要先启动 Azusa BM25 fixture 网关，再设置 `AZUSA_GATEWAY_URL`。如果测试显示
`skipped`，只能说明未配置网关，不能声称 Live 已通过。

历史验证只覆盖三条公开 BM25 fixture。Dense/Hybrid 的冷启动、召回指标和大规模性能
不在当前成绩范围内。

## 演示结束检查

- 没有在终端或报告中输出 API Key；
- 没有把 Scripted/Mock 结果称为真实模型成功率；
- 没有把一次 Live 成功称为 SLA；
- 没有隐藏失败 Episode 或放宽金标；
- 没有把 Checkpoint 恢复称为外部副作用 exactly-once。

## P1 静态说明

本求职包装阶段没有执行上述命令。它们来自已存在的代码入口与历史验证文档；下一次代码
变更后的动态验证应单独记录，不能把旧审计结果自动继承给新实现。
