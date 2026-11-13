# 三分支迁移验收记录

日期：2026-11-13；仅本地操作，未推送或删除任何远端分支。

## 验收结果

| 分支 | 独立离线回归 | 真实 TODO12 fixture | 展示复验 |
|---|---|---|---|
| main | 611 passed | 1 passed | 三个 Demo、SQLite 新连接、120+60 Preflight、公开包、HTTP 均通过 |
| develop | 611 passed | 1 passed | 同上，包含人的原有未提交源码 |
| solution | 657 passed，1 条既有 Starlette 弃用警告 | 1 passed | 同上，另覆盖 Chat/Web、service 与 span 投影测试 |

各验证独立检查 kaiji.__file__，固定对应工作区 src，pytest 使用独立 `.cache/codex-pytest-<唯一标识>`。全套离线明确排除 live fixture，后者在独立本机网关下分别执行，未用 skipped 替代。

main 源码和测试沿用人的迁移前已提交基底 eb5a53e；原始未提交源码保留 develop。受测提交为 main d3358ca、develop 366d3c5、solution 7d2caf4，后续仅补充本文等文档。实际受测源码散列记录在各分支生成报告中。

所有模型演示使用 Scripted/Mock 或规则决策，真实模型请求为 0。Formal 故障组 0.90 的严格成功率包含六条被识别的伪引用，不表示所有任务成功。真实 HTTP 使用 Azusa develop 58c081c，并仅在验证子进程内设置回环 NO_PROXY；没有切换 Azusa 或修改代理配置。

## 人的改动保全

原有 `.vscode/settings.json`、五个源码文件、`tests/test_runtime.py` 和 `重新捡起Kaiji.md` 在 develop 与备份逐字节相同；AGENTS 原文完整保留，仅附加当前分支说明。上述改动未纳入迁移提交，未重新挖空任何已完成能力。

solution 原有 Chat/Web 新源码、静态文件和对应测试逐字节保留并提交。其 Runtime 与模型原始改动已先完整备份；本次补充旧 answer-only 产物的兼容校验，并同步人的预期失败评分和引用零分母报告修复。旧公开包未改写；原 Chat/Web README 保留在 solution 的 docs/reference-chat-web.md。

## 恢复与清理

备份位于本机 `.cache/branch-migration-20261113`，包含 all-refs.bundle、三个工作区 zip、manifest、补丁和索引；bundle verify 和 zip 全文件 SHA256 均通过。最终逐项保全核对及报告路径见其中 final-validation.json，原始测试日志和失败诊断也保留。

旧 portfolio 历史已连接到 solution，独有服务、观测、部署和历史展示材料已保全；两个旧备份分支历史已在 develop。完成上述验证后才清理本地旧分支，保留 main、develop、solution。远端旧引用仍保留，因为本轮禁止推送。

本次只验收展示范围与迁移保全；不替代完整教学独立审计、逐子 TODO 学习路径验收、真实模型复验、Docker 重建或远端 CI。
