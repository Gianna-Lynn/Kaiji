"""提供带服务端工具授权边界的极薄 FastAPI 接口。"""

from __future__ import annotations

from collections.abc import Callable
import os
import secrets
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    StrictStr,
    field_validator,
)

from kaiji.demo import build_registry
from kaiji.models import RunResult
from kaiji.observability import SpanProjection, project_run_to_spans
from kaiji.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolExecutor, ToolRegistry


RuntimeFactory = Callable[[frozenset[str]], AgentRuntime]


class ServiceSettings(BaseModel):
    """保存服务端鉴权、Provider 与工具 allowlist 配置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_token: SecretStr | None = None
    allowed_tools: frozenset[StrictStr] = Field(default_factory=frozenset)
    provider_base_url: StrictStr | None = Field(default=None, min_length=1)
    provider_model: StrictStr | None = Field(default=None, min_length=1)
    provider_api_key_env: StrictStr = Field(
        default="KAIJI_PROVIDER_API_KEY",
        min_length=1,
    )
    max_steps: StrictInt = Field(default=4, ge=1, le=12)

    @field_validator("api_token")
    @classmethod
    def api_token_is_not_blank(cls, value: SecretStr | None) -> SecretStr | None:
        """已配置的服务 token 不得是空白值。"""

        if value is not None and not value.get_secret_value().strip():
            raise ValueError("api_token 不得为空白")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def allowed_tools_are_not_blank(
        cls,
        value: frozenset[str],
    ) -> frozenset[str]:
        """服务端权限项必须是可审计的非空名称。"""

        if any(not name.strip() or name != name.strip() for name in value):
            raise ValueError("allowed_tools 不得包含空白或两端空格")
        return value

    @classmethod
    def from_env(cls) -> ServiceSettings:
        """从环境读取配置；只保存 API token，不读取 Provider key 明文。"""

        raw_tools = os.environ.get("KAIJI_ALLOWED_TOOLS", "")
        parsed_tools = [name.strip() for name in raw_tools.split(",") if name.strip()]
        if len(parsed_tools) != len(set(parsed_tools)):
            raise ValueError("KAIJI_ALLOWED_TOOLS 不得包含重复工具名")
        allowed_tools = frozenset(parsed_tools)
        raw_max_steps = os.environ.get("KAIJI_MAX_STEPS", "4")
        try:
            max_steps = int(raw_max_steps)
        except ValueError as exc:
            raise ValueError("KAIJI_MAX_STEPS 必须是整数") from exc
        raw_token = os.environ.get("KAIJI_API_TOKEN")
        return cls(
            api_token=SecretStr(raw_token) if raw_token else None,
            allowed_tools=allowed_tools,
            provider_base_url=os.environ.get("KAIJI_PROVIDER_BASE_URL"),
            provider_model=os.environ.get("KAIJI_PROVIDER_MODEL"),
            provider_api_key_env=os.environ.get(
                "KAIJI_PROVIDER_API_KEY_ENV",
                "KAIJI_PROVIDER_API_KEY",
            ),
            max_steps=max_steps,
        )

    def readiness_issues(self) -> tuple[str, ...]:
        """返回不包含秘密值的缺失配置名称。"""

        issues: list[str] = []
        if self.api_token is None:
            issues.append("KAIJI_API_TOKEN")
        if not self.allowed_tools:
            issues.append("KAIJI_ALLOWED_TOOLS")
        known_tools = {
            definition.name for definition in build_registry().definitions()
        }
        if self.allowed_tools - known_tools:
            issues.append("KAIJI_ALLOWED_TOOLS_UNKNOWN")
        if not self.provider_base_url:
            issues.append("KAIJI_PROVIDER_BASE_URL")
        if not self.provider_model:
            issues.append("KAIJI_PROVIDER_MODEL")
        if not os.environ.get(self.provider_api_key_env):
            issues.append(self.provider_api_key_env)
        return tuple(issues)


class RunRequest(BaseModel):
    """定义一次无状态 Agent 请求及调用者希望缩小到的工具集合。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: StrictStr = Field(min_length=1, max_length=8_000)
    requested_tools: list[StrictStr] | None = Field(default=None, max_length=16)

    @field_validator("question")
    @classmethod
    def question_is_not_blank(cls, value: str) -> str:
        """拒绝只包含空白符的问题。"""

        if not value.strip():
            raise ValueError("question 不得为空白")
        return value

    @field_validator("requested_tools")
    @classmethod
    def requested_tools_are_unique(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        """拒绝空白和重复工具名，避免授权审计产生歧义。"""

        if value is None:
            return None
        if any(not name.strip() or name != name.strip() for name in value):
            raise ValueError("requested_tools 不得包含空白或两端空格")
        if len(value) != len(set(value)):
            raise ValueError("requested_tools 不得重复")
        return value


class RunResponse(BaseModel):
    """返回一次运行结果及其脱敏 span 投影。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    result: RunResult
    spans: list[SpanProjection]


class HealthResponse(BaseModel):
    """描述进程存活或配置就绪状态。"""

    model_config = ConfigDict(extra="forbid")

    status: str


def create_app(
    *,
    settings: ServiceSettings | None = None,
    runtime_factory: RuntimeFactory | None = None,
) -> FastAPI:
    """创建无进程级对话状态的服务，便于测试时注入 Runtime。"""

    resolved = settings or ServiceSettings.from_env()
    factory = runtime_factory or _runtime_factory(resolved)
    app = FastAPI(
        title="Kaiji Agent Runtime",
        version="0.1.0",
        description="带显式工具授权与停止原因的单 Agent Runtime API。",
    )

    def authorize(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        """使用恒定时间比较验证 Bearer token；未配置时拒绝而非开放服务。"""

        if resolved.api_token is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="服务鉴权尚未配置",
            )
        scheme, separator, credential = (authorization or "").partition(" ")
        expected = resolved.api_token.get_secret_value()
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not credential
            or not secrets.compare_digest(
                credential.encode("utf-8"),
                expected.encode("utf-8"),
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的 Bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        """只确认 ASGI 进程能够处理请求。"""

        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse)
    def ready() -> HealthResponse:
        """确认安全运行所需环境已配置，不发起外部请求。"""

        issues = list(resolved.readiness_issues())
        if issues:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="服务尚未就绪",
            )
        return HealthResponse(status="ready")

    @app.post(
        "/v1/runs",
        response_model=RunResponse,
        dependencies=[Depends(authorize)],
    )
    def run_agent(request: RunRequest) -> RunResponse:
        """在服务端权限集合内为当前请求创建并执行独立 Runtime。"""

        if resolved.readiness_issues():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="服务运行配置不完整",
            )
        requested = (
            frozenset(request.requested_tools)
            if request.requested_tools is not None
            else resolved.allowed_tools
        )
        unauthorized = requested - resolved.allowed_tools
        if unauthorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"unauthorized_tools": sorted(unauthorized)},
            )
        try:
            runtime = factory(requested)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Runtime 配置无效",
            ) from None
        result = runtime.run(request.question)
        run_id = uuid4().hex
        return RunResponse(
            run_id=run_id,
            result=result,
            spans=project_run_to_spans(run_id, result),
        )

    return app


def _runtime_factory(settings: ServiceSettings) -> RuntimeFactory:
    """构造只会暴露服务端已授权工具的 Runtime 工厂。"""

    def build(selected_tools: frozenset[str]) -> AgentRuntime:
        base_registry = build_registry()
        known_tools = {definition.name for definition in base_registry.definitions()}
        unknown = selected_tools - known_tools
        if unknown:
            raise ValueError(f"服务端 allowlist 包含未知工具：{sorted(unknown)}")
        if not settings.provider_base_url or not settings.provider_model:
            raise ValueError("Provider 配置不完整")

        registry = ToolRegistry()
        for definition in base_registry.definitions():
            if definition.name in selected_tools:
                registry.register(definition)
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                base_url=settings.provider_base_url,
                model=settings.provider_model,
                api_key_env=settings.provider_api_key_env,
            ),
            registry,
        )
        return AgentRuntime(
            provider,
            ToolExecutor(registry),
            max_steps=settings.max_steps,
        )

    return build


app = create_app()
