from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from kaiji.models import StopReason


class EvalCase(BaseModel):
    case_id: str
    prompt: str
    category: str
    expected_stop_reason: StopReason
    expected_tool: str | None = None
    expects_retry_recovery: bool = False
    expects_citation: bool = False
    max_tool_calls: int = Field(default=1, ge=0)


class EvalObservation(BaseModel):
    case_id: str
    task_success: bool
    selected_tool: str | None
    arguments_correct: bool | None
    tool_calls: int = 0
    successful_tool_calls: int = 0
    retry_recovered: bool | None = None
    unnecessary_tool_calls: int = 0
    steps: int
    latency_ms: float
    stop_reason: StopReason
    citation_supported: bool | None = None


class EvalReport(BaseModel):
    case_count: int
    task_success_rate: float
    tool_selection_accuracy: float
    argument_accuracy: float
    tool_success_rate: float
    retry_recovery_rate: float
    unnecessary_tool_call_rate: float
    average_steps: float
    p50_latency_ms: float
    p95_latency_ms: float
    stop_reason_distribution: dict[str, int]
    citation_supported_rate: float


def load_cases(path: Path) -> list[EvalCase]:
    return [
        EvalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def aggregate(cases: list[EvalCase], observations: list[EvalObservation]) -> EvalReport:
    case_by_id = {case.case_id: case for case in cases}
    if set(case_by_id) != {item.case_id for item in observations}:
        raise ValueError("observations must cover every eval case exactly once")

    # TODO 11: Aggregate the fixed observations.
    #
    # Denominators matter:
    # - selection/argument: cases that expect a tool;
    # - retry recovery: cases marked expects_retry_recovery;
    # - citation: cases marked expects_citation;
    # - tool success/unnecessary: actual tool call count.
    #
    # Use _rate and _percentile, and count every StopReason. Do not silently
    # skip a missing case (the exact-coverage guard above is intentional).
    del case_by_id
    return EvalReport(
        case_count=len(cases),
        task_success_rate=0.0,
        tool_selection_accuracy=0.0,
        argument_accuracy=0.0,
        tool_success_rate=0.0,
        retry_recovery_rate=0.0,
        unnecessary_tool_call_rate=0.0,
        average_steps=0.0,
        p50_latency_ms=0.0,
        p95_latency_ms=0.0,
        stop_reason_distribution={},
        citation_supported_rate=0.0,
    )


def run_eval(
    cases: list[EvalCase],
    runner: Callable[[EvalCase], EvalObservation],
) -> EvalReport:
    return aggregate(cases, [runner(case) for case in cases])


def reference_observation(case: EvalCase) -> EvalObservation:
    """Deterministic gold runner used to validate the metric pipeline itself."""

    tool_calls = 0 if case.expected_tool is None else (2 if case.expects_retry_recovery else 1)
    return EvalObservation(
        case_id=case.case_id,
        task_success=True,
        selected_tool=case.expected_tool,
        arguments_correct=True if case.expected_tool else None,
        tool_calls=tool_calls,
        successful_tool_calls=1 if case.expected_tool else 0,
        retry_recovered=True if case.expects_retry_recovery else None,
        unnecessary_tool_calls=0,
        steps=max(1, tool_calls),
        latency_ms=float(8 + int(case.case_id.split("-")[-1])),
        stop_reason=case.expected_stop_reason,
        citation_supported=True if case.expects_citation else None,
    )


def report_to_json(report: EvalReport) -> str:
    return json.dumps(report.model_dump(), ensure_ascii=False, indent=2)
