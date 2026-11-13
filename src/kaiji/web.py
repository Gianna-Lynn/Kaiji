"""提供本机聊天网页、安全 API 与追加式 JSONL 审计日志。"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
from time import perf_counter
from typing import Annotated, Callable, Protocol
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)

from kaiji.chat import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    SYSTEM_PROMPT,
    ChatSessionManager,
    _build_runtime_factory,
)
from kaiji.models import RunResult, StopReason
from kaiji.providers.openai_compatible import OpenAICompatibleConfig
from kaiji.runtime import AgentRuntime


SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
MAX_MESSAGE_LENGTH = 4000
INPUT_PRICE_CNY_PER_MILLION = Decimal("2")
OUTPUT_PRICE_CNY_PER_MILLION = Decimal("8")
MILLION = Decimal("1000000")
_STATIC_DIR = Path(__file__).with_name("web_static")
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_KEY_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key(?:[_-]?env)?|x-api-key|authorization|"
    r"credential|secret|access[_-]?token)\b[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}]+)"
)
_SECRET_TOKEN_PATTERN = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b")
_HEADER_LINE_PATTERN = re.compile(
    r"(?im)^\s*[\"']?(?:authorization|proxy-authorization|cookie|set-cookie|"
    r"x-api-key|api-key|content-type|accept|user-agent|x-request-id)[\"']?"
    r"\s*:\s*[^\r\n]*"
)


def _session_id_is_safe(value: str) -> str:
    """拒绝会话标识意外承载常见 API Key 或鉴权文本。"""

    if (
        _SECRET_TOKEN_PATTERN.search(value)
        or _BEARER_PATTERN.search(value)
        or _KEY_ASSIGNMENT_PATTERN.search(value)
    ):
        raise ValueError("session_id 不得包含凭据样式文本")
    return value


class ChatRequest(BaseModel):
    """校验单轮聊天输入并拒绝请求中未声明的字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: StrictStr = Field(pattern=SESSION_ID_PATTERN)
    message: StrictStr = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)

    @field_validator("session_id")
    @classmethod
    def session_id_does_not_look_like_a_credential(cls, value: str) -> str:
        """避免调用方将凭据误用为可能进入日志的会话 ID。"""

        return _session_id_is_safe(value)

    @field_validator("message")
    @classmethod
    def message_is_not_blank(cls, value: str) -> str:
        """拒绝只包含空白符的提问。"""

        if not value.strip():
            raise ValueError("message 不得为空白")
        return value


class ResetRequest(BaseModel):
    """校验仅针对单个会话的重置请求。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: StrictStr = Field(pattern=SESSION_ID_PATTERN)

    @field_validator("session_id")
    @classmethod
    def session_id_does_not_look_like_a_credential(cls, value: str) -> str:
        """避免调用方将凭据误用为会话 ID。"""

        return _session_id_is_safe(value)


class HistoryMessage(BaseModel):
    """表示可以返回浏览器的普通用户或 assistant 消息。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    role: StrictStr
    content: StrictStr


class ChatResponse(BaseModel):
    """限定聊天 API 可见的安全运行摘要，不包含 Trace 或原始错误。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: StrictStr = Field(pattern=SESSION_ID_PATTERN)
    answer: StrictStr | None
    stop_reason: StopReason
    failure_code: StrictStr | None
    latency_ms: StrictFloat = Field(ge=0, allow_inf_nan=False)
    model: StrictStr = Field(min_length=1)
    input_tokens: StrictInt | None
    output_tokens: StrictInt | None
    total_tokens: StrictInt | None
    estimated_peak_cost_cny: StrictStr | None
    tool_names: list[StrictStr]
    tool_success: StrictBool | None
    log_persisted: StrictBool
    logging_warning: StrictStr | None

    @field_validator("session_id")
    @classmethod
    def session_id_does_not_look_like_a_credential(cls, value: str) -> str:
        """避免在安全摘要中返回凭据样式的会话标识。"""

        return _session_id_is_safe(value)


class AuditRecord(BaseModel):
    """严格限制 JSONL 中允许持久化的每轮安全字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: StrictStr = Field(min_length=1)
    session_id: StrictStr = Field(pattern=SESSION_ID_PATTERN)
    timestamp: datetime
    latency_ms: StrictFloat = Field(ge=0, allow_inf_nan=False)
    stop_reason: StopReason
    failure_code: StrictStr | None
    model: StrictStr = Field(min_length=1)
    input_tokens: StrictInt | None
    output_tokens: StrictInt | None
    total_tokens: StrictInt | None
    estimated_peak_cost_cny: Decimal | None
    tool_names: list[StrictStr]
    tool_success: StrictBool | None
    log_content_enabled: StrictBool
    user_content: StrictStr | None = None
    assistant_content: StrictStr | None = None

    @field_validator("session_id")
    @classmethod
    def session_id_does_not_look_like_a_credential(cls, value: str) -> str:
        """避免把凭据样式文本写入会话索引字段。"""

        return _session_id_is_safe(value)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        """确保日志时间戳明确带有时区。"""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp 必须带时区")
        return value

    @model_validator(mode="after")
    def content_matches_privacy_setting(self) -> AuditRecord:
        """只在内容日志开关启用时允许保存提问或回答。"""

        if not self.log_content_enabled and (
            self.user_content is not None or self.assistant_content is not None
        ):
            raise ValueError("关闭内容日志时不得保存对话正文")
        if self.log_content_enabled and self.user_content is None:
            raise ValueError("开启内容日志时必须记录本轮提问")
        return self

    @field_serializer("estimated_peak_cost_cny", when_used="json")
    def serialize_cost_as_decimal_string(self, value: Decimal | None) -> str | None:
        """将金额以十进制字符串写入 JSON，避免浮点数误差。"""

        if value is None:
            return None
        return format(value, "f")


class AuditLogger(Protocol):
    """定义可注入的审计记录写入接口。"""

    def append(self, record: AuditRecord) -> None: ...


class JsonlAuditLogger:
    """在进程内线程锁保护下将已校验记录追加为 JSONL。"""

    def __init__(self, path: str | Path) -> None:
        """固定日志目标路径；路径只由服务端配置提供。"""

        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: AuditRecord) -> None:
        """先序列化并校验单行，再以追加模式写入并同步到磁盘。"""

        validated = AuditRecord.model_validate(record.model_dump())
        payload = validated.model_dump(mode="json")
        if payload["user_content"] is None:
            del payload["user_content"]
        if payload["assistant_content"] is None:
            del payload["assistant_content"]
        line = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if b"\n" in line[:-1] or b"\r" in line[:-1]:
            raise ValueError("JSONL 记录必须只占一行")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as output:
                output.write(line)
                output.flush()
                os.fsync(output.fileno())


class WebChatService:
    """复用会话管理器并按会话串行化聊天、历史读取和重置。"""

    def __init__(
        self,
        manager: ChatSessionManager,
        logger: AuditLogger,
        *,
        log_content_enabled: bool,
    ) -> None:
        """保存本地服务依赖和受锁保护的每会话锁表。"""

        self.manager = manager
        self.logger = logger
        self.log_content_enabled = log_content_enabled
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def session_lock(self, session_id: str) -> threading.Lock:
        """为同一个标识返回同一把锁，避免并发回合交错提交。"""

        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[session_id] = lock
            return lock

    def history(self, session_id: str) -> list[HistoryMessage]:
        """返回普通对话正文，并过滤所有工具消息与工具调用。"""

        with self.session_lock(session_id):
            messages = self.manager.get(session_id).history
            return [
                HistoryMessage(role=message.role, content=message.content)
                for message in messages
                if message.role == "user"
                or (message.role == "assistant" and message.tool_call is None)
            ]

    def reset(self, session_id: str) -> None:
        """串行清空指定会话的已提交历史。"""

        with self.session_lock(session_id):
            self.manager.reset(session_id)

    def chat(self, request: ChatRequest) -> tuple[ChatResponse, int]:
        """运行、汇总并持久化一轮；安全处理模型和日志失败。"""

        started = perf_counter()
        with self.session_lock(request.session_id):
            result = self.manager.get(request.session_id).turn(request.message)
            latency_ms = max(0.0, (perf_counter() - started) * 1000)
            summary = _summarize_result(result)
            run_id = str(uuid4())
            record = AuditRecord(
                run_id=run_id,
                session_id=request.session_id,
                timestamp=datetime.now(timezone.utc),
                latency_ms=latency_ms,
                stop_reason=result.stop_reason,
                failure_code=_failure_code(result),
                model=summary["model"],
                input_tokens=summary["input_tokens"],
                output_tokens=summary["output_tokens"],
                total_tokens=summary["total_tokens"],
                estimated_peak_cost_cny=summary["estimated_peak_cost_cny"],
                tool_names=summary["tool_names"],
                tool_success=summary["tool_success"],
                log_content_enabled=self.log_content_enabled,
                user_content=(
                    _redact_sensitive_content(request.message)
                    if self.log_content_enabled
                    else None
                ),
                assistant_content=(
                    _redact_sensitive_content(result.answer)
                    if self.log_content_enabled and result.answer is not None
                    else None
                ),
            )
            try:
                self.logger.append(record)
                log_persisted = True
                logging_warning = None
            except Exception:
                # 不暴露路径、异常内容或可能夹带服务信息的底层错误。
                log_persisted = False
                logging_warning = "logging_failed"

        response = ChatResponse(
            session_id=request.session_id,
            answer=result.answer,
            stop_reason=result.stop_reason,
            failure_code=_failure_code(result),
            latency_ms=latency_ms,
            model=summary["model"],
            input_tokens=summary["input_tokens"],
            output_tokens=summary["output_tokens"],
            total_tokens=summary["total_tokens"],
            estimated_peak_cost_cny=_cost_string(summary["estimated_peak_cost_cny"]),
            tool_names=summary["tool_names"],
            tool_success=summary["tool_success"],
            log_persisted=log_persisted,
            logging_warning=logging_warning,
        )
        if result.stop_reason == StopReason.COMPLETED and not log_persisted:
            return response, 207
        if result.stop_reason != StopReason.COMPLETED:
            return response, 502
        return response, 200


def _sum_usage(values: list[int | None]) -> int | None:
    """只有所有模型调用都提供该项时才报告精确合计。"""

    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _summarize_result(result: RunResult) -> dict[str, object]:
    """从本轮模型调用和工具结果中提取有限的结构化统计。"""

    calls = result.model_calls
    input_tokens = _sum_usage([call.metadata.usage.input_tokens for call in calls])
    output_tokens = _sum_usage([call.metadata.usage.output_tokens for call in calls])
    total_tokens = _sum_usage([call.metadata.usage.total_tokens for call in calls])
    cost = None
    if input_tokens is not None and output_tokens is not None:
        cost = (
            Decimal(input_tokens) * INPUT_PRICE_CNY_PER_MILLION / MILLION
            + Decimal(output_tokens) * OUTPUT_PRICE_CNY_PER_MILLION / MILLION
        ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    tool_results = [
        message.tool_result
        for message in result.messages
        if message.role == "tool" and message.tool_result is not None
    ]
    tool_names = list(
        dict.fromkeys(_redact_sensitive_content(tool.tool_name) for tool in tool_results)
    )
    return {
        "model": (
            _redact_sensitive_content(calls[-1].metadata.model)
            if calls
            else "unknown"
        ),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_peak_cost_cny": cost,
        "tool_names": tool_names,
        "tool_success": (
            all(tool.ok for tool in tool_results) if tool_results else None
        ),
    }


def _cost_string(value: Decimal | None) -> str | None:
    """格式化金额字符串，保留六位精度且不经过二进制浮点。"""

    return format(value, "f") if value is not None else None


def _failure_code(result: RunResult) -> str | None:
    """只返回稳定的 Provider/工具错误代码，不暴露原始错误文本。"""

    if result.stop_reason == StopReason.MODEL_FAILED and result.model_calls:
        failure = result.model_calls[-1].failure
        if failure is not None:
            return failure.code.value
    if result.stop_reason == StopReason.TOOL_FAILED:
        for message in reversed(result.messages):
            if message.tool_result is not None and message.tool_result.error is not None:
                return message.tool_result.error.code.value
    if result.stop_reason != StopReason.COMPLETED:
        return result.stop_reason.value
    return None


def _content_logging_enabled() -> bool:
    """仅接受字面值 true 开启正文持久化。"""

    return os.environ.get("KAIJI_LOG_CONTENT") == "true"


def _redact_sensitive_content(value: str) -> str:
    """在显式正文日志中遮蔽常见 API Key、Bearer 与鉴权头格式。"""

    redacted = _HEADER_LINE_PATTERN.sub("[REDACTED HTTP HEADER]", value)
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    redacted = _KEY_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", redacted)
    return _SECRET_TOKEN_PATTERN.sub("[REDACTED API KEY]", redacted)


def create_app(
    *,
    runtime_factory: Callable[[], AgentRuntime] | None = None,
    logger: AuditLogger | None = None,
    log_path: str | Path = "artifacts/web/chat-log.jsonl",
    log_content_enabled: bool | None = None,
) -> FastAPI:
    """创建可注入 Runtime 与日志器的 FastAPI 应用。"""

    content_enabled = (
        _content_logging_enabled()
        if log_content_enabled is None
        else log_content_enabled
    )
    if type(content_enabled) is not bool:
        raise TypeError("log_content_enabled 必须是真正的 bool")
    if runtime_factory is None:
        config = OpenAICompatibleConfig(
            base_url=DEFAULT_BASE_URL,
            model=DEFAULT_MODEL,
            api_key_env=DEFAULT_API_KEY_ENV,
            temperature=0,
            max_tokens=DEFAULT_MAX_TOKENS,
            system_prompt=SYSTEM_PROMPT,
            thinking="disabled",
        )
        runtime_factory = _build_runtime_factory(config)
    audit_logger = logger if logger is not None else JsonlAuditLogger(log_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """在应用生命周期中创建并持有会话管理器和审计日志器。"""

        manager = ChatSessionManager(runtime_factory)
        app.state.chat_session_manager = manager
        app.state.audit_logger = audit_logger
        app.state.chat_service = WebChatService(
            manager,
            audit_logger,
            log_content_enabled=content_enabled,
        )
        yield
        del app.state.chat_service
        del app.state.audit_logger
        del app.state.chat_session_manager

    app = FastAPI(title="Kaiji Local Chat", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.exception_handler(RequestValidationError)
    async def invalid_request_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """避免默认 422 响应回显用户输入或未知字段内容。"""

        del request, exc
        return JSONResponse(status_code=422, content={"error": "invalid_request"})

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """提供固定的本地聊天页面。"""

        return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")

    @app.post("/api/chat")
    def chat(body: ChatRequest, request: Request) -> JSONResponse:
        """执行一轮聊天并返回不含内部 Trace 的运行摘要。"""

        try:
            response, status_code = request.app.state.chat_service.chat(body)
        except Exception:
            # 未预期服务异常不携带原始文本，避免泄露请求、凭据或内部状态。
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error"},
            )
        return JSONResponse(
            status_code=status_code,
            content=response.model_dump(mode="json"),
        )

    @app.get("/api/history")
    def history(
        request: Request,
        session_id: Annotated[str, Query(pattern=SESSION_ID_PATTERN)],
    ) -> JSONResponse:
        """返回当前会话的普通用户与 assistant 消息。"""

        try:
            _session_id_is_safe(session_id)
        except ValueError:
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_request"},
            )
        messages = request.app.state.chat_service.history(session_id)
        return JSONResponse(
            content={
                "session_id": session_id,
                "messages": [message.model_dump(mode="json") for message in messages],
            }
        )

    @app.post("/api/reset")
    def reset(body: ResetRequest, request: Request) -> JSONResponse:
        """重置指定会话的内存历史。"""

        request.app.state.chat_service.reset(body.session_id)
        return JSONResponse(content={"session_id": body.session_id, "reset": True})

    return app


def _is_loopback_host(host: str) -> bool:
    """识别 IP loopback 与精确 localhost 名称。"""

    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _environment_variable_name(value: str) -> str:
    """只允许环境变量名，拒绝误把凭据值传入命令行。"""

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise argparse.ArgumentTypeError("必须提供环境变量名称")
    return value


def main(argv: list[str] | None = None) -> int:
    """启动单进程本地网页；远程监听必须显式接受无认证风险。"""

    parser = argparse.ArgumentParser(
        description="Kaiji 本地聊天网页",
        epilog=(
            "远程监听没有认证；只有在可信网络中并显式指定 --allow-remote 时使用。"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="允许绑定非 loopback 地址；服务没有认证，只能用于可信网络",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="模型 API 基础地址")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="模型名称")
    parser.add_argument(
        "--api-key-env",
        type=_environment_variable_name,
        default=DEFAULT_API_KEY_ENV,
        help="保存 API Key 的环境变量名称，不是 API Key 值",
    )
    parser.add_argument(
        "--log-path",
        default="artifacts/web/chat-log.jsonl",
        help="服务器本地 JSONL 日志路径",
    )
    args = parser.parse_args(argv)
    if not _is_loopback_host(args.host) and not args.allow_remote:
        parser.error("非 loopback host 必须同时指定 --allow-remote；服务没有认证")
    if not 1 <= args.port <= 65535:
        parser.error("port 必须在 1 到 65535 之间")
    try:
        config = OpenAICompatibleConfig(
            base_url=args.base_url,
            model=args.model,
            api_key_env=args.api_key_env,
            temperature=0,
            max_tokens=DEFAULT_MAX_TOKENS,
            system_prompt=SYSTEM_PROMPT,
            thinking="disabled",
        )
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    app = create_app(
        runtime_factory=_build_runtime_factory(config),
        log_path=args.log_path,
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
