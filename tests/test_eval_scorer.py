"""验证 Scorer 固定分母、证据引用和失败分类。"""

from kaiji.demo import ScriptedDecisionProvider
from kaiji.eval_runner import AgentEpisode, AgentEvalCase
from kaiji.eval_scorer import aggregate_scores, score_episode
from kaiji.models import (
    ModelAction,
    ModelCallMetadata,
    ModelCallResult,
    ModelUsage,
    ProviderAttemptFailure,
    ProviderErrorCode,
    ProviderFailure,
    StopReason,
    ToolCall,
)
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolDefinition, ToolExecutionError, ToolExecutor, ToolRegistry
from pydantic import BaseModel


class SearchArgs(BaseModel):
    query: str


class SearchResult(BaseModel):
    hits: list[dict]


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="search_azusa",
            description="搜索证据",
            arguments_model=SearchArgs,
            result_model=SearchResult,
            handler=lambda args: {
                "hits": [{"chunk_id": "kaiji-001", "text": "Kaiji 使用工具。"}]
            },
        )
    )
    return registry


def _case() -> AgentEvalCase:
    return AgentEvalCase(
        case_id="citation-1",
        semantic_group="citation",
        suite="behavior",
        prompt="Kaiji 如何工作？",
        expected_stop_reason=StopReason.COMPLETED,
        expected_tool="search_azusa",
        expected_arguments={"query": "Kaiji"},
        required_answer_facts=["使用工具"],
        allowed_citation_ids=["kaiji-001"],
    )


def _episode(
    answer: str,
    *,
    arguments: dict | None = None,
    registry: ToolRegistry | None = None,
    case: AgentEvalCase | None = None,
):
    selected_case = case or _case()
    actions = [
        ModelAction(
            kind="tool_call",
            tool_call=ToolCall(
                call_id="search-1",
                name="search_azusa",
                arguments=arguments or {"query": "Kaiji"},
            ),
        ),
        ModelAction(kind="final_answer", final_answer=answer),
    ]
    result = AgentRuntime(
        ScriptedDecisionProvider(actions.copy()),
        ToolExecutor(registry or _registry()),
    ).run(selected_case.prompt)
    return AgentEpisode(
        run_id="score-run",
        case_id=selected_case.case_id,
        suite=selected_case.suite,
        repeat_index=0,
        latency_ms=1.0,
        result=result,
    )


def test_todo_14b_scorer_derives_success_from_result_not_runner_claim() -> None:
    episode = _episode("Kaiji 使用工具。\nCitations: kaiji-001")

    score = score_episode(_case(), episode)

    assert score.task_success is True
    assert score.arguments_correct is True
    assert score.citation_precision == 1.0
    assert score.citation_recall == 1.0


def test_todo_14b_failed_or_unallowed_citation_cannot_be_success_evidence() -> None:
    episode = _episode("Kaiji 使用工具。\nCitations: invented-999")

    score = score_episode(_case(), episode)

    assert score.task_success is False
    assert score.citation_supported is False
    assert score.citation_precision == 0.0
    assert score.citation_recall == 0.0
    assert "citation" in score.failure_categories


def test_todo_14b_empty_citations_and_empty_allowed_set_are_neutral() -> None:
    case = _case().model_copy(update={"allowed_citation_ids": []})
    score = score_episode(case, _episode("Kaiji 使用工具。", case=case))

    assert score.citation_precision == 1.0
    assert score.citation_recall == 1.0
    assert score.citation_supported is True


def test_todo_14b_missing_required_citations_has_zero_precision_and_recall() -> None:
    score = score_episode(_case(), _episode("Kaiji 使用工具。"))

    assert score.citation_precision == 0.0
    assert score.citation_recall == 0.0
    assert score.citation_supported is False


def test_todo_14b_unrequested_citation_has_zero_precision_but_neutral_recall() -> None:
    case = _case().model_copy(update={"allowed_citation_ids": []})
    score = score_episode(
        case,
        _episode("Kaiji 使用工具。\nCitations: kaiji-001", case=case),
    )

    assert score.citation_precision == 0.0
    assert score.citation_recall == 1.0
    assert score.citation_supported is False


def test_todo_14b_required_answer_fact_does_not_accept_alphanumeric_fragment() -> None:
    """事实是规范化字面量，不能把 190 当成独立事实 90。"""

    case = _case().model_copy(update={"required_answer_facts": ["90"]})
    score = score_episode(case, _episode("结果是 190。", case=case))

    assert score.answer_facts_correct is False
    assert score.task_success is False
    assert "answer_facts" in score.failure_categories

    normalized_case = _case().model_copy(update={"required_answer_facts": ["９０"]})
    normalized_score = score_episode(
        normalized_case,
        _episode("结果是 １９０。", case=normalized_case),
    )
    assert normalized_score.answer_facts_correct is False


def test_todo_14b_required_answer_fact_allows_cjk_and_mixed_script_boundaries() -> None:
    """事实边界只约束 ASCII 标识，连续中文和中英混排仍可命中。"""

    score = score_episode(_case(), _episode("Kaiji使用工具。"))

    assert score.answer_facts_correct is True


def test_todo_14b_retry_recovery_counts_executor_retry_but_not_provider_retry() -> None:
    """重试恢复只统计 Executor retry；Provider transport retry 不算业务恢复。"""

    attempts = 0

    def transient_handler(_args: SearchArgs) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ToolExecutionError("temporary", retryable=True)
        return {"hits": [{"chunk_id": "kaiji-001", "text": "Kaiji 使用工具。"}]}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="search_azusa",
            description="搜索证据",
            arguments_model=SearchArgs,
            result_model=SearchResult,
            handler=transient_handler,
            max_retries=1,
        )
    )
    retry_case = _case().model_copy(
        update={"case_id": "retry-case", "expects_retry_recovery": True}
    )
    recovered = _episode(
        "Kaiji 使用工具。\nCitations: kaiji-001",
        registry=registry,
        case=retry_case,
    )
    score = score_episode(retry_case, recovered)
    assert recovered.result.messages[-1].tool_result.attempts == 2
    assert score.retry_recovered is True

    class ProviderRetryOnly:
        """在模型回合元数据中记录 transport retry，但不重试工具。"""

        def __init__(self) -> None:
            self._actions = [
                ModelAction(
                    kind="tool_call",
                    tool_call=ToolCall(
                        call_id="provider-retry",
                        name="search_azusa",
                        arguments={"query": "Kaiji"},
                    ),
                ),
                ModelAction(kind="final_answer", final_answer="Kaiji 使用工具。"),
            ]
            self._index = 0

        def decide(self, _messages):
            action = self._actions[self._index]
            self._index += 1
            prior = []
            if self._index == 1:
                prior = [
                    ProviderAttemptFailure(
                        failure=ProviderFailure(
                            code=ProviderErrorCode.TRANSPORT_ERROR,
                            message="temporary",
                            retryable=True,
                        ),
                        latency_ms=0.1,
                    )
                ]
            return ModelCallResult(
                action=action,
                metadata=ModelCallMetadata(
                    model="mock",
                    latency_ms=0.1,
                    usage=ModelUsage(),
                ),
                prior_attempt_failures=prior,
            )

    provider_result = AgentRuntime(
        ProviderRetryOnly(),
        ToolExecutor(_registry()),
    ).run(retry_case.prompt)
    provider_episode = AgentEpisode(
        run_id="provider-retry-run",
        case_id=retry_case.case_id,
        suite=retry_case.suite,
        latency_ms=1.0,
        result=provider_result,
    )
    assert score_episode(retry_case, provider_episode).retry_recovered is False


def test_todo_14c_aggregate_requires_every_case_and_unique_run_ids() -> None:
    case = _case()
    episode = _episode("Kaiji 使用工具。\nCitations: kaiji-001")
    report = aggregate_scores([case], [episode])

    assert report.episode_count == 1
    assert report.task_success_rate == 1.0
    assert report.total_tokens == 0
    assert report.p50_latency_ms == episode.latency_ms
    assert report.p95_latency_ms == episode.latency_ms

    import pytest

    with pytest.raises(ValueError, match="缺少 case"):
        aggregate_scores(
            [case, case.model_copy(update={"case_id": "missing"})], [episode]
        )
    with pytest.raises(ValueError, match="run_id 必须唯一"):
        aggregate_scores([case], [episode, episode])
    with pytest.raises(ValueError, match="suite"):
        aggregate_scores(
            [case],
            [episode.model_copy(update={"suite": "wrong-suite"})],
        )


def test_todo_14b_metadata_chunk_id_cannot_masquerade_as_success_evidence() -> None:
    """只有证据外壳的 chunk_id 可被引用，metadata 同名字段必须忽略。"""

    episode = _episode("Kaiji 使用工具。\nCitations: invented-999")
    episode.result.successful_tool_results[0].output["hits"][0]["metadata"] = {
        "chunk_id": "invented-999"
    }

    score = score_episode(_case(), episode)

    assert score.citation_supported is False
    assert score.task_success is False


def test_todo_14c_aggregate_uses_only_applicable_metric_denominators() -> None:
    """不适用样本不得稀释重试、引用、工具选择或无工具指标。"""

    citation_case = _case()
    bad_citation = _episode("Kaiji 使用工具。\nCitations: invented-999")
    retry_case = citation_case.model_copy(
        update={"case_id": "retry-1", "expects_retry_recovery": True}
    )
    retry_episode = _episode("Kaiji 使用工具。\nCitations: kaiji-001").model_copy(
        update={"run_id": "retry-run", "case_id": "retry-1"}
    )
    no_tool_case = AgentEvalCase(
        case_id="no-tool-1",
        semantic_group="no-tool",
        suite="behavior",
        prompt="直接回答。",
        expected_stop_reason=StopReason.COMPLETED,
        expects_no_tool=True,
    )
    no_tool_result = AgentRuntime(
        ScriptedDecisionProvider(
            [ModelAction(kind="final_answer", final_answer="收到")]
        ),
        ToolExecutor(_registry()),
    ).run(no_tool_case.prompt)
    no_tool_episode = AgentEpisode(
        run_id="no-tool-run",
        case_id=no_tool_case.case_id,
        suite=no_tool_case.suite,
        repeat_index=0,
        latency_ms=1.0,
        result=no_tool_result,
    )

    report = aggregate_scores(
        [citation_case, retry_case, no_tool_case],
        [bad_citation, retry_episode, no_tool_episode],
    )

    assert report.tool_selection_accuracy == 1.0
    assert report.argument_accuracy == 1.0
    assert report.no_tool_accuracy == 1.0
    assert report.retry_recovery_rate == 0.0
    assert report.citation_precision == 0.5
    assert report.citation_recall == 0.5
