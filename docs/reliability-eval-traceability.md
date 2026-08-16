# Reliability Eval v0.2 要求追踪表

本表区分书面要求、实现边界与直接测试。测试通过只表示对应断言通过，不自动形成
真实模型或生产结论。

## 课程 TODO 映射

| 课程子题 | 难度 | 主要要求 ID | 独立窄测试 |
|---|---|---|---|
| 13a | L1 | TOOL-02、DATA-02、PROVIDER-02 | `todo_13a` |
| 13b | L1 | CORE-01、CORE-02 | `todo_13b` |
| 13c | L1 | CORE-03、PROVIDER-03 | `todo_13c` |
| 13d | L1 | PROVIDER-01—04 | `todo_13d` |
| 14a | L2 | EVAL-01、EVAL-02 | `todo_14a` |
| 14b | L2 | EVAL-03、EVAL-04 | `todo_14b` |
| 14c | L2 | EVAL-05、EVAL-06 | `todo_14c` |
| 14d | L2 | SQLITE-01、SQLITE-02 | `todo_14d` |
| 15a | L3 | HARNESS-01 | `todo_15a` |
| 15b | L3 | FORMAL-01、FORMAL-06、EXPERIMENT-15B | `todo_15b` |
| 15c | L3 | HARNESS-01、DATA-01—05 | `todo_15c` |
| 15d | L3 | FORMAL-02—09 | `todo_15d` |

原文中连续出现的多个“必须/只能/不得”按可独立违反的语义拆成要求 ID；同一句的复合
要求不会只靠一个最终返回值间接覆盖。

| ID | 强制要求 | 实现边界 | 直接测试或验证 |
|---|---|---|---|
| CORE-01 | Message 的角色与 payload 组合唯一合法 | `Message` 跨字段校验 | `test_message_rejects_illegal_role_payload_combinations` |
| CORE-02 | assistant `tool_call` 与 tool result 的 ID 配对 | 完整历史中 assistant 工具调用后必须紧跟同 `call_id`、同工具名的唯一 tool 结果；孤立、缺失、错配和插入其他角色均在发送前拒绝 | `test_provider_sends_schema_and_round_trips_tool_call_id`、`test_todo_13b_rejects_unpaired_or_misordered_tool_history` |
| CORE-03 | 每模型回合最多一个工具调用 | Provider 拒绝多 `tool_calls`；关键字段严格类型校验，未消费的兼容扩展字段可忽略且不得改变动作语义 | `test_provider_rejects_invalid_json_protocol_and_multiple_calls`、`test_todo_13c_allows_unknown_extensions_without_changing_action` |
| CORE-04 | 同一运行不得复用工具调用 ID | 第二次 handler 前协议失败 | `test_runtime_rejects_reused_tool_call_id_before_second_execution` |
| CORE-05 | 重复调用判断不得依赖 call ID | 规范化 `name + arguments` | `test_runtime_stops_repeated_identical_calls` 使用不同 ID |
| CORE-06 | Runtime 必须有严格正整数最大步数 | 构造配置与循环双重边界 | `test_runtime_has_hard_maximum_steps`、`test_runtime_rejects_truthy_safety_configuration` |
| CORE-07 | 失败工具结果不得成为成功证据 | 只追加校验成功的 `ToolResult` | `test_runtime_stops_on_tool_failure`、`test_chunk_id_mismatch_never_enters_success_evidence` |
| CORE-08 | RunResult 各 artifact 必须互相一致 | 顶层回答/停止/步数/消息/证据交叉校验 | `test_run_result_rejects_cross_artifact_contradictions` |
| CORE-09 | 核心 Kaiji 状态/工具协议不得静默接受未知字段 | 核心业务模型使用 `extra=forbid`；Provider 未消费的兼容响应扩展另按 PROVIDER-03 处理 | `test_core_protocol_models_reject_unknown_fields` |
| CORE-10 | ToolResult 成败 payload 与 attempts 必须一致 | 模型跨字段校验 | `test_tool_result_requires_consistent_payload`、`test_tool_result_attempt_count_matches_execution_boundary` |
| CORE-11 | prior failure 只能记录可重试失败 | Episode 模型直接校验 | `test_prior_attempt_failure_must_have_been_retryable` |
| TOOL-01 | Registry 工具名唯一且保持定义顺序 | 注册期拒绝重复，返回副本 | `test_registry_rejects_duplicate_name`、`test_registry_definitions_preserve_order_without_exposing_mutable_container` |
| TOOL-02 | 暴露 Schema 与执行边界必须一致 | `additionalProperties=false`，输入/输出 `extra=forbid` | `test_registry_exposes_schema_and_lookup`、Executor 两项额外字段测试 |
| TOOL-03 | 未知工具和参数错误不得进入 handler | 预校验失败 `attempts=0` | `test_unknown_tool_is_structured_and_not_attempted`、`test_arguments_are_validated_before_handler_runs` |
| TOOL-04 | 参数与结果只允许有限 JSON | handler 前与结果校验后拒绝非有限值 | Executor 两项 non-finite 测试 |
| TOOL-05 | retryable 必须是真正 bool | 定义、异常与结果模型严格类型 | `test_executor_rejects_truthy_retry_flag`、`test_tool_execution_error_requires_strict_retryable_bool` |
| TOOL-06 | 可重试失败最多再执行一次 | Executor 固定重试预算 | `test_retryable_failure_reports_two_attempts_when_budget_is_exhausted` |
| TOOL-07 | timeout 和意外异常必须结构化 | `ToolFailure` 分类且有界 | timeout、unexpected runtime error 测试 |
| PROVIDER-01 | Provider 失败不得击穿 Runtime | `ModelCallResult.failure` 与 `model_failed` | `test_runtime_structures_provider_failure_without_completing` |
| PROVIDER-02 | 密钥只能从指定环境变量读取且不得进入 Trace | 延迟读取并净化意外异常 | `test_provider_reads_key_only_from_environment`、`test_runtime_hides_unexpected_provider_exception_text` |
| PROVIDER-03 | timeout、429、5xx、网络和协议必须分类 | `ProviderErrorCode`；消费字段严格类型，兼容扩展字段允许忽略；最终文本须非空白，工具/最终回答的 `finish_reason` 必须匹配；arguments 只接受有限 JSON 对象 | `test_provider_classifies_http_failures`、`test_todo_13c_rejects_blank_text_and_inconsistent_finish_reason`、`test_todo_13c_rejects_nonfinite_tool_arguments` |
| PROVIDER-04 | Provider 不得隐藏网络重试 | 上层 Pilot/Formal 按每次尝试记账 | `test_network_failure_retries_once_and_preserves_first_attempt` |
| DATA-01 | Behavior 只能包含可自主推导意图 | 故意重复/非法参数留在 Reliability | Behavior 五意图组与数据隔离测试 |
| DATA-02 | 所有 Behavior case 必须共用 Prompt 和五工具 | Runner 固定 Registry 和 Prompt hash | `test_behavior_pilot_uses_one_prompt_and_complete_tool_set` |
| DATA-03 | 工具说明必须区分本地搜索与可引用 Azusa 搜索 | 五工具固定描述与 Schema hash | Behavior 工具集契约测试、引用组 Pilot/ Formal |
| DATA-04 | 折扣 percent 必须表示从原价减免比例 | 固定工具描述与确定性 handler | Behavior discount 数据与 Provider 请求载荷测试 |
| DATA-05 | 题目必须提供工具必填参数且评分不得要求隐藏参数 | 数据契约预检 | Behavior/Paraphrase 一一对应与可评分值测试 |
| EVAL-01 | Runner 必须执行真实 Runtime 并保存原始结果 | `AgentEvalRunner` 保存 `RunResult` | `test_runner_executes_runtime_and_preserves_raw_result` |
| EVAL-02 | 每个 case 必须使用隔离的工厂状态 | 每 Episode 新建 Provider/Registry | `test_runner_calls_factories_for_each_case_without_state_leak` |
| EVAL-03 | Scorer 不得接受 Runner 自报成功 | 从动作、回答、停止和证据推导 | `test_scorer_derives_success_from_result_not_runner_claim` |
| EVAL-04 | 引用只能来自允许集合与成功证据交集 | 输出引用与成功 ToolResult 双重约束 | failed/unallowed citation 与 metadata masquerade 测试 |
| EVAL-05 | 报告必须覆盖全部 case 且 run ID 唯一 | 聚合前集合与身份校验 | `test_aggregate_requires_every_case_and_unique_run_ids` |
| EVAL-06 | 不适用样本不得稀释指标 | 工具/无工具/重试/引用各自固定分母 | `test_aggregate_uses_only_applicable_metric_denominators` |
| TODO8-01 | Checkpoint 身份必须为非空白字符串 | 模型、读取与查询入口统一验证 | `test_checkpoint_rejects_empty_identifiers`、lookup/read boundary 测试 |
| TODO8-02 | 外部 JSON 必须有限且损坏不得伪装为缺失 | 严格 `_read_all()` 与外壳重验 | `test_todo_8a_checkpoint_read_boundary_is_independently_verifiable`、corrupt tests |
| TODO8-03 | 保存必须原子替换，失败不得改变旧 latest | 同目录临时文件后 `replace` | `test_todo_8b_save_isolated_from_load_and_latest`、replace failure 测试 |
| TODO8-04 | load/latest 只能返回重验后的 Checkpoint 或 None | 8c/8d 各自复用严格读取 | `test_todo_8c_load_isolated_from_save_and_latest`、`test_todo_8d_latest_isolated_from_save_and_load` |
| TODO9-01 | user/key 身份不得为空白且外内 key 必须相等 | `_read_all()` 全量重验 | `test_todo_9a_memory_read_boundary_is_independently_verifiable` |
| TODO9-02 | put 必须先拒绝旧损坏并原子覆盖 | 严格读后写，保持首次顺序 | `test_todo_9b_put_isolated_from_get_and_list`、overwrite/replace tests |
| TODO9-03 | get/list 对损坏和缺失必须得出同一语义 | 共用 `_read_all()`；缺失分别为 None/[] | `test_todo_9c_get_isolated_from_put_and_list`、`test_todo_9d_list_isolated_from_put_and_get` |
| TODO10-01 | Trace 必须有唯一末尾 stop 且摘要一致 | `TraceRecord` 时间线校验 | `test_todo_10a_trace_record_rejects_timeline_without_stop` 及 stop 系列测试 |
| TODO10-02 | step 必须为严格正整数且非递减 | Event 类型和 Record 顺序校验 | strict step/decreasing step 测试 |
| TODO10-03 | data/metadata 必须为有限 JSON | 模型构造期拒绝 | `test_trace_record_rejects_non_json_or_non_finite_payloads` |
| TODO10-04 | 每个非空物理行只能有一条完整记录 | 严格迭代器，缺失为空、坏行报行号 | `test_todo_10b_iter_isolated_from_load_append_and_replay`、invalid line 测试 |
| TODO10-05 | 同一文件 run ID 必须唯一 | append 前扫描并拒绝重复 | `test_todo_10d_append_isolated_from_replay`、duplicate tests |
| TODO10-06 | replay 必须稳定且未知 ID 显式失败 | 排序紧凑 JSON | `test_todo_10e_replay_isolated_from_file_reader`、unknown run 测试 |
| TODO11-01 | Observation 安全字段必须严格类型且计数非负 | 模型直接与跨字段校验 | `test_todo_11a_observation_rejects_invalid_counts_and_measurements`、coercion tests |
| TODO11-02 | 选择/尝试/成功/恢复/引用状态必须内部一致 | Observation 跨字段校验 | `test_observation_rejects_ambiguous_cross_field_states` |
| TODO11-03 | case 与 observation ID 各自必须唯一 | `_unique_index()` 与 JSONL loader | `test_todo_11b_unique_index_isolated_from_alignment_and_metrics`、`test_todo_11c_load_cases_rejects_duplicate_ids` |
| TODO11-04 | 聚合前必须按 ID 完全对齐，输入顺序无身份语义 | `_align_observations()` | `test_todo_11d_alignment_isolated_from_metric_formulas` |
| TODO11-05 | 指标只能按固定适用分母与 nearest-rank 计算 | `_aggregate_aligned()` | `test_todo_11e_metrics_use_already_aligned_observations`、mixed/zero denominator tests |
| FORMAL-01 | 40×3 Behavior 与 20×3 Reliability 不得合并冒充真实 180 | 独立 Suite、Manifest 与报告 | 一一对应测试、`test_preflight_writes_separate_120_and_60_episode_reports` |
| FORMAL-02 | 数据、配置、Prompt、Schema 和模型必须冻结 | Manifest fingerprints；密钥名不入 Manifest | `test_resume_rejects_changed_dataset_or_config`、manifest tamper 测试 |
| FORMAL-03 | 每次请求必须先记 started，响应后记 Token；完整报告在配置绑定后才可断言账本与模型回合及重试一致 | 持久化 ledger；模型层只校验结构关系；`_report` 根据 provider 绑定 `requests_started == responses_finished` 与真实 Provider 请求数 = 模型回合数 + `prior_attempt_failures`，Scripted/Mock Preflight 固定为 0 | ledger counts/restore/boundary、live retry 正负计数与 complete-report ledger invariant 测试 |
| FORMAL-04 | 未决请求必须阻断恢复，不得按零费用继续 | started/finished 不等即失败 | `test_unresolved_request_blocks_real_resume_without_network` |
| FORMAL-05 | Episode 必须逐条 fsync，恢复只能跳过已完成坐标 | hash 链 JSONL 与坐标集合 | partial/resume/tamper/duplicate tests |
| FORMAL-06 | 部分报告不得包含 Aggregate 或标为完整 | 完整矩阵前 `complete=false` | partial run 与 closed budget 测试 |
| FORMAL-07 | 离线重评分不得构造 Provider，默认不得覆写 | `rescore_formal_run(..., persist=False)` | 两项 offline rescore 测试 |
| FORMAL-08 | 显式迁移必须先保留旧报告 | 首次写 `*.before-rescore.json` | `test_offline_rescore_is_dry_by_default_and_migrates_with_backups` |
| EXPERIMENT-15B | 普通 Experiment 失败不得伪造 incomplete 正式报告 | 全矩阵成功后才构造 `ExperimentReport`；产物先写临时目录再改名 | `test_todo_15b_failed_experiment_writes_no_formal_report`、报告完整性模型测试 |
| FORMAL-09 | hash 链只能检测未重算的损坏，不证明真实性 | 文档和记录模型明确无密钥、无外部可信锚点 | 契约审查；hash 重算仍可通过的边界说明 |
| HARNESS-01 | 四 Harness 只能切换 retry/duplicate guard | 同 Provider/Schema/Prompt/数据/max_steps | `test_retry_ablation`、`test_duplicate_guard_ablation_remains_bounded` |
| SQLITE-01 | 官方 Saver 必须跨新连接恢复且线程隔离 | State Schema 版本与 thread ID | `test_sqlite_resumes_interrupt_across_fresh_graph_and_connection` |
| SQLITE-02 | 审批只能接受真正 bool | interrupt 节点与恢复入口双重校验 | `test_sqlite_strict_bool_and_thread_isolation` |
| AZUSA-01 | Search 响应 mode 必须配对且 chunk ID 不得重复 | Adapter 严格响应模型 | `test_http_search_rejects_schema_drift_mode_mismatch_and_duplicate_ids` |
| AZUSA-02 | metadata 只允许有限 JSON，未知字段不得静默丢弃 | Evidence 模型和 HTTP response `extra=forbid` | evidence metadata 与 schema drift 测试 |
| AZUSA-03 | 429/503 只能有界恢复一次 | Adapter 标记 retryable，Executor 固定预算 | `test_http_429_and_503_recover_once` |
| AZUSA-04 | TODO12 必须是真实 BM25 HTTP 闭环，不得以 skip 代替 | fixture 网关 + live test | 2026-08-12 独立复验 `PYTEST_EXIT=0` |

## 独立设计审查结论

- Contract Suite、答案实现和测试当前自洽，但 Scripted 成绩不得当作模型成绩。
- `full_kaiji` 的优势只在固定故障机制上成立；尚无真实 Formal 对照数据。
- Live 第五条首次运行暴露了真实工具冷态超时，说明单次成功率不足以描述可靠性；
  该失败已保留在 Bad Case 中。
- 当前 Citation Scorer 使用确定性字符串事实和固定证据 ID，适合受控数据，不等价于
  开放域事实一致性或语义蕴含评估。
- 首次 Autonomous Pilot 的原始严格分数为 0.10，但 12 条传输失败且题集复用了
  Scripted 故障指令，因此该数字只能作为 Bad Case 审计输入，不能作为模型成绩。
- 重设计后的有效 Pilot 严格 Task Success 为 0.95；唯一失分是查询扩写不满足固定
  参数金标，工具、证据、回答和引用均正确。该成绩只有单次重复，不能外推稳定成功率。
- Formal Preflight 已离线完成 120 条 Behavior 与 60 条 Reliability；前者 1.0 只证明
  Mock 动作、数据、恢复与评分管线自洽，后者 0.90 的 6 个失败仍是刻意空证据伪引用。
  两者都不是新的真实模型成绩。
- DeepSeek Real Behavior Formal 已完成 120 Episode：严格 Task Success 0.9667、工具
  选择 1.0、参数 0.9583、无工具 1.0、引用 Precision/Recall 1.0。参数值为冻结后按
  “要求工具的 Episode”适用分母离线重算；原报告的 0.9667 曾被不适用样本稀释。
  4 个失败均为 query 扩写违反
  固定参数金标；功能路径、事实与引用仍正确。该结论限于受控 Mock 工具 fixture。
- 本轮独立审计反向构造了可通过旧测试的错误实现，确认并修复：Pydantic bool/int
  强制转换、`NaN`/`Infinity` 穿透、空白持久化身份、Trace step 倒退、未知字段静默
  丢弃、指标分母稀释、Search mode/ID 漂移、重复 `tool_call_id` 与 RunResult 跨 artifact
  矛盾。以上结论分别有直接负向断言，不由全量绿灯间接推断。
- SQLite 结论限于本地同步工作流；没有分布式、并发写入或服务级结论。
