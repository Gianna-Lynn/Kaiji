# FastAPI、容器与 CI 边界

P2 给 Kaiji 增加的是一个薄传输层，而不是第二套 Agent 编排。HTTP 请求经过鉴权与
Pydantic 校验后，仍由原生 `AgentRuntime`、`ToolExecutor` 和 `RunResult` 承担行为语义。

## 接口

| 方法与路径 | 鉴权 | 职责 |
|---|---|---|
| `GET /health/live` | 无 | 只证明 ASGI 进程能处理请求 |
| `GET /health/ready` | 无 | 静态检查安全运行所需配置，不连接 Provider |
| `POST /v1/runs` | Bearer | 创建一次独立 Runtime，返回结果与脱敏 span 投影 |

请求示例：

```json
{
  "question": "Kaiji 如何限制工具循环？",
  "requested_tools": ["local_search"]
}
```

`requested_tools` 只能缩小服务端 `KAIJI_ALLOWED_TOOLS`，请求出现集合外工具时返回
`403`。省略该字段表示使用服务端完整 allowlist；空数组表示本次不向模型暴露工具。

## 配置

| 环境变量 | 用途 | 是否必须 |
|---|---|---|
| `KAIJI_API_TOKEN` | 服务自己的 Bearer token | 是 |
| `KAIJI_ALLOWED_TOOLS` | 逗号分隔的服务端工具 allowlist | 是 |
| `KAIJI_PROVIDER_BASE_URL` | OpenAI-compatible `/v1` 前缀 | 是 |
| `KAIJI_PROVIDER_MODEL` | 模型名 | 是 |
| `KAIJI_PROVIDER_API_KEY_ENV` | 保存 Provider key 的环境变量名 | 否 |
| `KAIJI_PROVIDER_API_KEY` | 默认 Provider key 变量 | 是，除非改名 |
| `KAIJI_MAX_STEPS` | Runtime 最大逻辑步数，范围 1—12 | 否，默认 4 |

服务不会把 Provider key 放入 Pydantic 响应、Trace 或 span；配置缺失时 fail closed。

## 本地启动命令

```powershell
python -m pip install -e '.[service]'
$env:KAIJI_API_TOKEN = '<local-secret>'
$env:KAIJI_ALLOWED_TOOLS = 'local_search,get_profile'
$env:KAIJI_PROVIDER_BASE_URL = 'https://provider.example/v1'
$env:KAIJI_PROVIDER_MODEL = '<model-name>'
$env:KAIJI_PROVIDER_API_KEY = '<provider-secret>'
uvicorn kaiji.service:app --host 127.0.0.1 --port 8080
```

示例值是占位符，不应写入仓库或镜像。

## 容器边界

`Dockerfile` 使用 Python slim 基础镜像、非 root 用户、`.dockerignore` 和存活检查：

```powershell
docker build --tag kaiji:local .
docker run --rm --publish 8080:8080 --env-file .env kaiji:local
```

镜像不包含测试、文档、历史 artifacts、`.env`、SQLite 或 JSONL 运行数据。当前没有镜像
签名、SBOM、漏洞门禁或秘密管理集成，因此不能称为生产级供应链。

## CI 边界

`.github/workflows/ci.yml` 在 pull request 以及三个长期分支 push 时：

1. 从 clean clone 安装 `.[dev,service]`；
2. 显式排除 `test_azusa_live_integration.py`，运行其余离线回归；
3. 在独立 job 构建容器镜像。

工作流没有真实 Provider key，也不运行 Azusa Live。P2 已在 ChuoEnv 完成 588 条离线
回归和真实 Uvicorn 健康请求；WSL Docker Engine 已完成镜像构建、非 root、Docker
`HEALTHCHECK` 和容器内 fail-closed 小闭环。分支尚未推送，因此 Actions 仍必须以首次
远端运行结果为准。完整证据见 [P2 动态验证记录](p2-validation.md)。

## 设计依据

- [FastAPI Request Body](https://fastapi.tiangolo.com/tutorial/body/)：使用 Pydantic 模型
  生成请求校验与 OpenAPI Schema；
- [GitHub Actions：构建和测试 Python](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)：
  clean clone、Python 环境和 pytest 门禁；
- [Docker 构建最佳实践](https://docs.docker.com/build/building/best-practices/)：精简上下文、
  非 root 运行和 CI 构建检查。
