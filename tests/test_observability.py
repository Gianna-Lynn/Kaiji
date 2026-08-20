"""验证 Trace 到脱敏 span 投影的字段、层级与失败状态。"""

from kaiji.demo import ScriptedDecisionProvider, build_registry
from kaiji.models import ModelAction, StopReason, ToolCall
from kaiji.observability import SpanStatus, project_run_to_spans
from kaiji.runtime import AgentRuntime
from kaiji.tools import ToolExecutor


def _completed_result():
    """构造包含工具调用的确定性成功结果。"""

    provider = ScriptedDecisionProvider(
        [
            ModelAction(
                kind="tool_call",
                tool_call=ToolCall(
                    call_id="span-call",
                    name="get_profile",
                    arguments={"user_id": "user-7"},
                ),
            ),
            ModelAction(kind="final_answer", final_answer="Passenger 7"),
        ]
    )
    return AgentRuntime(provider, ToolExecutor(build_registry())).run("profile")


def test_projection_has_one_root_and_ordered_children() -> None:
    result = _completed_result()

    spans = project_run_to_spans("run-span", result)

    assert len(spans) == len(result.trace) + 1
    assert spans[0].name == "kaiji.agent.run"
    assert spans[0].parent_span_id is None
    assert spans[0].status == SpanStatus.UNSET
    assert all(span.trace_id == spans[0].trace_id for span in spans)
    assert all(span.parent_span_id == spans[0].span_id for span in spans[1:])
    assert [span.name for span in spans[1:]] == [
        f"kaiji.{event.event}" for event in result.trace
    ]


def test_projection_does_not_copy_sensitive_payloads() -> None:
    result = _completed_result()

    serialized = " ".join(
        span.model_dump_json() for span in project_run_to_spans("run-safe", result)
    )

    assert "user-7" not in serialized
    assert "Passenger 7" not in serialized
    assert "arguments" not in serialized
    assert "output" not in serialized
    assert "get_profile" in serialized


def test_non_completed_run_and_failed_event_are_errors() -> None:
    result = AgentRuntime(
        ScriptedDecisionProvider(
            [
                ModelAction(
                    kind="tool_call",
                    tool_call=ToolCall(
                        call_id="missing-call",
                        name="missing",
                        arguments={},
                    ),
                )
            ]
        ),
        ToolExecutor(build_registry()),
    ).run("missing")

    spans = project_run_to_spans("run-failed", result)

    assert result.stop_reason == StopReason.TOOL_FAILED
    assert spans[0].status == SpanStatus.ERROR
    assert [span.status for span in spans if span.name == "kaiji.tool_result"] == [
        SpanStatus.ERROR
    ]
    assert spans[-1].status == SpanStatus.ERROR
