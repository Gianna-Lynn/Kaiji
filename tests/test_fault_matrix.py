"""验证故障矩阵的恢复、耗尽、协议边界和状态隔离。"""

from __future__ import annotations

import time

import pytest
from kaiji.adapters.azusa import AzusaHttpAdapter
from kaiji.azusa_tools import build_azusa_registry
from kaiji.demo import DiscountArgs, DiscountResult
from kaiji.faults import (
    HttpFaultStep,
    ScriptedHttpTransport,
    ScriptedToolHandler,
    ToolFaultStep,
)
from kaiji.models import ToolCall, ToolErrorCode
from kaiji.tools import ToolDefinition, ToolExecutor, ToolRegistry


def _execute(steps: list[ToolFaultStep], *, timeout: float = 0.1):
    handler = ScriptedToolHandler(steps)
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="calculate_discount",
            description="故障测试工具",
            arguments_model=DiscountArgs,
            result_model=DiscountResult,
            handler=handler,
            timeout_seconds=timeout,
            max_retries=1,
        )
    )
    result = ToolExecutor(registry).execute(
        ToolCall(
            call_id="fault-call",
            name="calculate_discount",
            arguments={"price": 100, "percent": 10},
        )
    )
    return result, handler


def test_transient_failure_recovers_on_second_attempt() -> None:
    result, handler = _execute(
        [
            ToolFaultStep(kind="transient"),
            ToolFaultStep(kind="success", output={"final_price": 90}),
        ]
    )
    assert result.ok is True
    assert result.attempts == 2
    assert handler.attempts == 2


def test_persistent_failure_stops_at_retry_budget() -> None:
    result, handler = _execute(
        [ToolFaultStep(kind="transient"), ToolFaultStep(kind="transient")]
    )
    assert result.ok is False
    assert result.attempts == 2
    assert handler.attempts == 2
    assert result.error.retryable is True


def test_non_retryable_and_schema_drift_do_not_consume_retry() -> None:
    permanent, permanent_handler = _execute(
        [ToolFaultStep(kind="permanent"), ToolFaultStep(kind="success")]
    )
    drift, drift_handler = _execute(
        [ToolFaultStep(kind="invalid_result"), ToolFaultStep(kind="success")]
    )
    assert permanent.attempts == permanent_handler.attempts == 1
    assert permanent.error.retryable is False
    assert drift.attempts == drift_handler.attempts == 1
    assert drift.error.code == ToolErrorCode.INVALID_RESULT


def test_tool_timeout_stops_after_bounded_attempts() -> None:
    result, handler = _execute(
        [
            ToolFaultStep(kind="delay", delay_seconds=0.03),
            ToolFaultStep(kind="delay", delay_seconds=0.03),
        ],
        timeout=0.001,
    )
    assert result.ok is False
    assert result.error.code == ToolErrorCode.TIMEOUT
    assert result.attempts == 2
    assert handler.attempts == 2
    time.sleep(0.04)  # 让超时线程结束，避免影响后续用例进程退出。


@pytest.mark.parametrize("status", [429, 503])
def test_http_429_and_503_recover_once(status: int) -> None:
    scripted = ScriptedHttpTransport(
        [
            HttpFaultStep(kind="status", status_code=status, payload={}),
            HttpFaultStep(
                kind="json",
                payload={
                    "hits": [{"chunk_id": "kaiji-001", "text": "Kaiji 使用工具。"}],
                    "mode": "hybrid",
                },
            ),
        ]
    )
    adapter = AzusaHttpAdapter("https://azusa.invalid", transport=scripted.transport)
    result = ToolExecutor(build_azusa_registry(adapter)).execute(
        ToolCall(
            call_id="search-1",
            name="search_azusa",
            arguments={"query": "Kaiji", "k": 1, "mode": "hybrid"},
        )
    )
    assert result.ok is True
    assert result.attempts == 2
    assert scripted.attempts == 2
    adapter.close()


@pytest.mark.parametrize("kind", ["network", "invalid_json"])
def test_network_and_invalid_json_are_bounded(kind: str) -> None:
    scripted = ScriptedHttpTransport(
        [HttpFaultStep(kind=kind), HttpFaultStep(kind=kind)]
    )
    adapter = AzusaHttpAdapter("https://azusa.invalid", transport=scripted.transport)
    result = ToolExecutor(build_azusa_registry(adapter)).execute(
        ToolCall(
            call_id="search-1",
            name="search_azusa",
            arguments={"query": "Kaiji", "k": 1, "mode": "hybrid"},
        )
    )
    assert result.ok is False
    assert result.attempts == 2
    assert scripted.attempts == 2
    adapter.close()


def test_chunk_id_mismatch_never_enters_success_evidence() -> None:
    scripted = ScriptedHttpTransport(
        [
            HttpFaultStep(
                kind="json",
                payload={"chunk_id": "wrong-id", "text": "错误证据"},
            ),
            HttpFaultStep(
                kind="json",
                payload={"chunk_id": "wrong-id", "text": "错误证据"},
            ),
        ]
    )
    adapter = AzusaHttpAdapter("https://azusa.invalid", transport=scripted.transport)
    result = ToolExecutor(build_azusa_registry(adapter)).execute(
        ToolCall(
            call_id="chunk-1",
            name="get_chunk_by_id",
            arguments={"chunk_id": "expected-id"},
        )
    )
    assert result.ok is False
    assert result.output is None
    assert result.error.code == ToolErrorCode.EXECUTION_FAILED
    adapter.close()


def test_fault_injectors_do_not_share_consumed_state() -> None:
    steps = [ToolFaultStep(kind="success", output={"final_price": 90})]
    first = ScriptedToolHandler(steps)
    second = ScriptedToolHandler(steps)
    first(DiscountArgs(price=100, percent=10))
    assert first.attempts == 1
    assert second.attempts == 0
