"""把 Kaiji Trace 安全投影为 OpenTelemetry 风格的 span 记录。"""

from __future__ import annotations

from enum import StrEnum
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kaiji.models import RunResult, StopReason, TraceEvent


class SpanStatus(StrEnum):
    """表示投影 span 的最小状态集合。"""

    UNSET = "UNSET"
    ERROR = "ERROR"


class SpanProjection(BaseModel):
    """保存可序列化但不冒充真实 SDK span 的安全投影。"""

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    name: str = Field(min_length=1)
    status: SpanStatus
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def attributes_are_finite_json(self) -> SpanProjection:
        """保证属性可以安全进入日志或后续 exporter。"""

        try:
            json.dumps(self.attributes, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("span attributes 必须是有限 JSON 值") from exc
        return self


def project_run_to_spans(run_id: str, result: RunResult) -> list[SpanProjection]:
    """把一次完整运行映射为根 span 与逐事件子 span。

    该函数没有时钟、上下文传播或 exporter，因此只称为投影。属性采用白名单，
    不复制用户问题、工具参数、工具输出、Provider 正文或鉴权信息。
    """

    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 必须是非空字符串")

    trace_id = _stable_hex(f"trace:{run_id}", bytes_count=16)
    root_span_id = _stable_hex(f"root:{run_id}", bytes_count=8)
    spans = [
        SpanProjection(
            trace_id=trace_id,
            span_id=root_span_id,
            name="kaiji.agent.run",
            status=(
                SpanStatus.UNSET
                if result.stop_reason == StopReason.COMPLETED
                else SpanStatus.ERROR
            ),
            attributes={
                "kaiji.run_id": run_id,
                "kaiji.stop_reason": result.stop_reason.value,
                "kaiji.steps": result.steps,
                "kaiji.successful_tool_results": len(
                    result.successful_tool_results
                ),
            },
        )
    ]
    for index, event in enumerate(result.trace):
        spans.append(
            SpanProjection(
                trace_id=trace_id,
                span_id=_stable_hex(
                    f"event:{run_id}:{index}:{event.step}:{event.event}",
                    bytes_count=8,
                ),
                parent_span_id=root_span_id,
                name=f"kaiji.{event.event}",
                status=_event_status(event),
                attributes=_safe_event_attributes(event),
            )
        )
    return spans


def _safe_event_attributes(event: TraceEvent) -> dict[str, Any]:
    """仅抽取定位故障所需的低敏字段。"""

    attributes: dict[str, Any] = {
        "kaiji.step": event.step,
        "kaiji.event": event.event,
    }
    data = event.data
    if event.event == "stop":
        reason = data.get("reason")
        attributes["kaiji.stop_reason"] = (
            reason.value if isinstance(reason, StopReason) else reason
        )
    elif event.event == "tool_result":
        for source, target in (
            ("tool_name", "kaiji.tool.name"),
            ("ok", "kaiji.tool.ok"),
            ("attempts", "kaiji.tool.attempts"),
        ):
            if source in data:
                attributes[target] = data[source]
        error = data.get("error")
        if isinstance(error, dict) and "code" in error:
            attributes["kaiji.tool.error_code"] = error["code"]
    elif event.event in {"model_action", "model_failure"}:
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and "model" in metadata:
            attributes["kaiji.model"] = metadata["model"]
        failure = data.get("failure")
        if isinstance(failure, dict) and "code" in failure:
            attributes["kaiji.provider.error_code"] = failure["code"]
        if "code" in data:
            attributes["kaiji.provider.error_code"] = data["code"]
    return attributes


def _event_status(event: TraceEvent) -> SpanStatus:
    """把明确失败事件与非成功停止映射为 ERROR。"""

    if event.event == "model_failure":
        return SpanStatus.ERROR
    if event.event == "tool_result" and event.data.get("ok") is False:
        return SpanStatus.ERROR
    if event.event == "stop":
        try:
            reason = StopReason(event.data.get("reason"))
        except (TypeError, ValueError):
            return SpanStatus.ERROR
        if reason != StopReason.COMPLETED:
            return SpanStatus.ERROR
    return SpanStatus.UNSET


def _stable_hex(value: str, *, bytes_count: int) -> str:
    """生成只用于本地投影关联的稳定十六进制标识。"""

    return hashlib.blake2b(value.encode("utf-8"), digest_size=bytes_count).hexdigest()
