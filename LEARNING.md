# Kaiji 原生 Agent v0：第一课

本课只学习原生 Tool Loop，不引入 LangChain 或 LangGraph。标准答案位于
`solution/native-agent-v0-tool-loop` 分支；请先在 `main` 独立完成，再对照答案。

## 环境

```powershell
$py = 'D:\Chuo_Data\conda_envs\ChuoEnv\python.exe'
& $py -m pytest
& $py -m kaiji.demo
```

不需要 API key，也不需要 Azusa 在线运行。`ScriptedDecisionProvider` 是本地的确定性
模型替身，因此失败只来自你的 Runtime 逻辑。

## 学习顺序

### TODO 1：Tool Registry

在 `src/kaiji/tools.py` 完成 `ToolRegistry.register`、`get` 和 `schemas`。

你需要掌握：工具名称唯一性、JSON Schema 暴露、Registry 与 Executor 的边界。

验证：

```powershell
& $py -m pytest tests/test_registry.py
```

### TODO 2：参数与结果校验

完成 `ToolExecutor.execute` 的工具查找、Pydantic 参数校验和结果校验。未知工具、非法
参数和非法结果都必须返回结构化失败；它们不能抛出到 Runtime，更不能进入成功结果。

验证：

```powershell
& $py -m pytest tests/test_executor.py -k 'unknown or arguments or invalid_tool_output'
```

### TODO 3：有界重试

补全 Executor 的调用与重试循环。只有 `retryable=True` 的执行错误或超时可以重试，
且 v0 最多重试一次。思考为什么参数错误不能重试，以及 `attempts` 为什么从真正调用
handler 后才开始计数。

验证：

```powershell
& $py -m pytest tests/test_executor.py
```

### TODO 4：Agent Loop 与停止原因

在 `src/kaiji/runtime.py` 完成原生循环：获取模型动作、执行工具、回填结果、记录 trace，
并在完成、工具失败或达到最大步数时给出明确停止原因。只有校验成功的结果可以进入
`successful_tool_results`。

验证：

```powershell
& $py -m pytest tests/test_runtime.py
& $py -m kaiji.demo
```

## 全课验收

```powershell
& $py -m pytest
& $py -m kaiji.demo
```

完成后，请脱离代码解释：

1. 参数校验、工具执行、结果校验分别位于哪一层，为什么？
2. 哪些失败可重试，重试上限在哪里保证？
3. Runtime 如何确保不会无限循环？
4. 为什么失败的工具结果可以进入对话历史，却不能进入成功证据集合？
