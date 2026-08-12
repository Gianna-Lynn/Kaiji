"""验证官方 SQLite Checkpointer 的跨 Graph/连接恢复和线程隔离。"""

import pytest

from kaiji.adapters.azusa import MockAzusaAdapter
from kaiji.checkpointing import sqlite_checkpointer
from kaiji.langgraph_workflow import build_agent_graph, resume_graph, run_graph
from kaiji.models import StopReason


def test_sqlite_resumes_interrupt_across_fresh_graph_and_connection(tmp_path) -> None:
    database = tmp_path / "checkpoint.sqlite"
    thread_id = "approval-thread"
    with sqlite_checkpointer(database) as first_saver:
        first_graph = build_agent_graph(MockAzusaAdapter(), checkpointer=first_saver)
        paused = run_graph(first_graph, "publish the report", thread_id=thread_id)
        assert paused["pending_action"] == "publish the report"
        assert paused["approved"] is None

    with sqlite_checkpointer(database) as second_saver:
        second_graph = build_agent_graph(MockAzusaAdapter(), checkpointer=second_saver)
        restored = second_graph.get_state(
            {"configurable": {"thread_id": thread_id}}
        ).values
        assert restored["pending_action"] == "publish the report"
        assert restored["state_schema_version"] == 1
        completed = resume_graph(second_graph, thread_id=thread_id, approved=True)

    assert completed["stop_reason"] == StopReason.COMPLETED.value
    assert completed["approved"] is True


def test_sqlite_strict_bool_and_thread_isolation(tmp_path) -> None:
    database = tmp_path / "checkpoint.sqlite"
    with sqlite_checkpointer(database) as saver:
        graph = build_agent_graph(MockAzusaAdapter(), checkpointer=saver)
        run_graph(graph, "send item A", thread_id="thread-a")
        run_graph(graph, "send item B", thread_id="thread-b")
        with pytest.raises(ValueError, match="必须是 bool"):
            resume_graph(graph, thread_id="thread-a", approved=1)
        state_b = graph.get_state({"configurable": {"thread_id": "thread-b"}}).values
        assert state_b["pending_action"] == "send item B"
        assert state_b["approved"] is None


def test_resume_rejects_unsupported_state_schema(tmp_path) -> None:
    database = tmp_path / "checkpoint.sqlite"
    with sqlite_checkpointer(database) as saver:
        graph = build_agent_graph(MockAzusaAdapter(), checkpointer=saver)
        run_graph(graph, "publish old state", thread_id="old-thread")
        graph.update_state(
            {"configurable": {"thread_id": "old-thread"}},
            {"state_schema_version": 0},
        )
        with pytest.raises(ValueError, match="Schema 版本"):
            resume_graph(graph, thread_id="old-thread", approved=True)
