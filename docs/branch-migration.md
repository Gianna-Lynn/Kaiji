# 三分支迁移与远端同步

更新日期：2026-11-13。本地迁移和远端同步均已完成，本地与 origin 只保留 main、develop、solution，默认分支为 main。共享 Python 安装未改写。

## 当前职责与目录

| 分支 | 本机目录 | 职责 |
|---|---|---|
| develop | F:/Repositories/Kaiji | 人的全部学习实现、注释与原有未提交改动 |
| main | F:/Repositories/Kaiji/.worktrees/kaiji-portfolio | 展示分支；目录名保留以避免移动工作区和本机产物 |
| solution | F:/Repositories/Kaiji/.worktrees/kaiji-solution | 参考实现、服务扩展与保全的 Chat/Web 工作 |

main 从人的原 main 基底 eb5a53e 建立，保留其实现与测试。develop 不重新挖空已完成能力；main 的编号 TODO 只是课程定位。main 的机械展示整理与契约文档需回流 develop；答案分支在保留自身实现的同时补齐人的 P1 成果。

## 可恢复备份

本机备份：F:/Repositories/Kaiji/.cache/branch-migration-20261113。

- all-refs.bundle：迁移前全部引用及其可达历史，已执行 bundle verify。
- develop.zip、portfolio.zip、solution.zip：各工作区已跟踪和未忽略的新文件原字节，共 282 文件，逐项 SHA256 校验。
- manifest.json：原 HEAD、状态及逐文件散列；各工作区另有 staged/unstaged 二进制补丁与原始 index。
- 原有被忽略的模型、实验、缓存不移动不删除；本次新增报告写入唯一目录。

恢复时先在独立目录 clone all-refs.bundle，再按 manifest 指定旧提交 checkout，将对应 zip 解压到独立副本；可用 staged 补丁重建暂存内容。不要直接覆盖当前工作区，也不要在未经身份核对的不同安全上下文直接替换 index。备份只存本机，不提交。

## 验收范围

迁移前人的 main：611 项离线测试通过；三个 Demo、SQLite 新连接恢复、120+60 离线 Preflight、公开包和本机 HTTP 通过；TODO12 fixture 单独 1 passed。HTTP 使用 Azusa develop 58c081c，临时回环 NO_PROXY 后通过，真实模型请求为 0。

迁移后已分别固定三个工作区 src：main/develop 各 611 passed，solution 657 passed；三个 Demo、交付核验、公开包、HTTP 与 TODO12 fixture 均通过。完整范围见 [验收记录](migration-acceptance.md)。

本轮是展示交付与本地迁移验收，不替代 TODO8—12 新版完整独立审计、逐子任务学习路径验收或真实模型测试。历史报告保持原提交与旧分支名，不回填新来源。

## 保留与清理条件

develop 保留人的原始源码和测试修改、VS Code 设置、AGENTS 新增规则与重新捡起Kaiji.md，保持未提交。solution 原有 Chat/Web 改动作为参考工程保全并提交，原字节仍可从备份还原；测试与契约兼容所需调整单独记录。

旧 portfolio 的全部文件及历史已备份，独有服务能力已转入 solution；本地旧备份分支和远端旧分支均已在保全核对后清理。当前本地与 origin 都只保留 main、develop、solution，临时远端保全分支也已删除。

## 本次兼容修复

solution 保留多轮聊天及最终 assistant 消息；为复核旧产物，RunResult 同时接受旧单轮 answer-only 外形，且有普通 assistant 回答时仍严格要求唯一、位于末尾并等于 answer。同步人的预期失败评分规则和引用零分母报告格式，未改写冻结证据包。main/develop 的消息契约不变。原 Chat/Web 文档完整保留于 solution 的 docs/reference-chat-web.md。

## 远端同步记录

2026-11-13 已推送 main 7f0cb11、develop 25ee55d、solution 66b50fa，Git 与 GitHub API 双重核对通过；旧 solution/kaiji-v0 和 portfolio/kaiji-v0 已清理。上述是同步时点提交，分支后续可正常前进。推送未创建新提交，develop 工作文件与暂存区均保持原状。备份包含在本机 .cache/remote-sync-7f1beee91a3f41eba3feee10f1d06d3d。
