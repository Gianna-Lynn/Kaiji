"""验证实验产物完整性、唯一性、数据指纹和 Bad Case。"""

import json
import math
from pathlib import Path

import pytest

from kaiji.eval_runner import AgentEvalCase
from kaiji.experiment import (
    ExperimentConfig,
    ExperimentRunner,
    build_scripted_runner,
    load_agent_cases,
    run_ablation,
)
from kaiji.harnesses import HarnessVariant
from kaiji.models import StopReason


def _cases():
    return [
        AgentEvalCase(
            case_id="no-tool",
            semantic_group="direct",
            suite="smoke",
            prompt="直接回答",
            scenario="no_tool",
            expected_stop_reason=StopReason.COMPLETED,
            expects_no_tool=True,
            required_answer_facts=["无需调用工具"],
        ),
        AgentEvalCase(
            case_id="discount",
            semantic_group="tool",
            suite="smoke",
            prompt="折扣",
            scenario="discount",
            expected_stop_reason=StopReason.COMPLETED,
            expected_tool="calculate_discount",
            expected_arguments={"price": 100, "percent": 10},
            required_answer_facts=["90"],
        ),
    ]


class _CompletedHarness:
    """为 15b 提供已经完成的 15a 前置开关，不泄露其实现公式。"""

    retry_enabled = True
    duplicate_guard = True


def test_todo_15b_experiment_writes_complete_reproducible_artifacts(tmp_path) -> None:
    config = ExperimentConfig(suite="smoke", repeats=2)
    report = ExperimentRunner(config, build_scripted_runner(_CompletedHarness())).run(
        _cases(), tmp_path
    )
    target = tmp_path / report.experiment_id

    assert report.complete is True
    assert report.expected_episode_count == report.actual_episode_count == 4
    assert report.aggregate.task_success_rate == 1.0
    assert len(report.dataset_hash) == len(report.config_fingerprint) == 64
    assert (
        len((target / "episodes.jsonl").read_text(encoding="utf-8").splitlines()) == 4
    )
    assert (target / "report.json").exists()
    assert (target / "report.md").exists()
    assert json.loads((target / "bad_cases.json").read_text(encoding="utf-8")) == []


def test_todo_15b_failed_experiment_writes_no_formal_report(tmp_path) -> None:
    """普通 Experiment 失败不返回 incomplete，也不留下正式产物目录。"""

    class FailingRunner:
        def run(self, case, *, repeat_index=0):
            raise RuntimeError("受控 Provider 失败")

    with pytest.raises(RuntimeError, match="受控 Provider 失败"):
        ExperimentRunner(ExperimentConfig(suite="smoke"), FailingRunner()).run(
            _cases(), tmp_path
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "field,value",
    [
        ("repeats", True),
        ("repeats", "2"),
        ("max_steps", True),
        ("max_steps", "4"),
        ("temperature", True),
        ("temperature", math.inf),
        ("input_cost_per_million", "0.1"),
        ("output_cost_per_million", math.nan),
    ],
)
def test_todo_15b_experiment_config_rejects_unsafe_numeric_boundaries(
    field, value
) -> None:
    payload = {"suite": "smoke", field: value}
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate(payload)


def test_todo_15b_write_failure_cleans_staging_and_preserves_existing_directory(
    tmp_path, monkeypatch
) -> None:
    """产物阶段失败时清理临时目录，但不得碰已有实验目录。"""

    existing = tmp_path / "existing-run"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    original_write_text = Path.write_text

    def fail_report_write(path, data, *args, **kwargs):
        if path.name == "report.json":
            raise OSError("受控 report 写入失败")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_report_write)
    with pytest.raises(OSError, match="report 写入失败"):
        ExperimentRunner(
            ExperimentConfig(suite="smoke"),
            build_scripted_runner(_CompletedHarness()),
        ).run(_cases(), tmp_path)

    assert marker.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.iterdir()) == [existing]


def test_loader_rejects_duplicate_case_ids(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    line = _cases()[0].model_dump_json()
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="case_id 必须唯一"):
        load_agent_cases(path)


def test_todo_15c_ablation_uses_same_dataset_and_exposes_retry_effect(tmp_path) -> None:
    transient = AgentEvalCase(
        case_id="transient",
        semantic_group="retry",
        suite="reliability",
        prompt="瞬时失败",
        scenario="transient",
        expected_stop_reason=StopReason.COMPLETED,
        expected_tool="calculate_discount",
        expected_arguments={"price": 100, "percent": 10},
        required_answer_facts=["90"],
        expects_retry_recovery=True,
    )

    report = run_ablation(
        [transient], suite="reliability", repeats=1, output_dir=tmp_path
    )

    hashes = {item.dataset_hash for item in report.reports.values()}
    assert hashes == {report.dataset_hash}
    assert report.reports[HarnessVariant.FULL_KAIJI].aggregate.task_success_rate == 1
    assert report.reports[HarnessVariant.NO_RETRY].aggregate.task_success_rate == 0
    assert (tmp_path / "ablation.json").exists()
    assert (tmp_path / "ablation.md").exists()


def test_formal_datasets_have_fixed_twenty_twenty_twenty_split() -> None:
    root = Path(__file__).resolve().parents[1]
    agent_cases = load_agent_cases(root / "eval" / "agent_cases.jsonl")
    fault_cases = load_agent_cases(root / "eval" / "fault_cases.jsonl")

    assert sum(case.suite == "behavior" for case in agent_cases) == 20
    assert sum(case.suite == "paraphrase" for case in agent_cases) == 20
    assert len(fault_cases) == 20
    assert {case.suite for case in fault_cases} == {"reliability"}
    assert {
        "transient",
        "persistent",
        "permanent",
        "timeout",
        "empty_evidence",
        "http_429",
        "http_503",
        "network",
        "invalid_json",
        "schema_drift",
        "chunk_mismatch",
        "duplicate",
        "max_steps",
        "failed_evidence",
    } <= {case.scenario for case in fault_cases}
