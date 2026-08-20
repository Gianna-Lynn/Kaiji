# P2 动态验证记录

验证日期：2026-08-20。验证工作树：`portfolio/kaiji-v0`。本页区分已经运行的证据与仍未
验证的交付配置，不把本地回归扩大解释为生产可用性。

## 环境

- Python：ChuoEnv Python 3.11.14；
- 隔离方式：所有命令使用 `python -s`，禁止读取 roaming user-site；
- FastAPI：0.141.1；
- Starlette：1.6.0；
- Uvicorn：0.52.4；
- pytest：9.0.2。
- Docker：WSL2 Ubuntu 内 Docker Engine 29.6.1；
- 构建镜像：`kaiji:p2-validation`，镜像 ID
  `sha256:b1358f500ed5ae85f2a045ff23b2335a41c4d0c323df78838f27a283df9873b4`。

当前普通 `python -m pip` 会优先加载用户目录 pip；本次因此固定使用
`D:\Chuo_Data\conda_envs\ChuoEnv\python.exe -s -m ...`，避免把全局包误当成 ChuoEnv 证据。

## P2 窄测试

```powershell
D:\Chuo_Data\conda_envs\ChuoEnv\python.exe -s -m pytest `
  tests/test_observability.py tests/test_service.py `
  --basetemp=.codex-pytest-tmp -o cache_dir=.codex-pytest-cache
```

结果：`10 passed, 1 warning in 0.43s`。

首轮曾得到 `9 passed, 1 failed`：失败样例尝试用 Unicode `str` 构造 HTTP Header，httpx
在请求进入 ASGI 前按 ASCII 编码并抛错。fixture 改为原始非 ASCII Header 字节后，样例
成功触达服务，鉴权返回 `401`。这是测试输入修复，不是隐藏产品失败。

## 完整离线回归

```powershell
D:\Chuo_Data\conda_envs\ChuoEnv\python.exe -s -m pytest `
  --ignore=tests/test_azusa_live_integration.py `
  --basetemp=.codex-pytest-tmp -o cache_dir=.codex-pytest-cache
```

结果：`588 passed, 1 warning in 5.32s`。唯一 warning 是 Starlette 1.6 提示 TestClient
正在由 `httpx` 迁移到 `httpx2`；当前 FastAPI 测试仍通过，Kaiji 暂不同时引入两套客户端。

## 真实 ASGI 请求

使用 Uvicorn 监听 `127.0.0.1:8765`，在不设置任何秘密或 Provider 配置时得到：

| 请求 | 状态 | 响应 |
|---|---:|---|
| `GET /health/live` | 200 | `{"status":"ok"}` |
| `GET /health/ready` | 503 | `{"detail":"服务尚未就绪"}` |
| `POST /v1/runs` + 伪 Bearer | 503 | `{"detail":"服务鉴权尚未配置"}` |

这验证了 ASGI 启动、路由和缺配置 fail-closed。验证后已停止本次 Uvicorn 进程。

## Docker 完整构建

前两次构建在 BuildKit 解析 `docker/dockerfile:1` 时遇到 WSL2 NAT UDP DNS 超时。经只读
诊断确认 `/etc/resolv.conf` 指向 `/mnt/wsl/resolv.conf`，内容只有
`nameserver 172.27.208.1`，初始 SHA-256 为
`40922a9be347f2457c044cec55f3f999e30eb83247f516c510050d968ae23619`。

在用户逐项授权下，本次使用 root 事务临时在该文件末尾增加 `options use-vc`，不更换
DNS 地址，并用 trap 保证恢复。实际构建命令为：

```bash
env RES_OPTIONS=use-vc docker build \
  --progress=plain \
  --tag kaiji:p2-validation \
  /mnt/f/Repositories/Kaiji/.worktrees/kaiji-portfolio
```

结果：Dockerfile frontend 解析、`python:3.12-slim` Registry 元数据与镜像层、构建容器内
`RUN pip install --no-cache-dir ".[service]"`、wheel 构建和最终镜像导出全部成功，退出码为
0。构建后镜像大小为 280 MB；Docker 总镜像占用由 270.4 MB 增至 550 MB，Build Cache
由 8.192 KiB 增至 323.2 MB。

事务结束后原文件内容和 SHA-256 均恢复，唯一临时备份
`/tmp/kaiji-resolv.conf.qkjyon` 已删除。没有持久化 DNS 或代理设置，没有修改 Docker
daemon，也没有停止、重启或删除既有容器。

## 容器运行小闭环

使用上述镜像创建独立临时容器，验证结果如下：

| 检查 | 结果 |
|---|---|
| 镜像配置用户 | `kaiji` |
| 容器内 UID/GID | `999/999`，非 root |
| Docker `HEALTHCHECK` | `healthy` |
| 容器内 `GET /health/live` | `200`，`{"status":"ok"}` |
| 容器内 `GET /health/ready` | `503`，`{"detail":"服务尚未就绪"}` |

测试容器由 trap 删除。测试前后正在运行的既有容器 ID 集合完全一致。

## 环境与未验证项

- `pip check` 报告 ChuoEnv 中既有的两个无关问题：`python-docx` 缺 `lxml`，以及
  `virtualenv` 期望更新的 `filelock`。二者不在 Kaiji 依赖图和本次测试路径中，未擅自修改。
- P2 尚未推送，因此 GitHub Actions 尚未实际运行。
- 没有调用真实模型、Azusa Live、外部身份系统或 OpenTelemetry Collector。
- `588 passed` 证明当前断言在该环境中通过，不等价于生产安全、部署成功或完整审计。
