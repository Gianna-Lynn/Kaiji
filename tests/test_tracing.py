import pytest

from kaiji.models import StopReason, TraceEvent
from kaiji.tracing import JsonlTraceStore, TraceRecord


def test_trace_can_be_saved_loaded_and_replayed(tmp_path) -> None:
    store = JsonlTraceStore(tmp_path / "trace.jsonl")
    record = TraceRecord(
        run_id="run-1",
        events=[
            TraceEvent(step=1, event="model_action", data={"kind": "tool_call"}),
            TraceEvent(step=1, event="stop", data={"reason": "completed"}),
        ],
        stop_reason=StopReason.COMPLETED,
    )

    store.append(record)

    assert store.load("run-1") == record
    assert "event=model_action" in store.replay_summary("run-1")[0]


def test_replay_rejects_unknown_run(tmp_path) -> None:
    store = JsonlTraceStore(tmp_path / "trace.jsonl")

    with pytest.raises(KeyError, match="unknown run_id"):
        store.replay_summary("missing")
