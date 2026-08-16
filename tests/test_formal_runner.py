"""验证 Formal Runner 的逐条持久化、预算账本和安全恢复边界。"""

from pathlib import Path
import json

import pytest

from kaiji.eval_runner import AgentEpisode, AgentEvalCase
from kaiji.experiment import build_scripted_runner, load_agent_cases
from kaiji.formal_runner import (
    FormalRunConfig,
    FormalEpisodeRecord,
    FormalRunReport,
    PersistentRequestLedger,
    ResumableFormalRunner,
    build_deepseek_behavior_runner,
    rescore_formal_run,
    run_formal_preflight,
    _fingerprint,
)
from kaiji.models import (
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ModelUsage,
    Message,
    RunResult,
    ProviderAttemptFailure,
    ProviderErrorCode,
    ProviderFailure,
    StopReason,
    TraceEvent,
)
from kaiji.real_pilot import BEHAVIOR_SYSTEM_PROMPT, build_behavior_registry


def _config(*, repeats: int = 2) -> FormalRunConfig:
    return FormalRunConfig(
        suite="real_behavior",
        provider="mock",
        model="scripted",
        repeats=repeats,
        request_limit=100,
        cost_limit_usd=1,
        system_prompt_sha256=_fingerprint(BEHAVIOR_SYSTEM_PROMPT),
        tool_schema_sha256=_fingerprint(build_behavior_registry().schemas()),
    )


def _cases() -> list[AgentEvalCase]:
    root = Path(__file__).resolve().parents[1]
    return load_agent_cases(root / "eval" / "real_behavior_cases.jsonl")[:2]


class _CompletedHarness:
    """隔离 15d 与 15a，使 Formal 窄测试只反馈恢复循环。"""

    retry_enabled = True
    duplicate_guard = True


def _runner() -> object:
    return build_scripted_runner(_CompletedHarness())


def _minimal_episode(case: AgentEvalCase, model_call: ModelCallResult) -> AgentEpisode:
    """构造不依赖 TODO14a Runner 的最小完整 Episode。"""

    result = RunResult(
        answer="收到",
        stop_reason=StopReason.COMPLETED,
        steps=1,
        messages=[Message(role="assistant", content="收到")],
        successful_tool_results=[],
        trace=[
            TraceEvent(step=1, event="model_action", data={"kind": "final_answer"}),
            TraceEvent(
                step=1,
                event="stop",
                data={"reason": StopReason.COMPLETED.value},
            ),
        ],
        model_calls=[model_call],
    )
    return AgentEpisode(
        run_id=f"{case.case_id}-direct",
        case_id=case.case_id,
        suite=case.suite,
        repeat_index=0,
        latency_ms=1,
        result=result,
    )


class InterruptAfterOne:
    """第一条完成后模拟进程级异常，验证 finally 部分报告与后续恢复。"""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls = 0

    def run(self, case, *, repeat_index=0):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("模拟中断")
        return self.delegate.run(case, repeat_index=repeat_index)


def test_real_behavior_and_paraphrase_have_one_to_one_contracts() -> None:
    """两套真实题必须各 20 条，并在五类语义组中一一对应。"""

    root = Path(__file__).resolve().parents[1]
    behavior = load_agent_cases(root / "eval" / "real_behavior_cases.jsonl")
    paraphrase = load_agent_cases(root / "eval" / "real_paraphrase_cases.jsonl")

    assert len(behavior) == len(paraphrase) == 20
    assert {case.suite for case in behavior} == {"behavior"}
    assert {case.suite for case in paraphrase} == {"paraphrase"}
    assert sorted(case.semantic_group for case in behavior) == sorted(
        case.semantic_group for case in paraphrase
    )
    behavior_contracts = sorted(
        json.dumps(
            (
                case.semantic_group,
                case.expected_tool,
                case.expected_arguments,
                case.required_answer_facts,
                case.allowed_citation_ids,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
        for case in behavior
    )
    paraphrase_contracts = sorted(
        json.dumps(
            (
                case.semantic_group,
                case.expected_tool,
                case.expected_arguments,
                case.required_answer_facts,
                case.allowed_citation_ids,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
        for case in paraphrase
    )
    assert behavior_contracts == paraphrase_contracts
    assert all(
        case.scenario not in {"duplicate", "max_steps", "invalid_arguments"}
        for case in paraphrase
    )


def test_todo_15d_partial_run_persists_each_episode_without_aggregate(tmp_path) -> None:
    formal = ResumableFormalRunner(_config(), _cases(), _runner(), tmp_path / "formal")

    report = formal.run(max_new_episodes=1)

    assert report.complete is False
    assert report.stop_reason == "preflight_pause"
    assert report.actual_episode_count == 1
    assert report.expected_episode_count == 4
    assert report.aggregate is None
    assert len((formal.episodes_path).read_text(encoding="utf-8").splitlines()) == 1


def test_closed_budget_stops_before_any_episode(tmp_path) -> None:
    delegate = InterruptAfterOne(_runner())
    formal = ResumableFormalRunner(
        _config(),
        _cases(),
        delegate,
        tmp_path / "formal",
        stop_check=lambda: "request_limit",
    )

    report = formal.run()

    assert report.complete is False
    assert report.stop_reason == "request_limit"
    assert report.actual_episode_count == 0
    assert delegate.calls == 0


def test_resume_skips_completed_coordinates_and_finishes_matrix(tmp_path) -> None:
    run_dir = tmp_path / "formal"
    first = ResumableFormalRunner(_config(), _cases(), _runner(), run_dir)
    partial = first.run(max_new_episodes=2)
    previous_lines = first.episodes_path.read_text(encoding="utf-8").splitlines()

    resumed = ResumableFormalRunner(
        _config(), _cases(), _runner(), run_dir, resume=True
    )
    report = resumed.run()
    final_lines = resumed.episodes_path.read_text(encoding="utf-8").splitlines()

    assert partial.actual_episode_count == 2
    assert report.complete is True
    assert report.aggregate is not None
    assert report.aggregate.task_success_rate == 1
    assert final_lines[:2] == previous_lines
    assert len(final_lines) == 4
    assert json.loads((run_dir / "bad_cases.json").read_text(encoding="utf-8")) == []


def test_todo_15d_complete_report_exposes_model_retry_request_accounting(
    tmp_path,
) -> None:
    """离线替身不记真实请求，但报告仍暴露模型回合计数口径。"""

    report = ResumableFormalRunner(_config(), _cases(), _runner(), tmp_path / "formal").run()

    assert report.complete is True
    assert report.requests_started == report.responses_finished == 0
    assert report.expected_request_count == 0
    records = [
        FormalEpisodeRecord.model_validate_json(line).episode
        for line in (tmp_path / "formal" / "episodes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert report.model_request_count == sum(
        len(episode.result.model_calls) for episode in records
    )


def test_todo_15d_complete_report_rejects_ledger_count_mismatch(tmp_path) -> None:
    report = ResumableFormalRunner(_config(), _cases(), _runner(), tmp_path / "formal").run()
    payload = report.model_dump(mode="json")
    payload["requests_started"] = 1
    payload["responses_finished"] = 1
    with pytest.raises(ValueError, match="账本请求数"):
        FormalRunReport.model_validate(payload)


def test_todo_15d_live_provider_counts_model_retries_in_ledger(
    tmp_path, monkeypatch
) -> None:
    """真实 Provider 口径：模型回合数加 prior retry，少一或多一都拒绝。"""

    case = _cases()[0]
    config = _config(repeats=1).model_copy(update={"provider": "deepseek"})
    formal = ResumableFormalRunner(config, [case], _runner(), tmp_path / "formal")
    # 15d 直接测试只验证报告/账本边界；评分实现属于前置 TODO14b/14c，使用
    # 最小替身避免前置 NotImplementedError 遮蔽当前题反馈。
    monkeypatch.setattr(
        "kaiji.formal_runner.aggregate_scores",
        lambda cases, episodes: formal_report_aggregate(),
    )
    monkeypatch.setattr(
        "kaiji.formal_runner.score_episode",
        lambda case, episode: type("Score", (), {"task_success": True})(),
    )
    original_call = ModelCallResult(
        action=ModelAction(kind="final_answer", final_answer="收到"),
        metadata=ModelCallMetadata(model="deepseek", latency_ms=1),
    )
    prior_failure = ProviderAttemptFailure(
        failure=ProviderFailure(
            code=ProviderErrorCode.TRANSPORT_ERROR,
            message="受控传输失败",
            retryable=True,
        ),
        latency_ms=1,
        request_id="prior-1",
    )
    retried_call = original_call.model_copy(
        update={"prior_attempt_failures": [prior_failure]}
    )
    episode = _minimal_episode(case, retried_call)
    records = []
    formal._append(episode, records)
    first_attempt = ModelCallResult(
        failure=prior_failure.failure,
        metadata=original_call.metadata,
    )
    final_attempt = retried_call.model_copy(update={"prior_attempt_failures": []})
    formal.ledger.record_started()
    formal.ledger.record_finished(first_attempt)
    formal.ledger.record_started()
    formal.ledger.record_finished(final_attempt)

    report = formal._report(records, stop_reason=None, persist=False)
    assert report.complete is True
    assert report.model_request_count == 2
    assert report.expected_request_count == 2
    assert report.requests_started == report.responses_finished == 2

    # 独立模型只知道 offline/live 期望计数，不能从 provider 身份推导 real 语义；
    # 将其投影成离线报告仍是结构合法的，真正的 provider 约束由 _report 负责。
    offline_projection = report.model_dump(mode="json")
    offline_projection.update(
        {"requests_started": 0, "responses_finished": 0, "expected_request_count": 0}
    )
    assert FormalRunReport.model_validate(offline_projection).expected_request_count == 0

    for count in (1, 3):
        formal.ledger.state.requests_started = count
        formal.ledger.state.responses_finished = count
        with pytest.raises(ValueError, match="账本请求数"):
            formal._report(records, stop_reason=None, persist=False)


def formal_report_aggregate():
    """构造只供 15d 账本测试使用的聚合替身。"""

    from kaiji.eval_scorer import AgentAggregateReport

    return AgentAggregateReport(
        episode_count=1,
        task_success_rate=1,
        tool_selection_accuracy=0,
        argument_accuracy=0,
        no_tool_accuracy=1,
        retry_recovery_rate=0,
        citation_precision=0,
        citation_recall=0,
        average_steps=1,
        total_input_tokens=0,
        total_output_tokens=0,
        total_tokens=0,
        average_latency_ms=1,
        p50_latency_ms=1,
        p95_latency_ms=1,
        stop_reason_distribution={},
        failure_distribution={},
    )


def test_resume_rejects_changed_dataset_or_config(tmp_path) -> None:
    run_dir = tmp_path / "formal"
    ResumableFormalRunner(_config(), _cases(), _runner(), run_dir).run(
        max_new_episodes=1
    )
    changed_case = _cases()[0].model_copy(update={"prompt": "被修改的题目"})

    with pytest.raises(ValueError, match="数据集 hash"):
        ResumableFormalRunner(
            _config(), [changed_case, _cases()[1]], _runner(), run_dir, resume=True
        )
    with pytest.raises(ValueError, match="配置正文|配置 hash"):
        ResumableFormalRunner(
            _config(repeats=3), _cases(), _runner(), run_dir, resume=True
        )


def test_unexpected_interruption_writes_partial_report_then_resumes(tmp_path) -> None:
    run_dir = tmp_path / "formal"
    interrupted = ResumableFormalRunner(
        _config(), _cases(), InterruptAfterOne(_runner()), run_dir
    )

    with pytest.raises(RuntimeError, match="模拟中断"):
        interrupted.run()
    partial = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    first_line = interrupted.episodes_path.read_text(encoding="utf-8").splitlines()[0]

    assert partial["complete"] is False
    assert partial["stop_reason"] == "interrupted"
    assert partial["actual_episode_count"] == 1
    assert partial["aggregate"] is None

    resumed = ResumableFormalRunner(
        _config(), _cases(), _runner(), run_dir, resume=True
    )
    final = resumed.run()
    assert final.complete is True
    assert (
        resumed.episodes_path.read_text(encoding="utf-8").splitlines()[0] == first_line
    )


def test_resume_rejects_tampered_or_duplicate_episode(tmp_path) -> None:
    run_dir = tmp_path / "formal"
    formal = ResumableFormalRunner(_config(), _cases(), _runner(), run_dir)
    formal.run(max_new_episodes=1)
    line = formal.episodes_path.read_text(encoding="utf-8").strip()
    formal.episodes_path.write_text(line + "\n" + line + "\n", encoding="utf-8")

    resumed = ResumableFormalRunner(
        _config(), _cases(), _runner(), run_dir, resume=True
    )
    with pytest.raises(ValueError, match="顺序损坏|非法或重复"):
        resumed.run()


def test_resume_rejects_tampered_manifest_body(tmp_path) -> None:
    run_dir = tmp_path / "formal"
    ResumableFormalRunner(_config(), _cases(), _runner(), run_dir).run(
        max_new_episodes=0
    )
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["config"]["request_limit"] += 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="配置正文"):
        ResumableFormalRunner(_config(), _cases(), _runner(), run_dir, resume=True)


def test_resume_rejects_episode_suite_even_with_recomputed_record_hash(
    tmp_path,
) -> None:
    run_dir = tmp_path / "formal"
    formal = ResumableFormalRunner(_config(), _cases(), _runner(), run_dir)
    formal.run(max_new_episodes=1)
    record = json.loads(formal.episodes_path.read_text(encoding="utf-8"))
    record["episode"]["suite"] = "reliability"
    payload = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = _fingerprint(payload)
    formal.episodes_path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    resumed = ResumableFormalRunner(
        _config(), _cases(), _runner(), run_dir, resume=True
    )
    with pytest.raises(ValueError, match="suite 与 case"):
        resumed.run()


def test_resume_requires_persistent_request_ledger(tmp_path) -> None:
    run_dir = tmp_path / "formal"
    ResumableFormalRunner(_config(), _cases(), _runner(), run_dir).run(
        max_new_episodes=0
    )
    (run_dir / "request-ledger.json").unlink()

    with pytest.raises(FileNotFoundError, match="request-ledger"):
        ResumableFormalRunner(_config(), _cases(), _runner(), run_dir, resume=True)


def test_request_ledger_counts_started_before_finished_and_restores(tmp_path) -> None:
    path = tmp_path / "request-ledger.json"
    ledger = PersistentRequestLedger(path)
    ledger.record_started()

    restored = PersistentRequestLedger(path)
    assert restored.state.requests_started == 1
    assert restored.state.responses_finished == 0

    restored.record_finished(
        ModelCallResult(
            action=ModelAction(kind="final_answer", final_answer="ok"),
            metadata=ModelCallMetadata(
                model="mock",
                latency_ms=1,
                usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            ),
        )
    )
    again = PersistentRequestLedger(path)
    assert again.state.requests_started == again.state.responses_finished == 1
    assert again.state.input_tokens == 10
    assert again.state.output_tokens == 5


def test_request_ledger_rejects_finish_without_started_request(tmp_path) -> None:
    ledger = PersistentRequestLedger(tmp_path / "request-ledger.json")
    result = ModelCallResult(
        action=ModelAction(kind="final_answer", final_answer="ok"),
        metadata=ModelCallMetadata(model="mock", latency_ms=1),
    )

    with pytest.raises(ValueError, match="没有对应 started"):
        ledger.record_finished(result)
    assert ledger.state.requests_started == ledger.state.responses_finished == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "requests_started": True},
        {"schema_version": 1, "requests_started": "1"},
        {"schema_version": 1, "unexpected": 1},
    ],
)
def test_request_ledger_rejects_coerced_counts_and_unknown_fields(
    tmp_path,
    payload,
) -> None:
    path = tmp_path / "request-ledger.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        PersistentRequestLedger(path)


def test_unresolved_request_blocks_real_resume_without_network(tmp_path) -> None:
    """未知响应可能含未记账 Token，恢复时不得把它当零成本继续。"""

    ledger = PersistentRequestLedger(tmp_path / "request-ledger.json")
    ledger.record_started()
    config = _config()

    _, stop_check = build_deepseek_behavior_runner(
        config,
        ledger,
        api_key_env="DUMMY_ENV_NAME",
    )

    assert stop_check() == "unresolved_request"


@pytest.mark.parametrize(
    "field,value",
    [
        ("repeats", True),
        ("max_tokens", True),
        ("request_limit", True),
        ("cost_limit_usd", True),
    ],
)
def test_formal_config_rejects_bool_safety_boundaries(field, value) -> None:
    payload = _config().model_dump()
    payload[field] = value

    with pytest.raises(ValueError, match="不得为 bool"):
        FormalRunConfig.model_validate(payload)


@pytest.mark.parametrize("field", ["repeats", "max_tokens", "request_limit"])
def test_formal_config_rejects_string_integer_boundaries(field) -> None:
    payload = _config().model_dump()
    payload[field] = "3"
    with pytest.raises(ValueError):
        FormalRunConfig.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature", float("inf")),
        ("input_cost_per_million", float("inf")),
        ("output_cost_per_million", float("nan")),
    ],
)
def test_formal_config_rejects_non_finite_numeric_values(field, value) -> None:
    payload = _config().model_dump()
    payload[field] = value

    with pytest.raises(ValueError):
        FormalRunConfig.model_validate(payload)


def test_formal_rejects_suite_mismatch(tmp_path) -> None:
    with pytest.raises(ValueError, match="suite 与配置"):
        ResumableFormalRunner(
            _config(),
            [_cases()[0].model_copy(update={"suite": "reliability"})],
            _runner(),
            tmp_path / "formal",
        )


def test_preflight_writes_separate_120_and_60_episode_reports(tmp_path) -> None:
    root = Path(__file__).resolve().parents[1]

    report = run_formal_preflight(root, tmp_path)

    assert report.real_behavior.complete is True
    assert report.real_behavior.actual_episode_count == 120
    assert report.real_behavior.aggregate.task_success_rate == 1
    assert report.reliability.complete is True
    assert report.reliability.actual_episode_count == 60
    assert report.reliability.aggregate.task_success_rate == pytest.approx(0.9)
    assert report.real_behavior.dataset_hash != report.reliability.dataset_hash
    assert report.real_behavior.requests_started == 0
    assert report.reliability.requests_started == 0


def test_offline_rescore_is_dry_by_default_and_migrates_with_backups(tmp_path) -> None:
    """重评分不装配 Provider；显式写入前必须保留旧报告和 Bad Case。"""

    run_dir = tmp_path / "formal"
    original = ResumableFormalRunner(
        _config(repeats=1),
        _cases(),
        _runner(),
        run_dir,
    ).run()
    ledger_before = (run_dir / "request-ledger.json").read_bytes()
    stale_report = b'{"legacy_metric_denominator": true}\n'
    stale_bad_cases = b'[{"legacy_score": true}]\n'
    (run_dir / "report.json").write_bytes(stale_report)
    (run_dir / "bad_cases.json").write_bytes(stale_bad_cases)

    dry = rescore_formal_run(run_dir, _cases())

    assert dry.complete is True
    assert dry.aggregate is not None
    assert dry.aggregate.no_tool_accuracy == 1.0
    assert (run_dir / "report.json").read_bytes() == stale_report
    assert (run_dir / "bad_cases.json").read_bytes() == stale_bad_cases
    assert not (run_dir / "report.before-rescore.json").exists()
    assert (run_dir / "request-ledger.json").read_bytes() == ledger_before

    migrated = rescore_formal_run(run_dir, _cases(), persist=True)

    assert migrated.experiment_id == original.experiment_id
    assert migrated.aggregate is not None
    assert migrated.aggregate.no_tool_accuracy == 1.0
    assert (run_dir / "report.before-rescore.json").read_bytes() == stale_report
    assert (run_dir / "bad_cases.before-rescore.json").read_bytes() == stale_bad_cases
    assert (
        json.loads((run_dir / "report.json").read_text(encoding="utf-8"))["aggregate"][
            "no_tool_accuracy"
        ]
        == 1.0
    )
    assert (run_dir / "request-ledger.json").read_bytes() == ledger_before


def test_offline_rescore_rejects_partial_matrix_without_rewriting(tmp_path) -> None:
    run_dir = tmp_path / "partial"
    ResumableFormalRunner(
        _config(),
        _cases(),
        _runner(),
        run_dir,
    ).run(max_new_episodes=1)
    old_report = (run_dir / "report.json").read_bytes()

    with pytest.raises(ValueError, match="完整 Formal"):
        rescore_formal_run(run_dir, _cases(), persist=True)

    assert (run_dir / "report.json").read_bytes() == old_report
    assert not (run_dir / "report.before-rescore.json").exists()
