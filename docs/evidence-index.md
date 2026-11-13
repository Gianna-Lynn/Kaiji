# 展示证据索引与失败边界

| 展示主张 | 实现 | 直接验证入口 |
|---|---|---|
| 输入输出校验与有界重试 | tools.py、models.py | test_registry.py、test_executor.py |
| 明确停止原因与证据隔离 | runtime.py | test_runtime.py |
| 框架职责与严格审批 | langchain_compare.py、langgraph_workflow.py | 对应测试与 test_sqlite_checkpoint_resume.py |
| 文件快照、Memory、Trace 职责分离 | persistence.py、tracing.py | test_persistence.py、test_tracing.py |
| HTTP 契约与失败分类 | adapters/azusa.py、azusa_tools.py | test_azusa_adapter.py、TODO12 live fixture |
| 规则问答的实际引用 | knowledge_demo.py | test_knowledge_demo.py、scripts/verify_azusa.py |
| 执行与确定性评分分离 | eval_runner.py、eval_scorer.py | test_eval_runner.py、test_eval_scorer.py |
| 矩阵恢复与请求账本 | formal_runner.py | test_formal_runner.py、scripts/verify_delivery.py |
| 可携带复核与故障样例 | public_evidence.py、examples/public_evidence_v1 | test_public_evidence.py、公开包 CLI |

以上文件名相对于 src/kaiji 或 tests。当前命令见 demo-guide.md；历史验证见 delivery-baseline.md 与 p1-validation.md，旧数字不自动归属于当前提交。

## 失败解释

工具参数错误在 handler 前失败，attempts 为 0。超时只结束等待，不终止线程；有副作用的工具需要另行设计幂等与取消，当前 Azusa 工具只读。模型失败、工具失败、重复调用、最大步数分别停止；业务回答正确性不能从 completed 单独推出。

固定 Eval 的金标观测只验证聚合器。Behavior 与 Reliability 分开报告；引用评分依赖受控事实和证据集合，不代表开放域语义判断。Formal 的未决请求阻断恢复，partial 没有正式 aggregate；无密钥 hash 链不能认证作者或来源。

## 参考扩展

旧 portfolio 的完整文件快照和提交已备份；服务、span 投影、部署配置与历史展示材料转存 solution。其历史文档不是 main 当前能力清单。Chat/Web 是答案区参考工作，不能在 main 宣称已交付。
