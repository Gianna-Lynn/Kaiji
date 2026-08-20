# Agent 岗位能力矩阵（2026-08-20 抽样）

本页用于指导 Kaiji 的求职包装，不是招聘市场统计报告。样本是近期仍可访问的公开 JD、
行业调查和面试资料，偏向 Applied AI / Agent Engineering 中高级岗位；地区、职级和公司
类型不统一，因此只能识别反复出现的能力，不能推断岗位数量或录用概率。

## 直接 JD 样本

1. [Aera Technology — Senior AI/Machine Learning Engineer](https://jobs.lever.co/aeratechnology/05e71552-a936-4067-976e-fbad0a3698e0)
2. [Kobie Marketing — Lead AI Engineer](https://jobs.lever.co/kobie/9fe1a825-9232-4ce5-b196-8a462486f884)
3. [Turgon AI — Senior AI Engineer](https://jobs.lever.co/turgon-ai/3219f081-500a-4a63-b60a-db8c4f540c0b)
4. [Dun & Bradstreet — Senior Gen AI Engineer](https://jobs.lever.co/dnb/187db64c-4590-4a9c-ae8b-91e27d793366)

辅助参考：

- [LangChain 2026 Agent Engineering 调查](https://www.langchain.com/state-of-agent-engineering)：
  1,300+ 从业者样本中，可观测性采用率高于 Eval，质量仍是主要生产障碍之一。
- [AI Engineer Portfolio Projects 2026](https://github.com/landedjobs/ai-engineer-portfolio-projects)：
  建议作品提供架构、Eval、成本/延迟、失败案例与可运行入口。
- [公开 AI 应用工程师简历样本](https://www.syetongzl.com/resume/shiqi-ai-engineering.pdf)：
  仅参考“业务闭环—技术机制—量化证据”的表达结构，不复制经历、数字或措辞。
- [Agentic RAG 面经讨论](https://www.reddit.com/r/LangChain/comments/1k662xc/got_grilled_in_an_ml_interview_today_for_my/)：
  高频追问包括如何评测、如何保护敏感数据，以及为什么使用 Agent 而非传统模型/程序。

## 能力对照

`强` 表示 Kaiji 已有可指向代码与测试的完整主题；`部分` 表示只有受控实现或非生产边界；
`缺` 表示当前没有实现。

| 能力 | JD 中的典型要求 | Kaiji | 证据/缺口 |
|---|---|---:|---|
| Python 工程 | 模块化、可测试、可维护的服务代码 | 强 | Pydantic 模型、协议、Adapter、Runner/Scorer、测试契约 |
| Tool Calling | Tool schema、结构化输出、工具失败处理 | 强 | 原生 Registry/Executor、输入输出双重校验 |
| Agent 编排 | 多步 reasoning、状态、循环与停止 | 强 | 原生 Runtime + LangGraph 条件边 |
| LangChain/LangGraph | 熟悉框架，也能说明边界 | 强 | 原生/框架横向职责对照 |
| RAG/检索 | Vector DB、grounded generation、citations | 部分 | Azusa Adapter 与 BM25 Live；Dense/Hybrid 效果不在 Kaiji 成绩中 |
| Eval | Golden Set、回归、LLM-as-judge、质量指标 | 强/部分 | 确定性 Behavior/Fault Eval 强；没有 LLM-as-judge 人工一致性校准 |
| Observability | Trace、延迟、Token、成本、失败定位 | 部分 | 结构化 Trace/账本/报告与脱敏 OTel 风格投影；没有 SDK/Collector |
| HITL | 高风险动作审批、恢复与升级路径 | 部分 | Interrupt/Resume 为受控模拟；没有审批队列和 Reviewer 身份 |
| Guardrails/Security | Prompt Injection、权限、沙箱、数据隔离 | 部分 | Bearer + 服务端 allowlist + Schema；没有身份系统和内容级防护 |
| API 服务 | FastAPI 或等价生产服务 | 部分 | 薄 FastAPI、契约测试与 Uvicorn 健康小闭环；没有限流、云部署和生产流量 |
| Docker/CI/CD | 可重复部署与回归门禁 | 部分 | 非 root 镜像本地构建/健康小闭环；首次 Actions 待验证 |
| Cloud/数据栈 | AWS/GCP/Azure、SQL、队列、缓存 | 缺 | 项目当前是本地单进程实验 |
| Multi-Agent/MCP | 子 Agent、共享工具协议 | 非目标 | 不为关键词扩张；先把单 Agent 可靠性讲深 |
| Fine-tuning | 开源模型微调或选择判断 | 非目标 | Kaiji 研究模型外部 Runtime，不训练模型 |

## Kaiji 最适合的岗位叙事

Kaiji 对以下方向匹配度较高：

- LLM/Agent 应用工程；
- Agent Runtime、Tool Calling 与工作流编排；
- Agent Eval、Reliability、Trace 与实验基础设施；
- RAG 应用层与引用/证据边界；
- 需要解释 LangGraph 与自研 Runtime 取舍的 Applied AI 岗位。

当前不应把 Kaiji 包装成：

- 已运行生产流量的云服务；
- 完整 Agent 安全平台；
- 多租户、多 Agent 或分布式系统；
- Dense/Hybrid 检索 Benchmark；
- 模型训练、微调或推理优化项目。

## 简历关键词优先级

### 可以优先出现

`Python`、`Pydantic`、`Tool Calling`、`Structured Output`、`LangGraph`、`LangChain`、
`RAG Adapter`、`Checkpoint`、`Human-in-the-loop`、`Trace`、`Agent Eval`、
`Fault Injection`、`Ablation`、`Latency/Token/Cost`、`Bad Case Analysis`、`FastAPI`、
`Docker`、`GitHub Actions`。

### 必须带限定词

- `production-grade`：当前不能使用；
- `observability`：写成结构化 Trace、账本与 OTel 风格投影，不写已接 Collector；
- `security`：写 Bearer、服务端 allowlist/Schema，不写完整 Prompt Injection 防护；
- `RAG`：说明由 Azusa 提供检索，Kaiji 负责 Adapter、工具选择、证据和引用；
- `reliable`：列出具体机制与实验范围，不写泛化 SLA。

## P2 决策结果

P2 按求职收益和项目边界完成以下收口：

1. 极薄 FastAPI 服务、健康检查与 fail-closed Bearer 鉴权；
2. 非 root Dockerfile 与 clean-clone CI 配置；
3. 不含敏感载荷的 OpenTelemetry 风格 span 投影；
4. 服务端 Tool allowlist 与 Prompt Injection 威胁模型；
5. MCP 职责对照文档，不增加 MCP 运行时依赖。

API、离线回归和 WSL Docker 构建/健康小闭环已经动态验证；CI 因分支尚未推送而待验证。
任何一项都不能表述为已部署或已经承载生产流量。
