"""验证官方 SQLite Checkpointer 的跨 Graph/连接恢复和线程隔离。"""

import pytest

from kaiji.adapters.azusa import MockAzusaAdapter
from kaiji.checkpointing import sqlite_checkpointer
from kaiji.langgraph_workflow import build_agent_graph, resume_graph, run_graph
from kaiji.models import StopReason


def test_todo_14d_sqlite_resumes_across_fresh_connection(tmp_path) -> None:
    """
    第一次数据库文件不存在，为什么仍然可以运行？
    因为 SQLite 通常支持：
    文件不存在时创建数据库文件；
    数据库文件存在但表不存在时，由 Checkpointer 初始化所需表结构。
    sqlite_checkpointer() 先创建父目录, SQLite再创建数据库文件,
    只要父目录可写, 那么就可以运行.

    第二次全新的 Saver，为什么还能恢复同一个 thread_id？
    因为状态保存的位置不是内存，而是第一次运行使用的 SQLite 文件。
    """
    database = tmp_path / "nested" / "checkpoints" / "checkpoint.sqlite"
    thread_id = "approval-thread"
    with sqlite_checkpointer(database) as first_saver:
        assert database.parent.is_dir()
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
