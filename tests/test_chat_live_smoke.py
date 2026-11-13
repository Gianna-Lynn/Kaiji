"""验证 DeepSeek 冒烟测试的预算闸门和离线模拟流程。"""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
import json

import httpx
import pytest

from kaiji.chat_live_smoke import (
    API_KEY_ENV,
    BUDGET_CNY,
    MAX_BODY_BYTES,
    MAX_REQUESTS,
    BudgetGuardTransport,
    BudgetLedger,
    _fee,
    _report,
    main,
    offline_preflight,
)


class CountingTransport(httpx.BaseTransport):
    """记录下游实际收到的 body 数量和 transport 关闭次数。"""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self.close_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(request.read())
        return httpx.Response(200, content=b"ok", request=request)

    def close(self) -> None:
        self.close_count += 1


def _post_with_guard(guard: BudgetGuardTransport, body: bytes) -> None:
    """用短生命周期 Client 发送一个本地请求，复现 Provider 的关闭方式。"""

    with httpx.Client(transport=guard) as client:
        client.post("https://mock.invalid/chat/completions", content=body)


def test_sixth_request_is_refused_before_downstream() -> None:
    """第六次尝试被本地拒绝，downstream 只观察到前五个请求。"""

    downstream = CountingTransport()
    ledger = BudgetLedger()
    guard = BudgetGuardTransport(downstream, ledger)

    for index in range(MAX_REQUESTS):
        _post_with_guard(guard, f"body-{index}".encode())

    with pytest.raises(httpx.TransportError, match="request_limit_exceeded"):
        _post_with_guard(guard, b"sixth")

    assert len(downstream.bodies) == MAX_REQUESTS
    assert ledger.request_count == MAX_REQUESTS
    assert [record.sent for record in ledger.records] == [True] * 5 + [False]
    assert ledger.records[-1].refusal_reason == "request_limit_exceeded"


def test_body_limit_rejects_before_downstream_and_accepts_exact_boundary() -> None:
    """精确 8192 bytes 可通过，超出一个 byte 的 body 不会抵达下游。"""

    downstream = CountingTransport()
    ledger = BudgetLedger(max_requests=2)
    guard = BudgetGuardTransport(downstream, ledger)

    _post_with_guard(guard, b"x" * MAX_BODY_BYTES)
    with pytest.raises(httpx.TransportError, match="body_too_large"):
        _post_with_guard(guard, b"x" * (MAX_BODY_BYTES + 1))

    assert downstream.bodies == [b"x" * MAX_BODY_BYTES]
    assert ledger.records[0].body_bytes == MAX_BODY_BYTES
    assert ledger.records[0].reserved_input_tokens == 2 * MAX_BODY_BYTES + 1024
    assert ledger.records[1].sent is False
    assert ledger.records[1].body_bytes == MAX_BODY_BYTES + 1


def test_decimal_reservation_accepts_exact_budget_and_refuses_over_budget() -> None:
    """预约金额使用 Decimal，精确等于预算时允许，略低预算时先行拒绝。"""

    body = b"z"
    reservation = _fee(2 * len(body) + 1024, 256)
    assert type(reservation) is Decimal

    exact_downstream = CountingTransport()
    exact = BudgetLedger(budget_cny=reservation, max_requests=2)
    exact_guard = BudgetGuardTransport(exact_downstream, exact)
    _post_with_guard(exact_guard, body)
    with pytest.raises(httpx.TransportError, match="budget_exceeded"):
        _post_with_guard(exact_guard, body)
    assert exact.reserved_fee_cny == reservation
    assert exact.reserved_fee_cny <= exact.budget_cny
    assert len(exact_downstream.bodies) == 1
    assert exact.records[-1].refusal_reason == "budget_exceeded"

    below_downstream = CountingTransport()
    below = BudgetLedger(budget_cny=reservation - Decimal("0.000001"))
    below_guard = BudgetGuardTransport(below_downstream, below)
    with pytest.raises(httpx.TransportError, match="budget_exceeded"):
        _post_with_guard(below_guard, body)
    assert below_downstream.bodies == []
    assert below.request_count == 0


def test_largest_five_request_reservations_fit_budget_exactly_as_calculated() -> None:
    """五次最大 body 的保守预约仍低于 ¥0.20，计算全程使用 Decimal。"""

    preflight = offline_preflight()
    max_input_tokens = 2 * MAX_BODY_BYTES + 1024
    per_request = _fee(max_input_tokens, 256)
    total = per_request * MAX_REQUESTS

    assert type(total) is Decimal
    assert per_request == Decimal("0.036864")
    assert total == Decimal("0.184320")
    assert total < BUDGET_CNY
    assert preflight["five_request_worst_case_cny"] == "0.184320"
    assert preflight["remaining_headroom_cny"] == "0.015680"


def test_missing_usage_marks_actual_fee_unknown_but_keeps_reservation() -> None:
    """缺少 usage 时实际费用标为 unknown，预先预约仍被保留。"""

    downstream = CountingTransport()
    ledger = BudgetLedger()
    guard = BudgetGuardTransport(downstream, ledger)
    _post_with_guard(guard, b"request without usage")

    report = _report(guard, [], "usage_not_attached", run_id="offline-test")

    assert report["actual_peak_fee_estimate_cny"] is None
    assert report["actual_peak_fee_estimate_status"] == "unknown_usage_missing"
    assert Decimal(str(report["reserved_fee_cny"])) > 0
    assert ledger.request_count == 1


def test_guard_stays_open_between_provider_style_clients_then_closes_once() -> None:
    """Provider 每回合关闭 Client 不会提前关闭共享下游，任务收尾只关一次。"""

    downstream = CountingTransport()
    guard = BudgetGuardTransport(downstream, BudgetLedger(max_requests=2))

    _post_with_guard(guard, b"first")
    _post_with_guard(guard, b"second")
    assert downstream.bodies == [b"first", b"second"]
    assert downstream.close_count == 0

    guard.close_downstream()
    guard.close_downstream()
    assert downstream.close_count == 1


def test_cli_without_execute_only_prints_preflight_and_never_builds_transport() -> None:
    """缺少显式开关时，即使提供 transport factory 也只输出离线预检。"""

    output = StringIO()
    constructed = False

    def factory() -> httpx.BaseTransport:
        nonlocal constructed
        constructed = True
        return CountingTransport()

    assert main([], output_stream=output, transport_factory=factory) == 0
    assert constructed is False
    assert "offline budget preflight (not executed)" in output.getvalue()
    assert "five_request_worst_case_cny: 0.184320" in output.getvalue()
    assert "HTTP requests sent: 0" in output.getvalue()


def _completion(content: str, *, prompt_tokens: int = 20) -> httpx.Response:
    """构造带用量的确定性兼容响应。"""

    return httpx.Response(
        200,
        json={
            "model": "deepseek-flash",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 3,
                "total_tokens": prompt_tokens + 3,
            },
        },
    )


def test_execute_flag_runs_full_smoke_only_through_mock_and_writes_redacted_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """两组 Live 场景通过 MockTransport 跑通且报告不含鉴权或原始消息。"""

    # 这是专供单测使用的假值，绝不连接真实 transport。
    monkeypatch.setenv(API_KEY_ENV, "test-only-never-real-key")
    captured: list[tuple[dict, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        captured.append((payload, request.headers.get("authorization")))
        assert request.headers["authorization"] == "Bearer test-only-never-real-key"
        assert payload["model"] == "deepseek-flash"
        assert payload["temperature"] == 0
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["stream"] is False
        assert payload["max_tokens"] == 256
        index = len(captured)
        if index in (1, 2):
            if index == 2:
                assert any("KJ-4827" in message.get("content", "") for message in payload["messages"])
            return _completion("验证码是 KJ-4827。")
        if index == 3:
            assert "calculate_discount" in {tool["function"]["name"] for tool in payload["tools"]}
            return httpx.Response(
                200,
                json={
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-discount",
                                        "type": "function",
                                        "function": {
                                            "name": "calculate_discount",
                                            "arguments": '{"price":100,"percent":10}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 30,
                        "completion_tokens": 8,
                        "total_tokens": 38,
                    },
                },
            )
        if index == 4:
            messages = payload["messages"]
            assert messages[-2]["tool_calls"][0]["id"] == "call-discount"
            assert messages[-1]["tool_call_id"] == "call-discount"
            assert '"final_price":"90"' in messages[-1]["content"]
            return _completion("90")
        assert index == 5
        assert any("90" in str(message.get("content") or "") for message in payload["messages"])
        return _completion("90")

    output_root = tmp_path / "artifacts" / "chat-live-smoke"
    stdout = StringIO()
    result = main(
        ["--execute", "--output-dir", str(output_root)],
        output_stream=stdout,
        transport_factory=lambda: httpx.MockTransport(handler),
    )

    assert result == 0
    assert len(captured) == 5
    assert len({authorization for _, authorization in captured}) == 1
    report_files = list(output_root.glob("*/report.json"))
    assert len(report_files) == 1
    report_text = report_files[0].read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["passed"] is True
    assert report["request_count"] == 5
    assert Decimal(report["reserved_fee_cny"]) > 0
    assert report["actual_peak_fee_estimate_status"] == "estimated"
    assert all(attempt["sent"] for attempt in report["request_attempts"])
    assert all(attempt["body_bytes"] <= MAX_BODY_BYTES for attempt in report["request_attempts"])
    assert all(attempt["reserved_input_tokens"] == 2 * attempt["body_bytes"] + 1024 for attempt in report["request_attempts"])
    assert all(attempt["input_tokens"] is not None for attempt in report["request_attempts"])
    assert all(attempt["output_tokens"] is not None for attempt in report["request_attempts"])
    assert report["scenarios"][0]["passed"] is True
    assert report["scenarios"][0]["history_message_count_after_turn_2"] == 4
    assert report["scenarios"][1]["tool_result_verified"] is True
    assert report["scenarios"][1]["turns"][1]["fact_verified"] is True
    assert "test-only-never-real-key" not in report_text
    assert "authorization" not in report_text.lower()
    assert "KJ-4827" not in report_text
    assert "call-discount" not in report_text


def test_cli_execute_with_missing_key_does_not_construct_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """缺凭据时在 Transport 创建前停止，并且不创建报告目录。"""

    monkeypatch.delenv(API_KEY_ENV, raising=False)
    created = False

    def factory() -> httpx.BaseTransport:
        nonlocal created
        created = True
        return CountingTransport()

    output = StringIO()
    assert main(
        ["--execute", "--output-dir", str(tmp_path / "artifacts")],
        output_stream=output,
        transport_factory=factory,
    ) == 2
    assert created is False
    assert "is absent" in output.getvalue()
    assert not (tmp_path / "artifacts").exists()
