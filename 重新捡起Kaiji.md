# 重新捡起 Kaiji：最小阅读路线

人，这次只恢复三件事：Kaiji 解决什么问题、一次工具调用怎样完成、失败时怎样停止。
能对着 Demo 讲清这三件事，就可以结束第一轮；不要求重新读完课程或全部源码。

## 第一轮：只跟着折扣 Demo 走

下面的链接均相对于仓库根目录。按表中顺序读，只读指定部分。

| 顺序 | 文件 | 重点看什么 | 读完能回答什么 |
|---|---|---|---|
| 1 | [README.md](README.md) | 项目定位、项目关系、最小体验、分支边界；暂时不点展示分支的扩展链接 | Kaiji 管执行，Azusa 管检索；当前读的是 main 的离线实现 |
| 2 | [demo.py](src/kaiji/demo.py) | 从底部入口进入 `main()`；再看 `ScriptedDecisionProvider`、`DiscountArgs` / `DiscountResult`、`build_registry()` 的第一个工具。搜索和资料工具暂时跳过 | 谁预设动作，谁计算价格，谁组织执行；为什么无需真实模型 |
| 3 | [models.py](src/kaiji/models.py) | 只看 `ToolCall`、`ToolResult`、`Message`、`ModelAction`、`ModelCallResult`、`StopReason`、`RunResult`；先看字段，再读各自校验器。元数据只需知道记录模型身份、延迟和用量 | 调用请求、执行结果、消息、最终返回值有什么区别；哪些组合不合法 |
| 4 | [tools.py](src/kaiji/tools.py) | `ToolDefinition` → `ToolRegistry` → `ToolExecutor.execute()`；先跟成功路径，再看未知工具、参数错误、结果错误、超时和重试分支 | 工具只能从注册表取出；为什么执行前后都校验；重试在哪里发生 |
| 5 | [runtime.py](src/kaiji/runtime.py) | `run()` 的局部状态容器、每轮 `decide()`、消息回填、成功结果保存、所有返回路径 | 一步怎样计数；失败为何不能进入成功结果；重复调用和最大步数怎样停止循环 |
| 6 | [test_runtime.py](tests/test_runtime.py) 与 [test_executor.py](tests/test_executor.py) | Runtime 先看 `test_runtime_backfills_valid_result_then_finishes`、`test_runtime_stops_on_tool_failure`、`test_runtime_has_hard_maximum_steps`、`test_runtime_stops_repeated_identical_calls`；Executor 先看 `test_arguments_are_validated_before_handler_runs`、`test_invalid_tool_output_never_becomes_success`、`test_retryable_failure_recovers_after_one_retry` | 用具体断言核对成功、失败、循环保护和重试，而不只看最终答案 |

读到第 5 步后，再回到 Demo，对照这条路径：

```text
main() 组装 Provider、Registry、Executor 和 Runtime
  → run(question)
  → 第 1 步：Provider 给出预设 ToolCall
  → Executor 校验输入 → handler 计算 → 校验输出
  → Runtime 记录 Trace、回填 assistant/tool 消息、保存成功结果
  → 第 2 步：Provider 给出预设 final_answer
  → 返回 RunResult，停止原因为 completed
```

注意：Demo 的工具真实执行，但动作和答案都已预设，Provider 不读取消息来决定下一步。
它验证执行流程，不验证模型自主选工具或答案忠实于工具结果。成功工具结果也不自动等于可引用的检索证据。
当前 Native Runtime 把最终答案放进 `RunResult.answer`，没有再追加最终 assistant 消息。

## 第一轮的运行与停止标准

在根目录、已安装依赖的 ChuoEnv 中运行：

```powershell
python -m kaiji.demo
$testRoot = '.cache/codex-pytest-review-' + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $testRoot | Out-Null
python -m pytest tests/test_runtime.py tests/test_executor.py -o addopts= -q --basetemp=$testRoot -o "cache_dir=$testRoot/cache"
```

先确认导入来自当前仓库：`python -c "import kaiji; print(kaiji.__file__)"`。
Demo 应得到答案 `The final price is 90.`、`stop_reason=completed`、`steps=2`。
能解释“两步决策、一次工具执行、三条消息”，并指出失败结果与成功结果分别存在哪里，就先停在这里。
测试通过只证明覆盖的断言通过，不等于课程独立审计完成。

## 第二轮：需要解释哪项能力，再补哪一小段

这些不是第一轮必读项，先不展开 Formal、服务化、全部课程和历史实验。

| 想恢复的能力 | 最少补读 | 只抓住的重点 |
|---|---|---|
| Kaiji 怎样调用 Azusa | [azusa_tools.py](src/kaiji/azusa_tools.py) → [adapters/azusa.py](src/kaiji/adapters/azusa.py) | 工具如何绑定 Adapter；`search_azusa` / `get_chunk_by_id` 的输入输出；不追 Azusa 内部检索代码 |
| 人工审批与恢复 | [langgraph_workflow.py](src/kaiji/langgraph_workflow.py) → [test_sqlite_checkpoint_resume.py](tests/test_sqlite_checkpoint_resume.py) | 状态、interrupt、严格 bool 审批、thread_id、新连接恢复；这是另一条工作流路径，不是 Native Runtime 自动获得的能力 |
| 执行与评分怎样分离 | [eval_runner.py](src/kaiji/eval_runner.py) → [eval_scorer.py](src/kaiji/eval_scorer.py) | `AgentEvalCase` 是题目和预期，`AgentEpisode` 是执行记录；先看 Runner 的 `run()` 和 Scorer 的 `score_episode()`，再按调用关系查评分细节 |
| 怎样稳定复现失败 | [faults.py](src/kaiji/faults.py) | `ScriptedToolHandler` 按队列制造瞬时失败等结果；它是测试辅助工具，重试决定仍由 Executor 作出 |

需要引用展示能力或历史数字时，再查 [交付基线](docs/delivery-baseline.md)，确认所属提交、分支和证据范围。
