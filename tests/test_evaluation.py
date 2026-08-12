"""验证固定评测集、严格观测覆盖、计数不变量和全部聚合公式。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import kaiji.evaluation as evaluation_module
from kaiji.evaluation import (
    EvalCase,
    EvalObservation,
    aggregate,
    load_cases,
    reference_observation,
    run_eval,
)
from kaiji.models import StopReason


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


def test_reference_observation_is_only_pipeline_gold_fixture() -> None:
    """金标生成器验证聚合管线满分输入，不代表真实 Agent 已被评测。"""

    cases = load_cases(CASES_PATH)
    observations = [reference_observation(case) for case in cases]

    report = run_eval(cases, reference_observation)

    assert report.case_count == 20
    assert report.task_success_rate == 1.0
    assert report.tool_selection_accuracy == 1.0
    assert report.argument_accuracy == pytest.approx(13 / 14)
    assert report.tool_success_rate == pytest.approx(13 / 19)
    assert report.retry_recovery_rate == 1.0
    assert report.unnecessary_tool_call_rate == 0.0
    assert report.average_steps == pytest.approx(24 / 20)
    assert report.citation_supported_rate == 1.0
    assert report.p50_latency_ms == 10.0
    assert report.p95_latency_ms == 10.0
    assert report.stop_reason_distribution == {
        StopReason.COMPLETED.value: 12,
        StopReason.TOOL_FAILED.value: 5,
        StopReason.MAX_STEPS.value: 1,
        StopReason.INSUFFICIENT_EVIDENCE.value: 1,
        StopReason.HUMAN_REJECTED.value: 1,
    }

    for case, observation in zip(cases, observations, strict=True):
        assert observation.case_id == case.case_id
        assert observation.task_success
        assert observation.stop_reason == case.expected_stop_reason
        assert 0 <= observation.successful_tool_calls <= observation.tool_calls
        assert observation.tool_calls <= case.max_tool_calls
        assert observation.unnecessary_tool_calls == 0
        assert observation.steps >= 1
        assert observation.latency_ms == 10.0

        if case.expected_tool is None:
            assert observation.selected_tool is None
            assert observation.arguments_correct is None
            assert observation.tool_calls == 0
            assert observation.successful_tool_calls == 0
        else:
            assert observation.selected_tool == case.expected_tool
            assert observation.arguments_correct == (case.category != "argument_error")

        if case.expected_stop_reason == StopReason.TOOL_FAILED:
            assert observation.successful_tool_calls == 0
        if case.category in {"argument_error", "unknown_tool"}:
            assert observation.tool_calls == 0
        if case.expected_stop_reason in {
            StopReason.MAX_STEPS,
            StopReason.INSUFFICIENT_EVIDENCE,
        }:
            assert observation.tool_calls == case.max_tool_calls
            assert observation.successful_tool_calls == observation.tool_calls

        if case.expects_retry_recovery:
            assert observation.retry_recovered is True
            assert observation.tool_calls == 2
            expected_successes = 2 if case.category == "query_rewrite" else 1
            assert observation.successful_tool_calls == expected_successes
        else:
            assert observation.retry_recovered is None

        if case.expects_citation:
            assert observation.citation_supported is True
        else:
            assert observation.citation_supported is None

        if case.category == "query_rewrite" or case.expected_stop_reason in {
            StopReason.MAX_STEPS,
            StopReason.INSUFFICIENT_EVIDENCE,
        }:
            assert observation.steps == max(1, observation.tool_calls)
        else:
            # 工具内部尝试数和状态机逻辑迭代不是同一个计数器。
            assert observation.steps == 1


def test_mixed_observations_exercise_every_metric_formula() -> None:
    cases = [
        EvalCase(
            case_id="case-a",
            prompt="需要重试并引用",
            category="retry",
            expected_stop_reason=StopReason.COMPLETED,
            expected_tool="search_azusa",
            expects_retry_recovery=True,
            expects_citation=True,
            max_tool_calls=2,
        ),
        EvalCase(
            case_id="case-b",
            prompt="普通工具调用",
            category="tool",
            expected_stop_reason=StopReason.COMPLETED,
            expected_tool="get_chunk_by_id",
            expects_citation=True,
            max_tool_calls=1,
        ),
        EvalCase(
            case_id="case-c",
            prompt="不应调用工具",
            category="no_tool",
            expected_stop_reason=StopReason.HUMAN_REJECTED,
            max_tool_calls=0,
        ),
        EvalCase(
            case_id="case-d",
            prompt="预期工具失败",
            category="failure",
            expected_stop_reason=StopReason.TOOL_FAILED,
            expected_tool="search_azusa",
            expects_retry_recovery=True,
            max_tool_calls=2,
        ),
    ]
    observations = [
        EvalObservation(
            case_id="case-a",
            task_success=True,
            selected_tool="search_azusa",
            arguments_correct=True,
            tool_calls=2,
            successful_tool_calls=1,
            retry_recovered=True,
            unnecessary_tool_calls=0,
            steps=3,
            latency_ms=10,
            stop_reason=StopReason.COMPLETED,
            citation_supported=True,
        ),
        EvalObservation(
            case_id="case-b",
            task_success=True,
            selected_tool="search_azusa",
            arguments_correct=None,
            tool_calls=1,
            successful_tool_calls=0,
            unnecessary_tool_calls=1,
            steps=2,
            latency_ms=20,
            stop_reason=StopReason.COMPLETED,
            citation_supported=None,
        ),
        EvalObservation(
            case_id="case-c",
            task_success=True,
            selected_tool="search_azusa",
            arguments_correct=None,
            tool_calls=1,
            successful_tool_calls=1,
            unnecessary_tool_calls=1,
            steps=1,
            latency_ms=30,
            stop_reason=StopReason.HUMAN_REJECTED,
        ),
        EvalObservation(
            case_id="case-d",
            task_success=True,
            selected_tool="search_azusa",
            arguments_correct=False,
            tool_calls=1,
            successful_tool_calls=1,
            retry_recovered=None,
            unnecessary_tool_calls=0,
            steps=4,
            latency_ms=40,
            stop_reason=StopReason.COMPLETED,
        ),
    ]

    report = aggregate(cases, observations)
    reversed_report = aggregate(cases, list(reversed(observations)))

    assert reversed_report == report
    assert report.case_count == 4
    assert report.task_success_rate == pytest.approx(0.5)
    assert report.tool_selection_accuracy == pytest.approx(2 / 3)
    assert report.argument_accuracy == pytest.approx(1 / 3)
    assert report.tool_success_rate == pytest.approx(3 / 5)
    assert report.retry_recovery_rate == pytest.approx(1 / 2)
    assert report.unnecessary_tool_call_rate == pytest.approx(2 / 5)
    assert report.average_steps == pytest.approx(2.5)
    assert report.p50_latency_ms == 20
    assert report.p95_latency_ms == 40
    assert report.stop_reason_distribution == {
        StopReason.COMPLETED.value: 3,
        StopReason.HUMAN_REJECTED.value: 1,
    }
    assert report.citation_supported_rate == pytest.approx(1 / 2)


def test_eval_zero_denominators_are_explicit_zero_rates() -> None:
    """没有适用工具、重试、引用或实际调用时，各比率必须稳定为零。"""

    cases = [make_case("case-no-tool")]
    observations = [make_observation("case-no-tool")]

    report = aggregate(cases, observations)

    assert report.task_success_rate == 1.0
    assert report.tool_selection_accuracy == 0.0
    assert report.argument_accuracy == 0.0
    assert report.tool_success_rate == 0.0
    assert report.retry_recovery_rate == 0.0
    assert report.unnecessary_tool_call_rate == 0.0
    assert report.citation_supported_rate == 0.0


def make_observation(case_id: str) -> EvalObservation:
    """构造用于覆盖和计数校验的最小合法观测。"""

    return EvalObservation(
        case_id=case_id,
        task_success=True,
        selected_tool=None,
        arguments_correct=None,
        steps=1,
        latency_ms=1,
        stop_reason=StopReason.COMPLETED,
    )


def make_case(case_id: str) -> EvalCase:
    """构造用于覆盖校验的最小合法用例。"""

    return EvalCase(
        case_id=case_id,
        prompt="测试",
        category="test",
        expected_stop_reason=StopReason.COMPLETED,
        max_tool_calls=0,
    )


def test_todo_11b_unique_index_isolated_from_alignment_and_metrics() -> None:
    """TODO 11b 只建立唯一索引，不进入 11d 对齐或 11e 聚合。"""

    first = make_case("first")
    second = make_case("second")
    assert evaluation_module._index_by_unique_case_id(
        [first, second],
        "评测用例",
    ) == {"first": first, "second": second}
    with pytest.raises(ValueError, match="重复项: first"):
        evaluation_module._index_by_unique_case_id(
            [first, first],
            "评测用例",
        )


def test_todo_11d_alignment_isolated_from_metric_formulas() -> None:
    """TODO 11d 只验证覆盖并恢复 case 顺序，不计算任何比率。"""

    cases = [make_case("first"), make_case("second")]
    second = make_observation("second")
    first = make_observation("first")

    assert evaluation_module._align_observations(cases, [second, first]) == [
        first,
        second,
    ]
    with pytest.raises(ValueError, match="缺失: second"):
        evaluation_module._align_observations(cases, [first])


def test_todo_11e_metrics_use_already_aligned_observations() -> None:
    """TODO 11e 可注入 11d 结果，证明公式反馈不被身份对齐实现掩盖。"""

    case = make_case("case")
    observation = make_observation("case")

    report = evaluation_module._aggregate_aligned([case], [observation])

    assert report.case_count == 1
    assert report.task_success_rate == 1.0
    assert report.average_steps == 1.0


def test_eval_rejects_missing_or_unexpected_observations() -> None:
    cases = [make_case("case-1"), make_case("case-2")]

    with pytest.raises(ValueError, match="缺失: case-2"):
        aggregate(cases, [make_observation("case-1")])

    with pytest.raises(ValueError, match="额外: case-3"):
        aggregate(
            cases,
            [
                make_observation("case-1"),
                make_observation("case-2"),
                make_observation("case-3"),
            ],
        )


def test_eval_rejects_duplicate_case_and_observation_ids() -> None:
    with pytest.raises(ValueError, match="评测用例.*case-1"):
        aggregate(
            [make_case("case-1"), make_case("case-1")],
            [make_observation("case-1")],
        )

    with pytest.raises(ValueError, match="评测观测.*case-1"):
        aggregate(
            [make_case("case-1")],
            [make_observation("case-1"), make_observation("case-1")],
        )


def test_todo_11c_load_cases_rejects_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    case = make_case("case-1").model_dump(mode="json")
    path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for _ in range(2)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="评测用例.*case-1"):
        load_cases(path)


def test_load_cases_preserves_non_empty_file_order(tmp_path) -> None:
    """JSONL 空行会被忽略，但合法用例必须维持文件中的先后顺序。"""

    path = tmp_path / "ordered-cases.jsonl"
    second = make_case("case-second").model_dump_json()
    first = make_case("case-first").model_dump_json()
    path.write_text(f"\n{second}\n  \n{first}\n", encoding="utf-8")

    assert [case.case_id for case in load_cases(path)] == [
        "case-second",
        "case-first",
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"tool_calls": -1},
        {"successful_tool_calls": -1},
        {"unnecessary_tool_calls": -1},
        {"steps": 0},
        {"latency_ms": -0.1},
        {"latency_ms": float("inf")},
        {"latency_ms": float("nan")},
        {"tool_calls": 1, "successful_tool_calls": 2},
        {"tool_calls": 1, "unnecessary_tool_calls": 2},
    ],
)
def test_todo_11a_observation_rejects_invalid_counts_and_measurements(
    changes: dict[str, object],
) -> None:
    values = {
        "case_id": "case-1",
        "task_success": True,
        "selected_tool": None,
        "arguments_correct": None,
        "tool_calls": 0,
        "successful_tool_calls": 0,
        "unnecessary_tool_calls": 0,
        "steps": 1,
        "latency_ms": 1,
        "stop_reason": StopReason.COMPLETED,
    }
    values.update(changes)

    with pytest.raises(ValidationError):
        EvalObservation(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"task_success": 1},
        {"arguments_correct": "false"},
        {"tool_calls": True},
        {"successful_tool_calls": True},
        {"retry_recovered": 1},
        {"unnecessary_tool_calls": True},
        {"steps": True},
        {"citation_supported": "true"},
    ],
)
def test_observation_rejects_coerced_bool_and_integer_boundaries(changes) -> None:
    values = make_observation("case-1").model_dump()
    values.update(changes)

    with pytest.raises(ValidationError):
        EvalObservation.model_validate(values)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_tool_calls": True},
        {"expects_retry_recovery": 1},
        {"expects_citation": "false"},
    ],
)
def test_eval_case_rejects_coerced_safety_fields(changes) -> None:
    values = make_case("case-1").model_dump()
    values.update(changes)

    with pytest.raises(ValidationError):
        EvalCase.model_validate(values)


@pytest.mark.parametrize(
    "changes",
    [
        {"case_id": "   "},
        {"selected_tool": "   "},
        {"selected_tool": None, "arguments_correct": True},
        {"retry_recovered": True, "tool_calls": 1},
        {
            "citation_supported": True,
            "successful_tool_calls": 0,
        },
    ],
)
def test_observation_rejects_ambiguous_cross_field_states(changes) -> None:
    values = make_observation("case-1").model_dump()
    values.update(changes)
    with pytest.raises(ValidationError):
        EvalObservation.model_validate(values)


@pytest.mark.parametrize(
    "changes",
    [
        {"case_id": "   "},
        {"prompt": "   "},
        {"category": "   "},
        {"expected_tool": "   "},
    ],
)
def test_eval_case_rejects_blank_identity_and_contract_text(changes) -> None:
    values = make_case("case-1").model_dump()
    values.update(changes)
    with pytest.raises(ValidationError):
        EvalCase.model_validate(values)
