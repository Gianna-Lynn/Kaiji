"""验证本地聊天 API、隐私边界、日志可靠性和会话并发。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest

import kaiji.chat as chat
from kaiji import web
from kaiji.demo import build_registry
from kaiji.models import (
    Message,
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ModelUsage,
    ProviderErrorCode,
    ProviderFailure,
    ToolCall,
)
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolExecutor


class CallbackProvider:
    """将收到的上下文交给测试回调，并记录深拷贝后的调用输入。"""

    def __init__(self, decide) -> None:
        self.callback = decide
        self.calls: list[list[Message]] = []
        self._lock = threading.Lock()

    def decide(self, messages: list[Message]) -> ModelCallResult:
        with self._lock:
            self.calls.append([message.model_copy(deep=True) for message in messages])
        return self.callback(messages)


def _call(
    *,
    answer: str | None = None,
    failure: ProviderFailure | None = None,
    tool_call: ToolCall | None = None,
    model: str = "test-model",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
) -> ModelCallResult:
    """构造一次带有确定 Token 统计的本地模型结果。"""

    action = (
        ModelAction(kind="tool_call", tool_call=tool_call)
        if tool_call is not None
        else ModelAction(kind="final_answer", final_answer=answer)
    )
    return ModelCallResult(
        action=action,
        failure=failure,
        metadata=ModelCallMetadata(
            model=model,
            latency_ms=1,
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
        ),
    )


def _app(provider_factory, *, log_path: Path, **kwargs):
    """构造完全离线、可注入 Provider 的测试应用。"""

    return web.create_app(
        runtime_factory=lambda: AgentRuntime(
            provider_factory(), ToolExecutor(build_registry())
        ),
        log_path=log_path,
        **kwargs,
    )


def test_page_and_static_assets_are_served_from_fixed_paths(tmp_path: Path) -> None:
    provider = CallbackProvider(lambda _: _call(answer="ok"))
    with TestClient(_app(lambda: provider, log_path=tmp_path / "audit.jsonl")) as client:
        page = client.get("/")
        script = client.get("/static/app.js")
        style = client.get("/static/style.css")

    assert page.status_code == script.status_code == style.status_code == 200
    assert "Kaiji 本地聊天" in page.text
    assert "localStorage" in script.text
    assert "innerHTML" not in script.text
    assert "textContent" in script.text
    assert "isApiErrorPayload(data)" in script.text
    assert "请求失败。请检查消息格式" in script.text
    assert "服务异常，请稍后重试" in script.text
    assert "连接失败。请确认本地服务" in script.text
    assert "../" not in page.text


def test_second_turn_and_history_refresh_preserve_only_public_messages(
    tmp_path: Path,
) -> None:
    provider = CallbackProvider(
        lambda messages: _call(answer=f"回答第 {sum(m.role == 'user' for m in messages)} 轮")
    )
    with TestClient(_app(lambda: provider, log_path=tmp_path / "audit.jsonl")) as client:
        first = client.post(
            "/api/chat", json={"session_id": "local_1", "message": "第一问"}
        )
        second = client.post(
            "/api/chat", json={"session_id": "local_1", "message": "第二问"}
        )
        history = client.get("/api/history", params={"session_id": "local_1"})

    assert first.status_code == second.status_code == 200
    assert second.json()["answer"] == "回答第 2 轮"
    assert [(m["role"], m["content"]) for m in history.json()["messages"]] == [
        ("user", "第一问"),
        ("assistant", "回答第 1 轮"),
        ("user", "第二问"),
        ("assistant", "回答第 2 轮"),
    ]
    assert "trace" not in history.text.casefold()


def test_sessions_are_isolated_and_reset_affects_only_selected_session(
    tmp_path: Path,
) -> None:
    provider = CallbackProvider(lambda messages: _call(answer=f"答复:{messages[-1].content}"))
    with TestClient(_app(lambda: provider, log_path=tmp_path / "audit.jsonl")) as client:
        client.post("/api/chat", json={"session_id": "alice", "message": "A"})
        client.post("/api/chat", json={"session_id": "bob", "message": "B"})
        reset = client.post("/api/reset", json={"session_id": "alice"})
        alice = client.get("/api/history", params={"session_id": "alice"})
        bob = client.get("/api/history", params={"session_id": "bob"})

    assert reset.status_code == 200 and reset.json()["reset"] is True
    assert alice.json()["messages"] == []
    assert [message["content"] for message in bob.json()["messages"]] == ["B", "答复:B"]


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "../escape", "message": "x"},
        {"session_id": "bad/id", "message": "x"},
        {"session_id": "sk-supersecret123456", "message": "x"},
        {"session_id": "ok", "message": ""},
        {"session_id": "ok", "message": "  \t"},
        {"session_id": "ok", "message": "x" * 4001},
        {"session_id": "ok", "message": "x", "api_key": "do-not-echo"},
    ],
)
def test_invalid_chat_requests_are_rejected_without_echoing_input(
    payload, tmp_path: Path
) -> None:
    provider = CallbackProvider(lambda _: _call(answer="unused"))
    with TestClient(_app(lambda: provider, log_path=tmp_path / "audit.jsonl")) as client:
        response = client.post("/api/chat", json=payload)

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request"}
    assert "do-not-echo" not in response.text
    assert provider.calls == []


def test_history_and_reset_reject_invalid_session_ids(tmp_path: Path) -> None:
    provider = CallbackProvider(lambda _: _call(answer="unused"))
    with TestClient(_app(lambda: provider, log_path=tmp_path / "audit.jsonl")) as client:
        history = client.get("/api/history", params={"session_id": "../secret"})
        reset = client.post("/api/reset", json={"session_id": "../secret"})
        key_history = client.get(
            "/api/history", params={"session_id": "sk-supersecret123456"}
        )

    assert history.status_code == reset.status_code == 422
    assert history.json() == reset.json() == {"error": "invalid_request"}
    assert key_history.status_code == 422
    assert key_history.json() == {"error": "invalid_request"}
    assert "sk-supersecret123456" not in key_history.text


def test_failed_model_result_is_sanitized_and_not_committed(tmp_path: Path) -> None:
    provider = CallbackProvider(
        lambda _: ModelCallResult(
            failure=ProviderFailure(
                code=ProviderErrorCode.RATE_LIMITED,
                message="secret provider body and bearer value",
                retryable=True,
            ),
            metadata=ModelCallMetadata(model="test-model", latency_ms=2),
        )
    )
    log_path = tmp_path / "audit.jsonl"
    with TestClient(_app(lambda: provider, log_path=log_path)) as client:
        failed = client.post(
            "/api/chat", json={"session_id": "failed", "message": "private prompt"}
        )
        history = client.get("/api/history", params={"session_id": "failed"})

    assert failed.status_code == 502
    assert failed.json()["failure_code"] == "rate_limited"
    assert failed.json()["answer"] is None
    assert history.json()["messages"] == []
    assert "secret provider body" not in failed.text
    assert "bearer value" not in failed.text.casefold()
    assert "Trace" not in failed.text
    rendered_log = log_path.read_text(encoding="utf-8")
    assert "private prompt" not in rendered_log
    assert "secret provider body" not in rendered_log
    assert "bearer value" not in rendered_log.casefold()


def test_default_audit_log_omits_content_and_sensitive_fields(tmp_path: Path) -> None:
    provider = CallbackProvider(lambda _: _call(answer="sensitive assistant body"))
    log_path = tmp_path / "audit.jsonl"
    with TestClient(_app(lambda: provider, log_path=log_path)) as client:
        response = client.post(
            "/api/chat",
            json={"session_id": "private", "message": "sensitive user body"},
        )

    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert record["log_content_enabled"] is False
    assert "user_content" not in record and "assistant_content" not in record
    assert record["input_tokens"] is None
    assert record["output_tokens"] is None
    assert record["total_tokens"] is None
    assert response.json()["tool_success"] is None
    assert record["tool_success"] is None
    assert "sensitive user body" not in log_path.read_text(encoding="utf-8")
    assert "sensitive assistant body" not in log_path.read_text(encoding="utf-8")
    assert not any("key" in name.casefold() or "header" in name.casefold() for name in record)
    assert "Authorization" not in log_path.read_text(encoding="utf-8")
    assert "Bearer" not in log_path.read_text(encoding="utf-8")
    assert log_path.read_bytes().count(b"\n") == 1


def test_content_log_requires_exact_lowercase_true(tmp_path: Path, monkeypatch) -> None:
    provider = CallbackProvider(lambda _: _call(answer="saved answer"))
    monkeypatch.setenv("KAIJI_LOG_CONTENT", "true")
    log_path = tmp_path / "content.jsonl"
    with TestClient(_app(lambda: provider, log_path=log_path)) as client:
        response = client.post(
            "/api/chat", json={"session_id": "content", "message": "saved question"}
        )
    record = json.loads(log_path.read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert record["log_content_enabled"] is True
    assert record["user_content"] == "saved question"
    assert record["assistant_content"] == "saved answer"


def test_content_logging_redacts_recognizable_credentials_and_auth_headers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider = CallbackProvider(lambda _: _call(answer="safe response"))
    monkeypatch.setenv("KAIJI_LOG_CONTENT", "true")
    log_path = tmp_path / "content.jsonl"
    message = (
        "Authorization: Bearer header-secret-value\n"
        '"api_key": "generic-secret-value"\n'
        "sk-deepseeksecret123456789"
    )
    with TestClient(_app(lambda: provider, log_path=log_path)) as client:
        response = client.post(
            "/api/chat", json={"session_id": "redact", "message": message}
        )

    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert "header-secret-value" not in log_path.read_text(encoding="utf-8")
    assert "generic-secret-value" not in log_path.read_text(encoding="utf-8")
    assert "sk-deepseeksecret" not in log_path.read_text(encoding="utf-8")
    assert "[REDACTED HTTP HEADER]" in record["user_content"]
    assert "[REDACTED]" in record["user_content"]


@pytest.mark.parametrize("value", ["1", "yes", "TRUE", "True", " true", "true "])
def test_content_log_rejects_noncanonical_true_values(value: str, tmp_path: Path, monkeypatch) -> None:
    provider = CallbackProvider(lambda _: _call(answer="not saved"))
    monkeypatch.setenv("KAIJI_LOG_CONTENT", value)
    log_path = tmp_path / "audit.jsonl"
    with TestClient(_app(lambda: provider, log_path=log_path)) as client:
        client.post("/api/chat", json={"session_id": "strict", "message": "not saved"})

    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["log_content_enabled"] is False
    assert "user_content" not in record
    assert "not saved" not in log_path.read_text(encoding="utf-8")


def test_log_failure_keeps_answer_and_returns_explicit_warning(tmp_path: Path) -> None:
    class BrokenLogger:
        def append(self, record: web.AuditRecord) -> None:
            raise OSError("private filesystem path C:/secret")

    provider = CallbackProvider(lambda _: _call(answer="answer survives"))
    app = _app(lambda: provider, log_path=tmp_path / "unused.jsonl", logger=BrokenLogger())
    with TestClient(app) as client:
        response = client.post(
            "/api/chat", json={"session_id": "logfail", "message": "question"}
        )

    assert response.status_code == 207
    assert response.json()["answer"] == "answer survives"
    assert response.json()["log_persisted"] is False
    assert response.json()["logging_warning"] == "logging_failed"
    assert "C:/secret" not in response.text


def test_token_peak_cost_and_tool_summary_aggregate_every_model_call(tmp_path: Path) -> None:
    results = [
        _call(
            tool_call=ToolCall(
                call_id="discount-call",
                name="calculate_discount",
                arguments={"price": 100, "percent": 10},
            ),
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        ),
        _call(
            answer="折后价 90",
            input_tokens=200,
            output_tokens=10,
            total_tokens=210,
        ),
    ]
    provider = CallbackProvider(lambda _: results.pop(0))
    log_path = tmp_path / "audit.jsonl"
    with TestClient(_app(lambda: provider, log_path=log_path)) as client:
        response = client.post(
            "/api/chat", json={"session_id": "tool", "message": "算九折"}
        )
        history = client.get("/api/history", params={"session_id": "tool"})

    data = response.json()
    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert data["input_tokens"] == record["input_tokens"] == 300
    assert data["output_tokens"] == record["output_tokens"] == 30
    assert data["total_tokens"] == record["total_tokens"] == 330
    assert data["estimated_peak_cost_cny"] == "0.000840"
    assert record["estimated_peak_cost_cny"] == "0.000840"
    assert data["tool_names"] == record["tool_names"] == ["calculate_discount"]
    assert data["tool_success"] is record["tool_success"] is True
    assert [message["role"] for message in history.json()["messages"]] == [
        "user",
        "assistant",
    ]
    assert all("call_id" not in message and "tool_result" not in message for message in history.json()["messages"])


def test_failed_tool_is_summarized_without_returning_tool_error_text(tmp_path: Path) -> None:
    provider = CallbackProvider(
        lambda _: _call(
            tool_call=ToolCall(
                call_id="unknown-tool-call",
                name="private_unknown_tool",
                arguments={},
            )
        )
    )
    with TestClient(_app(lambda: provider, log_path=tmp_path / "audit.jsonl")) as client:
        response = client.post(
            "/api/chat", json={"session_id": "tool-fail", "message": "invoke it"}
        )

    assert response.status_code == 502
    assert response.json()["tool_names"] == ["private_unknown_tool"]
    assert response.json()["tool_success"] is False
    assert response.json()["failure_code"] == "unknown_tool"
    assert "未知工具" not in response.text


def test_same_session_turns_are_serialized_and_history_does_not_interleave(
    tmp_path: Path,
) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    call_index = 0
    call_guard = threading.Lock()

    def decide(messages: list[Message]) -> ModelCallResult:
        nonlocal call_index
        with call_guard:
            current = call_index
            call_index += 1
        if current == 0:
            first_started.set()
            assert release_first.wait(timeout=3)
            return _call(answer="first answer")
        return _call(answer="second answer")

    provider = CallbackProvider(decide)
    app = _app(lambda: provider, log_path=tmp_path / "audit.jsonl")
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            client.post,
            "/api/chat",
            json={"session_id": "serialized", "message": "first question"},
        )
        assert first_started.wait(timeout=2)
        second = pool.submit(
            client.post,
            "/api/chat",
            json={"session_id": "serialized", "message": "second question"},
        )
        time.sleep(0.05)
        assert not second.done()
        release_first.set()
        assert first.result(timeout=3).status_code == 200
        assert second.result(timeout=3).status_code == 200
        history = client.get("/api/history", params={"session_id": "serialized"})

    assert [message.content for message in provider.calls[1]] == [
        "first question",
        "first answer",
        "second question",
    ]
    assert [(m["role"], m["content"]) for m in history.json()["messages"]] == [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
        ("assistant", "second answer"),
    ]


def test_non_loopback_requires_explicit_remote_opt_in(monkeypatch, capsys) -> None:
    called = []
    monkeypatch.setattr(web.uvicorn, "run", lambda *args, **kwargs: called.append(kwargs))

    with pytest.raises(SystemExit) as error:
        web.main(["--host", "0.0.0.0"])
    assert error.value.code == 2
    assert "没有认证" in capsys.readouterr().err
    assert called == []

    assert web.main(["--host", "0.0.0.0", "--allow-remote"]) == 0
    assert called == [{"host": "0.0.0.0", "port": 8000, "workers": 1}]


def test_default_web_and_both_cli_provider_configs_have_model_and_token_cap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configs = []

    def capture_config(config):
        configs.append(config)
        return lambda: AgentRuntime(
            CallbackProvider(lambda _: _call(answer="unused")),
            ToolExecutor(build_registry()),
        )

    monkeypatch.setattr(web, "_build_runtime_factory", capture_config)
    web.create_app(log_path=tmp_path / "default.jsonl")
    assert configs[0].model == "deepseek-flash"
    assert configs[0].max_tokens == 512

    monkeypatch.setattr(web.uvicorn, "run", lambda *args, **kwargs: None)
    assert web.main([]) == 0
    assert configs[1].model == "deepseek-flash"
    assert configs[1].max_tokens == 512

    monkeypatch.setattr(chat, "_build_runtime_factory", capture_config)
    assert chat.main([], input_stream=StringIO(""), output_stream=StringIO()) == 0
    assert configs[2].model == "deepseek-flash"
    assert configs[2].max_tokens == 512


def test_unknown_logger_fields_and_naive_timestamp_are_rejected() -> None:
    common = {
        "run_id": "run",
        "session_id": "session",
        "timestamp": "2026-09-18T00:00:00",
        "latency_ms": 1.0,
        "stop_reason": "completed",
        "failure_code": None,
        "model": "test",
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_peak_cost_cny": Decimal("0"),
        "tool_names": [],
        "tool_success": True,
        "log_content_enabled": False,
    }
    with pytest.raises(ValueError):
        web.AuditRecord.model_validate({**common, "trace": {"private": "raw"}})
    with pytest.raises(ValueError):
        web.AuditRecord.model_validate(common)
