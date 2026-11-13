"""提供显式授权、逐请求硬预算保护的 DeepSeek 聊天真实冒烟测试。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Callable, TextIO
from uuid import uuid4

import httpx

from kaiji.chat import ChatSessionManager, SYSTEM_PROMPT
from kaiji.demo import build_registry
from kaiji.models import RunResult, StopReason
from kaiji.providers.openai_compatible import OpenAICompatibleConfig
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolExecutor


BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-flash"
API_KEY_ENV = "KAIJI_DS_SMOKE_CREDENTIAL"
TEMPERATURE = 0
MAX_TOKENS = 256
MAX_REQUESTS = 5
MAX_BODY_BYTES = 8192
BUDGET_CNY = Decimal("0.20")
INPUT_PRICE_CNY_PER_MILLION = Decimal("2")
OUTPUT_PRICE_CNY_PER_MILLION = Decimal("8")
MILLION = Decimal("1000000")


def _fee(input_tokens: int, output_tokens: int) -> Decimal:
    """按未命中缓存的峰值人民币单价计算费用，始终返回 Decimal。"""

    return (
        Decimal(input_tokens) * INPUT_PRICE_CNY_PER_MILLION / MILLION
        + Decimal(output_tokens) * OUTPUT_PRICE_CNY_PER_MILLION / MILLION
    )


@dataclass(slots=True)
class RequestRecord:
    """记录一次预算判断；只保存字节数、费用和用量，不保存请求内容。"""

    attempt: int
    body_bytes: int
    reserved_input_tokens: int
    reserved_output_tokens: int
    reserved_fee_cny: str
    sent: bool
    refusal_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    actual_peak_fee_cny: str | None = None


class BudgetLedger:
    """在每个请求下游发送前预约峰值费用并执行硬预算限制。"""

    def __init__(
        self,
        *,
        budget_cny: Decimal = BUDGET_CNY,
        max_requests: int = MAX_REQUESTS,
        max_body_bytes: int = MAX_BODY_BYTES,
        max_output_tokens: int = MAX_TOKENS,
    ) -> None:
        """初始化严格的预算账本；参数校验避免 bool 被当作整数上限。"""

        if not isinstance(budget_cny, Decimal) or not budget_cny.is_finite() or budget_cny < 0:
            raise ValueError("budget_cny 必须是非负有限 Decimal")
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("max_requests 必须是真正的正整数")
        if type(max_body_bytes) is not int or max_body_bytes < 1:
            raise ValueError("max_body_bytes 必须是真正的正整数")
        if type(max_output_tokens) is not int or max_output_tokens < 1:
            raise ValueError("max_output_tokens 必须是真正的正整数")
        self.budget_cny = budget_cny
        self.max_requests = max_requests
        self.max_body_bytes = max_body_bytes
        self.max_output_tokens = max_output_tokens
        self.reserved_fee_cny = Decimal("0")
        self.records: list[RequestRecord] = []
        self.stop_reason: str | None = None
        self._lock = threading.Lock()

    def authorize(self, body: bytes) -> RequestRecord:
        """检查真实 body 并先预约费用；拒绝时绝不允许请求抵达下游。"""

        if type(body) is not bytes:
            raise TypeError("HTTP 请求 body 必须是 bytes")
        body_size = len(body)
        input_tokens = 2 * body_size + 1024
        reservation = _fee(input_tokens, self.max_output_tokens)
        with self._lock:
            sent_count = sum(record.sent for record in self.records)
            reason = None
            if body_size > self.max_body_bytes:
                reason = "body_too_large"
            elif sent_count >= self.max_requests:
                reason = "request_limit_exceeded"
            elif self.reserved_fee_cny + reservation > self.budget_cny:
                reason = "budget_exceeded"
            record = RequestRecord(
                attempt=len(self.records) + 1,
                body_bytes=body_size,
                reserved_input_tokens=input_tokens,
                reserved_output_tokens=self.max_output_tokens,
                reserved_fee_cny=str(reservation),
                sent=reason is None,
                refusal_reason=reason,
            )
            self.records.append(record)
            if reason is None:
                self.reserved_fee_cny += reservation
            elif self.stop_reason is None:
                self.stop_reason = reason
            return record

    @property
    def request_count(self) -> int:
        """返回已经通过预算闸门并交给下游的请求数。"""

        return sum(record.sent for record in self.records)

    def attach_usage(self, index: int, usage: object) -> None:
        """把 Provider 返回的 Token 用量写到对应请求记录，不接受伪造零值。"""

        if index < 0 or index >= len(self.records):
            return
        record = self.records[index]
        if usage is None:
            return
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if type(input_tokens) is not int or type(output_tokens) is not int:
            return
        if input_tokens < 0 or output_tokens < 0:
            return
        record.input_tokens = input_tokens
        record.output_tokens = output_tokens
        record.total_tokens = total_tokens if type(total_tokens) is int else None
        record.actual_peak_fee_cny = str(_fee(input_tokens, output_tokens))


class BudgetGuardTransport(httpx.BaseTransport):
    """在请求离开本机前审核 HTTPX 已序列化 body 的预算保护层。"""

    def __init__(self, downstream: httpx.BaseTransport, ledger: BudgetLedger) -> None:
        """包装真正的 HTTPX transport；下游由本 harness 在所有场景结束后关闭。"""

        self._downstream = downstream
        self.ledger = ledger
        self.usage_queue: list[object] = []
        self._closed = False
        self._close_lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """审计精确序列化请求体；拒绝分支不调用下游 transport。"""

        body = request.read()
        record = self.ledger.authorize(body)
        if not record.sent:
            raise httpx.TransportError(
                f"本地请求预算拒绝：{record.refusal_reason}", request=request
            )
        return self._downstream.handle_request(request)

    def close(self) -> None:
        """兼容 Provider 的逐回合 Client 生命周期，不提前关闭共享下游。"""

        # OpenAICompatibleProvider 每次 decide 都关闭自己的短生命周期 Client。
        # 保护层由整个冒烟任务持有，必须等场景结束后才能关闭真正的下游。

    def close_downstream(self) -> None:
        """由 harness 在全部场景结束后关闭下游，重复调用安全。"""

        with self._close_lock:
            if not self._closed:
                self._closed = True
                self._downstream.close()


def offline_preflight() -> dict[str, str | int]:
    """返回不检查凭据、不构造 transport 的离线最坏情况预算摘要。"""

    max_input_tokens = 2 * MAX_BODY_BYTES + 1024
    per_request = _fee(max_input_tokens, MAX_TOKENS)
    total = per_request * MAX_REQUESTS
    return {
        "model": MODEL,
        "base_url": BASE_URL,
        "budget_cny": str(BUDGET_CNY),
        "max_requests": MAX_REQUESTS,
        "max_body_bytes": MAX_BODY_BYTES,
        "reserved_input_tokens_per_max_body": max_input_tokens,
        "max_output_tokens_per_request": MAX_TOKENS,
        "max_fee_per_max_body_request_cny": str(per_request),
        "five_request_worst_case_cny": str(total),
        "remaining_headroom_cny": str(BUDGET_CNY - total),
    }


def _contains_price_90(text: str) -> bool:
    """验证回答中独立出现十进制金额 90，避免匹配 190 等数字。"""

    return re.search(r"(?<!\d)90(?:\.0+)?(?!\d)", text) is not None


def _turn_summary(result: RunResult, *, fact_verified: bool) -> dict[str, object]:
    """生成不含模型原文、Trace、请求头或凭据的最小回合报告。"""

    return {
        "completed": result.stop_reason == StopReason.COMPLETED,
        "stop_reason": result.stop_reason.value,
        "fact_verified": fact_verified,
        "model_call_count": len(result.model_calls),
    }


def _verify_plain_fact_turn(result: RunResult, fact: str) -> bool:
    """要求纯文本事实轮恰好一次模型调用且不执行工具。"""

    return (
        result.stop_reason == StopReason.COMPLETED
        and fact in (result.answer or "")
        and len(result.model_calls) == 1
        and not result.successful_tool_results
        and len(result.messages) == 2
        and [message.role for message in result.messages] == ["user", "assistant"]
    )


def _verify_discount_result(result: RunResult) -> bool:
    """确认 Runtime 产生一条成功的折扣工具证据且最终金额为 90。"""

    if result.stop_reason != StopReason.COMPLETED or len(result.successful_tool_results) != 1:
        return False
    tool_result = result.successful_tool_results[0]
    if not tool_result.ok or tool_result.tool_name != "calculate_discount":
        return False
    if tool_result.output is None:
        return False
    try:
        final_price = Decimal(str(tool_result.output["final_price"]))
    except (KeyError, ValueError, TypeError):
        return False
    call_messages = [message for message in result.messages if message.tool_call is not None]
    tool_messages = [message for message in result.messages if message.tool_result is not None]
    return (
        final_price == Decimal("90")
        and len(call_messages) == 1
        and len(tool_messages) == 1
        and call_messages[0].tool_call.call_id == tool_result.call_id
        and tool_messages[0].tool_result.call_id == tool_result.call_id
        and tool_messages[0].tool_result.ok is True
    )


def _report(
    guard: BudgetGuardTransport,
    scenarios: list[dict[str, object]],
    stop_reason: str,
    *,
    run_id: str,
) -> dict[str, object]:
    """构造脱敏 JSON 报告；没有任何原始消息、HTTP 头或响应正文。"""

    records = guard.ledger.records
    for index, usage in enumerate(guard.usage_queue):
        guard.ledger.attach_usage(index, usage)
    actual_unknown = any(
        record.sent and record.actual_peak_fee_cny is None for record in records
    )
    actual_fee = None
    if not actual_unknown:
        actual_fee = str(
            sum(
                (Decimal(record.actual_peak_fee_cny or "0") for record in records if record.sent),
                Decimal("0"),
            )
        )
    passed = (
        stop_reason == "completed"
        and len(scenarios) == 2
        and all(scenario.get("passed") is True for scenario in scenarios)
        and guard.ledger.request_count <= MAX_REQUESTS
        and guard.ledger.reserved_fee_cny <= BUDGET_CNY
    )
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "stop_reason": stop_reason,
        "model": MODEL,
        "base_url": BASE_URL,
        "temperature": TEMPERATURE,
        "thinking": "disabled",
        "max_tokens_per_request": MAX_TOKENS,
        "budget_cny": str(BUDGET_CNY),
        "request_limit": MAX_REQUESTS,
        "request_count": guard.ledger.request_count,
        "max_body_bytes": MAX_BODY_BYTES,
        "reserved_fee_cny": str(guard.ledger.reserved_fee_cny),
        "actual_peak_fee_estimate_cny": actual_fee,
        "actual_peak_fee_estimate_status": "unknown_usage_missing" if actual_unknown else "estimated",
        "reservation_formula": "(2 * body_bytes + 1024) input tokens plus 256 output tokens per request",
        "prices_cny_per_million_uncached_peak": {
            "input": str(INPUT_PRICE_CNY_PER_MILLION),
            "output": str(OUTPUT_PRICE_CNY_PER_MILLION),
        },
        "request_attempts": [asdict(record) for record in records],
        "scenarios": scenarios,
    }


def _render_preflight(stream: TextIO) -> None:
    """打印固定最坏情况离线预检，不探测凭据也不创建 HTTP transport。"""

    print("DeepSeek Chat Live Smoke offline budget preflight (not executed)", file=stream)
    for key, value in offline_preflight().items():
        print(f"{key}: {value}", file=stream)
    print("HTTP requests sent: 0; reserved fee: CNY 0", file=stream)


def main(
    argv: list[str] | None = None,
    *,
    output_stream: TextIO | None = None,
    transport_factory: Callable[[], httpx.BaseTransport] | None = None,
) -> int:
    """默认只预检；显式 --execute 后 Provider 从环境变量读取鉴权值。"""

    parser = argparse.ArgumentParser(description="带人民币硬预算的 DeepSeek Chat Live Smoke")
    parser.add_argument("--execute", action="store_true", help="明确授权发送最多五个真实请求")
    parser.add_argument(
        "--api-key-env",
        default=API_KEY_ENV,
        help="保存 API Key 的环境变量名称；Provider 会读取其值，CLI 和报告不会打印该值",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/chat-live-smoke",
        help="脱敏运行报告的根目录",
    )
    args = parser.parse_args(argv)
    stdout = output_stream if output_stream is not None else sys.stdout
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.api_key_env):
        parser.error("--api-key-env 必须是环境变量名称")
    if not args.execute:
        _render_preflight(stdout)
        return 0

    if args.api_key_env != API_KEY_ENV:
        # 本项目的 Live Smoke 凭据身份固定，避免误把其他环境变量用于计费账户。
        parser.error(f"本冒烟测试固定使用环境变量 {API_KEY_ENV}")
    if API_KEY_ENV not in os.environ:
        print(f"Stopped: required environment variable {API_KEY_ENV} is absent; no transport created.", file=stdout)
        return 2

    run_id = uuid4().hex
    output_root = Path(args.output_dir)
    run_dir = output_root / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except OSError:
        print("Stopped: unable to create the redacted report directory; no transport created.", file=stdout)
        return 2

    ledger = BudgetLedger()
    downstream = transport_factory() if transport_factory is not None else httpx.HTTPTransport()
    guard = BudgetGuardTransport(downstream, ledger)
    from kaiji.providers.openai_compatible import OpenAICompatibleProvider

    try:
        # 子类只保留净化后的 Token 数；请求和响应原文不会进入报告。
        class RecordingProvider(OpenAICompatibleProvider):
            """在原 Provider 上记录 usage 数字，不改变请求和响应协议。"""

            def decide(self, messages):
                """调用一次 Provider 后，仅将 usage 模型加入脱敏队列。"""

                result = super().decide(messages)
                guard.usage_queue.append(result.metadata.usage)
                return result

        config = OpenAICompatibleConfig(
            base_url=BASE_URL,
            model=MODEL,
            api_key_env=API_KEY_ENV,
            temperature=TEMPERATURE,
            system_prompt=SYSTEM_PROMPT,
            thinking="disabled",
            max_tokens=MAX_TOKENS,
        )

        def create_runtime() -> AgentRuntime:
            """为每个会话创建独立 Runtime，但共享本次运行预算 transport。"""

            registry = build_registry()
            provider = RecordingProvider(config, registry, transport=guard)
            return AgentRuntime(provider, ToolExecutor(registry))

        scenarios, stop_reason = _run_scenarios_with_runtime_factory(guard, create_runtime)
        report = _report(guard, scenarios, stop_reason, run_id=run_id)
    except Exception as exc:
        # 异常类型可用于定位，异常文本可能带服务返回内容，因此不写入报告。
        report = _report(
            guard,
            [],
            f"internal_error:{type(exc).__name__}",
            run_id=run_id,
        )
    finally:
        guard.close_downstream()

    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Result: {'PASS' if report['passed'] else 'FAIL'}", file=stdout)
    print(f"Stop reason: {report['stop_reason']}", file=stdout)
    print(f"HTTP requests: {report['request_count']} / {MAX_REQUESTS}", file=stdout)
    print(f"Reserved fee: CNY {report['reserved_fee_cny']} / CNY {BUDGET_CNY}", file=stdout)
    print(f"Actual peak fee estimate: {report['actual_peak_fee_estimate_cny'] or 'unknown'}", file=stdout)
    print(f"Redacted report: {report_path}", file=stdout)
    return 0 if report["passed"] else 1


def _run_scenarios_with_runtime_factory(
    guard: BudgetGuardTransport,
    runtime_factory: Callable[[], AgentRuntime],
) -> tuple[list[dict[str, object]], str]:
    """以同一 Transport 工厂执行真实 smoke 的两组精简验收步骤。"""

    manager = ChatSessionManager(runtime_factory)
    scenarios: list[dict[str, object]] = []
    code = "KJ-4827"
    session_a = manager.get("chat-live-smoke-continuity")
    first_a = session_a.turn(
        f"请记住这个短验证码：{code}。请在本轮回答中原样写出验证码。"
    )
    first_a_ok = _verify_plain_fact_turn(first_a, code)
    scenario_a: dict[str, object] = {
        "name": "two_turn_history",
        "turns": [_turn_summary(first_a, fact_verified=first_a_ok)],
        "history_message_count_after_turn_1": len(session_a.history),
    }
    scenarios.append(scenario_a)
    if not first_a_ok:
        scenario_a["passed"] = False
        reason = guard.ledger.stop_reason or (
            first_a.stop_reason.value if first_a.stop_reason != StopReason.COMPLETED else "verification_failed:turn_1"
        )
        return scenarios, reason
    second_a = session_a.turn("请只回答我刚才给你的短验证码是什么？")
    second_a_ok = (
        _verify_plain_fact_turn(second_a, code)
        and len(session_a.history) == len(first_a.messages) + len(second_a.messages)
    )
    scenario_a["turns"].append(_turn_summary(second_a, fact_verified=second_a_ok))
    scenario_a["history_message_count_after_turn_2"] = len(session_a.history)
    scenario_a["passed"] = second_a_ok
    if not second_a_ok:
        reason = guard.ledger.stop_reason or (
            second_a.stop_reason.value if second_a.stop_reason != StopReason.COMPLETED else "verification_failed:turn_2"
        )
        return scenarios, reason

    session_b = manager.get("chat-live-smoke-discount")
    first_b = session_b.turn(
        "必须调用 calculate_discount 工具，参数严格为 price=100、percent=10，"
        "然后只用阿拉伯数字报告折后价格。"
    )
    tool_verified = _verify_discount_result(first_b)
    first_b_ok = (
        tool_verified
        and len(first_b.model_calls) == 2
        and _contains_price_90(first_b.answer or "")
    )
    scenario_b: dict[str, object] = {
        "name": "tool_call_then_cross_turn_fact",
        "turns": [_turn_summary(first_b, fact_verified=first_b_ok)],
        "successful_tool_result_count": len(first_b.successful_tool_results),
        "tool_result_verified": tool_verified,
        "history_message_count_after_turn_1": len(session_b.history),
    }
    scenarios.append(scenario_b)
    if not first_b_ok:
        scenario_b["passed"] = False
        reason = guard.ledger.stop_reason or (
            first_b.stop_reason.value if first_b.stop_reason != StopReason.COMPLETED else "verification_failed:tool_turn"
        )
        return scenarios, reason
    second_b = session_b.turn("刚才计算出的折后价格是多少？请只回答金额数字。")
    second_b_ok = (
        _verify_plain_fact_turn(second_b, "90")
        and len(session_b.history) == len(first_b.messages) + len(second_b.messages)
    )
    scenario_b["turns"].append(_turn_summary(second_b, fact_verified=second_b_ok))
    scenario_b["history_message_count_after_turn_2"] = len(session_b.history)
    scenario_b["passed"] = second_b_ok
    if not second_b_ok:
        reason = guard.ledger.stop_reason or (
            second_b.stop_reason.value if second_b.stop_reason != StopReason.COMPLETED else "verification_failed:tool_followup"
        )
        return scenarios, reason
    return scenarios, "completed"


if __name__ == "__main__":
    raise SystemExit(main())
