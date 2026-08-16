# Kaiji Reliability Eval v0.2 契约

## 评测边界

现有 `eval/cases.jsonl` 是组件 Contract Suite，不执行真实 Agent，不能作为
Agent 成功率。Agent Behavior Suite 与 Reliability Suite 必须通过原生
`AgentRuntime` 运行，并保存完整 `RunResult`、模型调用元数据和 Trace；Scorer
只能从这些原始结果独立计算成绩。

真实 Behavior Suite 只包含能够从用户提示和公开工具 Schema 自主推导的正常意图。
要求模型故意重复、故意不终止、故意构造非法参数或触发指定内部停止原因的场景属于
Reliability Suite，必须由可控 Provider 或 Fault Injector 驱动，不能混入真实模型
行为成功率。真实 Pilot 使用独立的 `eval/real_behavior_cases.jsonl`，不复用
Scripted Formal 的 `agent_cases.jsonl`。

## 失败与重试

模型请求的 timeout、429、5xx 和网络失败标记为可重试，但 Provider 本身不做
隐藏重试，调用预算由上层实验显式控制。Azusa Adapter 的 timeout、网络失败、
429、5xx 与响应校验失败允许现有 `ToolExecutor` 有界重试一次；其他 4xx 和明确
的业务错误不可重试。当前版本不解析 `Retry-After`，也不等待或进行无界退避。

每个模型回合最多接受一个工具调用。历史消息中 assistant 工具调用后必须紧跟一个
同 `tool_call_id`、同工具名的 tool 结果；孤立 tool、缺失结果、ID/工具名错配以及
插入其他角色都在请求发送前拒绝。同一历史不得复用调用 ID。非法 JSON、多工具调用、
关键字段或语义发生 Schema Drift，以及 chunk ID 错配都属于协议或证据失败，不得进入
成功证据；未消费的兼容扩展字段不属于失败。

Provider 只严格校验会被 Kaiji 消费的关键字段类型（不把字符串/布尔值隐式转换为
计数或文本），未消费的兼容扩展字段允许忽略，不因服务端新增 `object`、`created`、
`system_fingerprint`、`index` 或 `logprobs` 等字段而失败。工具路径的
`finish_reason` 只接受 `tool_calls` 或兼容服务省略该字段，最终回答路径只接受
`stop` 或省略该字段；最终文本必须包含非空白字符。工具 arguments 必须是 JSON 对象，
并拒绝 `NaN`、`Infinity` 等非有限常量。

`required_answer_facts` 使用确定性的字面量规则：先对回答和事实分别做 Unicode
NFKC 规范化、大小写折叠与连续空白折叠，再要求事实作为连续片段出现；事实两端若
是 ASCII 字母、数字或下划线，则不能与回答中的同类 ASCII 字符相邻（例如事实
`90` 不匹配 `190`）；CJK 字符不参与该西文标识边界。该字段不做自然语言蕴含、
否定识别、同义词、翻译或数值等价判断，题目应
提供可由字面量检查的短事实。空白事实、重复事实在 `AgentEvalCase` 构造时拒绝。

TODO14 的 `retry_recovered` 只统计原生 `AgentRuntime` 中 `ToolExecutor` 在同一
工具调用内的有界 retry，并要求最终运行成功。TODO11 LangGraph 的 query rewrite
由 `retry_count`/`step_count` 独立记录，不能从 Native `RunResult` 的消息数量猜测；
Provider 的 timeout、网络、429、5xx retry 是传输可靠性指标，不属于该业务恢复指标。

工具 Schema 与 Executor 使用同一严格边界：未声明输入和输出字段都拒绝，参数、结果、
证据 metadata 只允许有限 JSON。同一运行内 `tool_call_id` 不得复用；Search 响应的
`mode` 必须与请求一致，`chunk_id` 不得重复。框架 Tool 封装不会替代这些业务校验。

所有真实 Behavior Case 使用同一 System Prompt 与同一套五个工具。工具说明必须让
模型区分本地演示搜索与可引用 Azusa 证据搜索；`calculate_discount.percent` 明确表示
从原价中减免的百分比。题目必须给出决定必填参数所需的信息，评分不得要求提示中没有
约定的隐藏参数。

真实 Pilot 的联网重试只覆盖 timeout、传输失败、429 与 5xx，且至多一次。每次真实
请求都必须独立消耗共享请求预算；第一次失败必须作为 `prior_attempt_failures` 保存在
最终 Episode，不能因第二次成功而隐藏。协议、鉴权或余额类失败不借网络重试掩盖。

## Formal 持久化与恢复

普通 `ExperimentRunner` 与 Formal Runner 的失败语义不同：普通 Experiment 只有在
`case × repeat` 全部完成且校验通过时才返回并落盘正式 `ExperimentReport`；Provider、
评分、身份校验或产物写入异常会直接失败，不生成 `incomplete` 报告。需要断点续跑和
部分产物时必须使用 Formal Runner，不能把普通实验的异常目录当作正式成绩。

真实 Behavior Formal 由 20 条核心行为与 20 条一一对应同义变体组成，重复三次后为
120 Episode。Reliability Formal 保留 20 条受控故障并重复三次，形成独立的 60
Episode 报告。后者执行 Scripted Provider/Fault Injector，不得与前者相加后表述为
“180 条真实模型实验”。

Formal 创建时冻结数据集 hash、完整配置 fingerprint、System Prompt hash、五工具
Schema hash、模型、base URL、温度、thinking、输出上限、价格与预算。恢复时任何一项
变化都必须拒绝；API Key 及其环境变量名不进入 Manifest。

每次真实请求在发送前更新 `request-ledger.json`，响应后再记录已报告 Token。对完整
Formal，账本不变量是 `responses_finished == requests_started`，且真实 Provider 的
`requests_started` 必须等于所有已完成 Episode 中模型回合数加每个回合的
`prior_attempt_failures` 数量；模型重试也占用一次请求预算。Scripted/Mock Preflight
明确不发送模型请求，账本请求数固定为 0，不能拿 Episode 中的替身模型回合数冒充真实
网络请求。

`FormalRunReport` 模型本身只校验上述计数的结构关系，允许 `expected_request_count=0`
表示离线替身；它不携带 Provider 配置，不能独立证明 real-provider 语义。只有
`ResumableFormalRunner._report` 根据冻结的 `FormalRunConfig.provider` 绑定 live/offline
口径后，才可作真实请求账本结论。

若恢复
时 `requests_started != responses_finished`，说明存在成本未知的请求，必须以
`unresolved_request` 停止人工审计，不得把未知 Token 当零继续。请求硬上限使用
`requests_started`，因此重启不能重置预算。

每个完成 Episode 以 `(case_id, repeat_index)` 为矩阵坐标追加到 hash 链 JSONL，并在
写入后执行 flush 与 fsync。恢复时已完成坐标只能跳过，不得重复消费。数据、配置、
hash 链、坐标或 run ID 不一致都必须拒绝恢复。部分报告的 `complete` 必须为 false、
`aggregate` 必须为空；只有固定矩阵完整后才允许聚合成绩。

hash 链是无密钥的篡改检测机制，不是签名、真实性证明或作者身份认证；能够改写产物的
攻击者可以重算整条链。需要真实性时必须另行提供可信外部锚点或签名机制，本阶段不把
链 hash 当作真实性结论。

## 指标分母

报告必须覆盖配置中的每条 case，且每个 Episode 使用唯一 `run_id`。缺失字段按
失败或零用量计入既定分母，不能通过省略失败 Episode 缩小分母。引用只在同时属于
case 允许引用集合与 `successful_tool_results` 中已校验证据时计为受支持。

工具选择与参数准确率只以要求工具的 Episode 为分母；无工具准确率只以
`expects_no_tool` 为分母；重试恢复只以 `expects_retry_recovery` 为分母。Citation
Precision 使用全部实际引用数，Recall 使用全部允许引用数；没有适用项时报告 `0.0`，
不得把不适用样本先记成满分再平均。

## Harness 对照

`bounded_baseline` 关闭一次工具重试与重复调用保护，`no_retry` 只保留重复
保护，`no_duplicate_guard` 只保留有界重试，`full_kaiji` 同时启用两者。四种
Harness 都保留相同工具 Schema、参数/结果校验、Provider、System Prompt、数据、
温度和最大步骤；因此基线仍然有界且安全。Checkpoint 不计入普通单回合成功率。

## DeepSeek Smoke 决策

真实 Smoke 使用官方 `https://api.deepseek.com` 与 `deepseek-v4-flash`，并通过
`--api-key-env` 指定临时凭据所在的环境变量。请求明确发送
`thinking: {type: disabled}`、`stream: false`、
`temperature: 0` 与 `max_tokens: 512`。原因是 DeepSeek V4 默认启用 thinking，
而 thinking 工具回合要求把 `reasoning_content` 无损回传；Kaiji v0.2 刻意不扩展
这条协议。首轮最多发送 20 个请求，只传受控提示、工具 Schema 与公开 fixture。

真实 Azusa Pilot 使用答案分支网关的 3 条公开 fixture 与 `bm25` 模式。它验证
DeepSeek 选择工具、Kaiji 经 HTTP Adapter 调用网关、工具结果回填、引用归属和
Trace，不宣称 Hybrid、Dense 或大规模检索成绩。默认 Dense/Hybrid 后端首次加载
Embedding 模型超过了 Kaiji Adapter 的 3 秒默认超时，因此不纳入这组微型 Smoke。
