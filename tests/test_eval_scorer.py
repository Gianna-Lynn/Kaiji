"""验证 Scorer 固定分母、证据引用和失败分类。"""

from kaiji.demo import ScriptedDecisionProvider
from kaiji.eval_runner import AgentEvalCase, AgentEvalRunner
from kaiji.eval_scorer import aggregate_scores, score_episode
from kaiji.models import ModelAction, StopReason, ToolCall
from kaiji.tools import ToolDefinition, ToolExecutor, ToolRegistry
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


def _episode(answer: str, *, arguments: dict | None = None):
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
    return AgentEvalRunner(
        lambda case: ScriptedDecisionProvider(actions.copy()),
        lambda case: ToolExecutor(_registry()),
    ).run(_case())


def test_scorer_derives_success_from_result_not_runner_claim() -> None:
    episode = _episode("Kaiji 使用工具。\nCitations: kaiji-001")

    score = score_episode(_case(), episode)

    assert score.task_success is True
    assert score.arguments_correct is True
    assert score.citation_precision == 1.0
    assert score.citation_recall == 1.0


def test_failed_or_unallowed_citation_cannot_be_success_evidence() -> None:
    episode = _episode("Kaiji 使用工具。\nCitations: invented-999")

    score = score_episode(_case(), episode)

    assert score.task_success is False
    assert score.citation_supported is False
    assert score.citation_precision == 0.0
    assert score.citation_recall == 0.0
    assert "citation" in score.failure_categories


def test_aggregate_requires_every_case_and_unique_run_ids() -> None:
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


def test_metadata_chunk_id_cannot_masquerade_as_success_evidence() -> None:
    """只有证据外壳的 chunk_id 可被引用，metadata 同名字段必须忽略。"""

    episode = _episode("Kaiji 使用工具。\nCitations: invented-999")
    episode.result.successful_tool_results[0].output["hits"][0]["metadata"] = {
        "chunk_id": "invented-999"
    }

    score = score_episode(_case(), episode)

    assert score.citation_supported is False
    assert score.task_success is False


def test_aggregate_uses_only_applicable_metric_denominators() -> None:
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
    no_tool_episode = AgentEvalRunner(
        lambda case: ScriptedDecisionProvider(
            [ModelAction(kind="final_answer", final_answer="收到")]
        ),
        lambda case: ToolExecutor(_registry()),
    ).run(no_tool_case)

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
