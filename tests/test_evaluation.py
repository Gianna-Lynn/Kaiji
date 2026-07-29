from pathlib import Path

import pytest

from kaiji.evaluation import (
    EvalObservation,
    aggregate,
    load_cases,
    reference_observation,
    run_eval,
)


CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "cases.jsonl"


def test_eval_suite_has_task_book_coverage() -> None:
    cases = load_cases(CASES_PATH)

    assert 20 <= len(cases) <= 30
    categories = {case.category for case in cases}
    assert {
        "argument_error",
        "retry_recovery",
        "max_steps",
        "human_rejection",
        "checkpoint_resume",
        "citation_guard",
    } <= categories


def test_reference_eval_exercises_all_metric_formulas() -> None:
    cases = load_cases(CASES_PATH)

    report = run_eval(cases, reference_observation)

    assert report.case_count == 20
    assert report.task_success_rate == 1.0
    assert report.tool_selection_accuracy == 1.0
    assert report.argument_accuracy == 1.0
    assert report.retry_recovery_rate == 1.0
    assert report.unnecessary_tool_call_rate == 0.0
    assert report.citation_supported_rate == 1.0
    assert report.p95_latency_ms >= report.p50_latency_ms


def test_eval_requires_exact_case_coverage() -> None:
    cases = load_cases(CASES_PATH)
    incomplete: list[EvalObservation] = []

    with pytest.raises(ValueError, match="every eval case"):
        aggregate(cases, incomplete)
