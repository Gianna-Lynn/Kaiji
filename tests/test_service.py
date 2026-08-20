"""验证 FastAPI 鉴权、工具权限收缩和无状态运行响应契约。"""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from kaiji.demo import ScriptedDecisionProvider, build_registry
from kaiji.models import ModelAction
from kaiji.runtime import AgentRuntime
from kaiji.service import ServiceSettings, create_app
from kaiji.tools import ToolExecutor


def _settings() -> ServiceSettings:
    """构造不依赖真实环境秘密的测试配置。"""

    return ServiceSettings(
        api_token=SecretStr("test-token"),
        allowed_tools=frozenset({"local_search", "get_profile"}),
        provider_base_url="https://provider.invalid/v1",
        provider_model="mock-model",
        provider_api_key_env="PATH",
    )


def _factory(calls: list[frozenset[str]]):
    """记录每次授权集合并返回新的确定性 Runtime。"""

    def build(tools: frozenset[str]) -> AgentRuntime:
        calls.append(tools)
        return AgentRuntime(
            ScriptedDecisionProvider(
                [ModelAction(kind="final_answer", final_answer="ok")]
            ),
            ToolExecutor(build_registry()),
        )

    return build


def test_live_endpoint_does_not_require_authentication() -> None:
    client = TestClient(create_app(settings=_settings(), runtime_factory=_factory([])))

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_run_requires_exact_bearer_token() -> None:
    client = TestClient(create_app(settings=_settings(), runtime_factory=_factory([])))

    assert client.post("/v1/runs", json={"question": "hello"}).status_code == 401
    response = client.post(
        "/v1/runs",
        headers={"Authorization": "Bearer wrong"},
        json={"question": "hello"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

    non_ascii = client.post(
        "/v1/runs",
        headers=[(b"authorization", b"Bearer \xff")],
        json={"question": "hello"},
    )
    assert non_ascii.status_code == 401


def test_caller_can_shrink_but_cannot_expand_tool_permissions() -> None:
    calls: list[frozenset[str]] = []
    client = TestClient(
        create_app(settings=_settings(), runtime_factory=_factory(calls))
    )
    headers = {"Authorization": "Bearer test-token"}

    denied = client.post(
        "/v1/runs",
        headers=headers,
        json={"question": "hello", "requested_tools": ["calculate_discount"]},
    )
    allowed = client.post(
        "/v1/runs",
        headers=headers,
        json={"question": "hello", "requested_tools": ["get_profile"]},
    )

    assert denied.status_code == 403
    assert calls == [frozenset({"get_profile"})]
    assert allowed.status_code == 200
    assert allowed.json()["result"]["answer"] == "ok"
    assert allowed.json()["spans"][0]["name"] == "kaiji.agent.run"


def test_each_request_receives_a_new_runtime_and_run_id() -> None:
    calls: list[frozenset[str]] = []
    client = TestClient(
        create_app(settings=_settings(), runtime_factory=_factory(calls))
    )
    headers = {"Authorization": "Bearer test-token"}

    first = client.post("/v1/runs", headers=headers, json={"question": "one"})
    second = client.post("/v1/runs", headers=headers, json={"question": "two"})

    assert first.status_code == second.status_code == 200
    assert first.json()["run_id"] != second.json()["run_id"]
    assert calls == [_settings().allowed_tools, _settings().allowed_tools]


def test_unconfigured_authentication_fails_closed() -> None:
    settings = _settings().model_copy(update={"api_token": None})
    client = TestClient(create_app(settings=settings, runtime_factory=_factory([])))

    response = client.post(
        "/v1/runs",
        headers={"Authorization": "Bearer anything"},
        json={"question": "hello"},
    )

    assert response.status_code == 503


def test_ready_reports_unknown_server_tool_without_external_call() -> None:
    settings = _settings().model_copy(
        update={"allowed_tools": frozenset({"not_registered"})}
    )
    client = TestClient(create_app(settings=settings, runtime_factory=_factory([])))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "服务尚未就绪"}
    assert "not_registered" not in response.text


def test_run_rejects_incomplete_server_configuration_before_factory() -> None:
    settings = _settings().model_copy(update={"provider_model": None})
    calls: list[frozenset[str]] = []
    client = TestClient(
        create_app(settings=settings, runtime_factory=_factory(calls))
    )

    response = client.post(
        "/v1/runs",
        headers={"Authorization": "Bearer test-token"},
        json={"question": "hello"},
    )

    assert response.status_code == 503
    assert calls == []
