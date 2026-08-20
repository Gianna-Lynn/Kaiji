# Kaiji 中英文简历项目描述

## 使用规则

本页把“仓库具备能力”和“作者已经亲手掌握”分开。简历只能使用能独立解释、修改并验证
的 bullet。当前 `main` 已完成人的 TODO1—11；TODO13—15 虽然存在于答案/portfolio，
但在人亲手完成学习前，不应写成“我独立实现”。

所有数字必须保留实验范围。不要把受控 Mock 工具、Scripted Fault Suite 或历史审计写成
生产成绩。

## 当前可安全使用：中文版

### 一行版

Kaiji——基于 Python/Pydantic、LangChain/LangGraph 与 Azusa RAG Adapter 的可靠单 Agent
Runtime，覆盖类型化工具调用、有界重试、HITL 恢复、Trace 和固定 Eval。

### 标准项目 bullet

- 从零实现单 Agent Tool Calling Runtime，以 Pydantic/JSON Schema 约束工具参数和返回值，
  对未知工具、额外字段、非法 JSON 和失败结果进行结构化隔离。
- 设计工具 timeout、至多一次重试、重复业务调用检测、最大步骤和七类停止原因，确保模型
  循环具备明确预算与可审计退出路径。
- 使用 LangGraph 实现检索、证据评估、一次查询改写和 Human-in-the-loop Interrupt/Resume，
  区分搜索步数、恢复线程与审批状态。
- 设计 Checkpoint、Memory、Message History 与 Trace 的独立数据边界，实现 JSON/JSONL
  严格读取、原子写入、身份隔离和稳定 Trace 回放。
- 通过稳定 `search_azusa`/`get_chunk_by_id` Adapter 集成独立 Azusa RAG，确保只有成功且
  通过 Schema 校验的工具结果进入最终证据和引用集合。
- 构建 20 条固定 Eval Case 与确定性聚合，覆盖工具选择、参数、重试、停止原因、延迟和
  Citation，并用混合正负样例防止“全满分 fixture”掩盖公式错误。

### 面向不同岗位的选配

- Agent Runtime 岗：优先第 1、2、3 条。
- RAG/应用岗：优先第 1、5、6 条。
- Eval/LLMOps 岗：优先第 2、4、6 条。

## 当前可安全使用：English

### One-line summary

Kaiji — a reliability-focused single-agent runtime built with Python, Pydantic, LangChain/
LangGraph, and a typed Azusa RAG adapter, covering bounded tool execution, HITL recovery,
tracing, and deterministic evaluation.

### Project bullets

- Built a single-agent tool-calling runtime from first principles, enforcing Pydantic/JSON
  Schema contracts on both tool arguments and results while isolating unknown tools,
  undeclared fields, invalid JSON, and failed evidence.
- Designed explicit reliability controls including tool timeouts, at most one retry,
  business-call deduplication, step budgets, and seven structured stop reasons.
- Implemented a LangGraph workflow for retrieval, evidence assessment, one bounded query
  rewrite, and human-in-the-loop interrupt/resume with isolated thread state.
- Separated checkpoints, long-term memory, message history, and traces into distinct data
  lifecycles with strict JSON/JSONL validation, atomic writes, identity isolation, and replay.
- Integrated an external Azusa RAG service through typed `search_azusa` and
  `get_chunk_by_id` adapters, allowing only validated successful results into citation evidence.
- Created a 20-case deterministic evaluation suite covering tool selection, arguments,
  retries, stop reasons, latency, and citation support, including adversarial mixed outcomes
  to expose aggregation false positives.

## 完成 TODO13—15 并能独立答辩后解锁

以下 bullet 描述 portfolio 已有能力，但目前不是默认个人主张。只有人在 `main` 亲手完成、
理解测试、修改关键逻辑并完成相应验证后才能使用。

### 中文

- 实现 OpenAI-compatible Provider 的请求构造、消息配对、单工具响应解析和 timeout/429/5xx/
  协议错误分类，并记录 Token、延迟和重试历史。
- 构建隔离的 Agent Episode Runner 与确定性 Scorer，按适用 Episode 计算工具选择、参数、
  No-tool、Retry Recovery 和 Citation 指标，修复不适用样本稀释分母的问题。
- 设计四组 Reliability Harness 与受控故障注入，在固定 20 条故障集上完成 retry/duplicate
  guard Ablation，`full_kaiji` Task Success 为 0.90；该结果不外推真实模型。
- 实现可恢复 Formal Runner，以 Manifest 指纹、逐请求费用账本、Episode hash 链和坐标去重
  支持中断恢复；历史 DeepSeek 实验完成 120 Episode，严格成功 116/120。
- 对真实 Pilot 的金标泄露、题集失效、网络失败和 Scorer 分母问题进行方法学复核，保留
  无效实验与 Bad Case，而非只报告最佳结果。

### English

- Implemented an OpenAI-compatible provider boundary covering request construction,
  tool-message pairing, single-tool response parsing, structured HTTP/protocol failures,
  token usage, latency, and retry history.
- Built isolated runtime episode execution and a deterministic scorer with applicability-aware
  denominators for tool selection, arguments, no-tool behavior, retry recovery, and citations.
- Designed four controlled reliability harnesses for retry and duplicate-guard ablations;
  `full_kaiji` reached 0.90 task success on the fixed 20-case scripted fault suite without
  generalizing the result to real-model performance.
- Implemented a resumable formal experiment runner with frozen manifests, a per-request cost
  ledger, episode hash chains, and coordinate deduplication; the historical DeepSeek run
  completed 120 episodes with 116/120 strict success under controlled mock tools.
- Audited invalid pilots, leaked gold labels, transport failures, and metric-denominator bugs,
  preserving failed experiments and bad cases instead of reporting only the best run.

## 亲自复核 P2 后解锁

P2 已在 portfolio 分支完成代码、契约测试、588 条离线回归、Uvicorn 健康小闭环，以及
Docker 构建、非 root 和容器健康验证；远端 CI 尚未验证。人在能够解释权限边界、修改
关键逻辑并亲自复验之前，不把下列内容加入正式简历。

### 中文

- 为单 Agent Runtime 增加薄 FastAPI 服务，以 fail-closed Bearer 鉴权和服务端 Tool
  allowlist 保证调用者只能缩小权限，并为每个请求构建独立 Runtime/Registry。
- 将 Kaiji Trace 映射为脱敏 OpenTelemetry 风格 span，只暴露停止原因、工具状态和错误码，
  不记录用户问题、工具参数/输出或 Provider 正文。
- 提供非 root Docker 镜像和 GitHub Actions clean-clone 门禁，隔离执行离线回归与镜像构建；
  使用时必须补充真实 CI 结果，不能写成生产部署。

### English

- Added a thin FastAPI boundary with fail-closed bearer authentication and a server-owned
  tool allowlist, ensuring callers can only reduce permissions and each request receives an
  isolated runtime and registry.
- Projected Kaiji traces into redacted OpenTelemetry-style spans containing operational
  status and error codes without user prompts, tool arguments/results, or provider bodies.
- Added a non-root Docker image and clean-clone GitHub Actions gates for offline regression
  and image builds; these are delivery artifacts, not evidence of production deployment.

## 禁止使用的表述

- “生产级 Agent 平台”
- “实现了 exactly-once 副作用”
- “支持完整 Prompt Injection 防护”
- “在生产环境成功率 96.67%”
- “Hybrid RAG 达到 100% 引用准确率”
- “独立实现 TODO13—15”（在人尚未亲手完成之前）
- “已接入 OpenTelemetry/已生产部署”（当前只有 span 投影与交付配置）
- “节省 X 小时/降低 Y% 成本”（仓库没有对应真实业务证据）

## 面试证据准备

每使用一条 bullet，至少准备：

1. 对应源码入口；
2. 一个成功路径；
3. 一个失败或对抗路径；
4. 为什么不采用更复杂方案；
5. 当前实现仍不能保证什么。

证据入口见 [`evidence-index.md`](evidence-index.md)，压力问题见
[`interview-adversary.md`](interview-adversary.md)。
