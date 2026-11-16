"""验证 LangGraph 官方 SQLite Checkpointer 的恢复、审批与状态版本边界。

测试使用临时 SQLite 文件和 MockAzusaAdapter，不依赖网络或真实模型。
覆盖关闭连接后用新 Saver/Graph 恢复、拒绝非 bool 审批值、线程状态隔离，
以及拒绝不受支持的状态版本。跨连接测试在同一进程内执行，不启动新进程；
工作流中的发送和发布也只是审批模拟，不执行真实外部写操作。
"""

import pytest

from kaiji.adapters.azusa import MockAzusaAdapter
from kaiji.checkpointing import sqlite_checkpointer
from kaiji.langgraph_workflow import build_agent_graph, resume_graph, run_graph
from kaiji.models import StopReason


def test_todo_14d_sqlite_resumes_across_fresh_connection(tmp_path) -> None:
    """关闭首次连接后，用新 Saver 和 Graph 读取审批暂停状态并继续运行。

    对应原课程 TODO 14d。先在尚不存在的嵌套目录下创建检查点数据库，
    运行发布请求，确认待审批操作已保存且尚无审批决定。退出第一个 with
    后连接关闭，再用相同数据库路径创建新的 Saver 和 Graph，以相同
    thread_id 读取待审批操作与状态版本，最后提交 True 并检查正常完成。

    sqlite_checkpointer 负责创建父目录，SQLite 创建数据库文件，Saver
    初始化所需表结构。新实例从同一个文件恢复，不依赖旧 Graph 的内存状态。
    本测试不验证真实发布操作，也不验证进程崩溃或断电后的恢复。

    Args:
        tmp_path: pytest 提供的独立临时目录，用于保存本测试的 SQLite 文件。
    """
    database = tmp_path / "nested" / "checkpoints" / "checkpoint.sqlite"
    thread_id = "approval-thread"
    with sqlite_checkpointer(database) as first_saver:
        # with 这一行的意思是"打开数据库连接, 准备好检查点存取对象, 并命名为 first_saver,
        # 退出 with 时关闭连接"。
        assert database.parent.is_dir()
        first_graph = build_agent_graph(MockAzusaAdapter(), checkpointer=first_saver)
        # run_graph 从头开始执行，直到遇到需要审批的操作而暂停。返回值包含暂停时的状态。
        paused = run_graph(first_graph, "publish the report", thread_id=thread_id)
        assert paused["pending_action"] == "publish the report"
        assert paused["approved"] is None

    with sqlite_checkpointer(database) as second_saver:
        second_graph = build_agent_graph(MockAzusaAdapter(), checkpointer=second_saver)
        # 这里不再调用 run_graph，而是直接读取上次暂停的状态，
        # 确认 thread_id 对应的待审批操作和状态版本正确。
        restored = second_graph.get_state(
            {"configurable": {"thread_id": thread_id}}
        ).values
        assert restored["pending_action"] == "publish the report"
        assert restored["state_schema_version"] == 1
        completed = resume_graph(second_graph, thread_id=thread_id, approved=True)

    assert completed["stop_reason"] == StopReason.COMPLETED.value
    assert completed["approved"] is True


def test_sqlite_strict_bool_and_thread_isolation(tmp_path) -> None:
    """拒绝整数审批值，并检查另一线程的待审批状态保持不变。

    在同一 Graph 和数据库中分别暂停 thread-a 与 thread-b。恢复 thread-a
    时传入整数 1，要求抛出 ValueError，不能按真值转换成批准。随后读取
    thread-b，确认它仍对应自己的操作且 approved 为 None，检查这次非法
    审批没有改变另一线程的这两个字段。

    Args:
        tmp_path: pytest 提供的独立临时目录，用于隔离本测试的数据库。
    """

    database = tmp_path / "checkpoint.sqlite"
    with sqlite_checkpointer(database) as saver:
        graph = build_agent_graph(MockAzusaAdapter(), checkpointer=saver)
        run_graph(graph, "send item A", thread_id="thread-a")
        run_graph(graph, "send item B", thread_id="thread-b")
        # with 语句检查抛出的异常是 ValueError，且异常消息包含"必须是 bool"。
        # 因为故意传入整数 1，而不是 True，违反了严格布尔类型约束。
        with pytest.raises(ValueError, match="必须是 bool"):
            resume_graph(graph, thread_id="thread-a", approved=1)
        # 继续读取 thread-b 的状态，确认它仍然是待审批状态，且 approved 字段为 None。
        state_b = graph.get_state({"configurable": {"thread_id": "thread-b"}}).values
        assert state_b["pending_action"] == "send item B"
        assert state_b["approved"] is None


def test_resume_rejects_unsupported_state_schema(tmp_path) -> None:
    """检查点中的状态版本不受支持时，恢复入口必须拒绝继续运行。

    首先生成真实的审批暂停点，
    再通过 update_state 将持久化状态的state_schema_version 改为 0。
    提交合法的 True 审批值时，要求恢复入口抛出包含 Schema 版本说明的 ValueError，
    避免把版本错误归因于审批类型。

    Args:
        tmp_path: pytest 提供的独立临时目录，用于保存被修改版本的检查点。
    """

    database = tmp_path / "checkpoint.sqlite"
    with sqlite_checkpointer(database) as saver:
        graph = build_agent_graph(MockAzusaAdapter(), checkpointer=saver)
        run_graph(graph, "publish old state", thread_id="old-thread")
        # graph.update_state 找到 old-thread 这次运行的状态,
        # 并把它的 state_schema_version 改为 0，模拟不受支持的版本。
        graph.update_state(
            {"configurable": {"thread_id": "old-thread"}},
            {"state_schema_version": 0},
        )
        with pytest.raises(ValueError, match="Schema 版本"):
            # 当resume_graph尝试读取 old-thread 的状态时，发现 state_schema_version 为 0，
            # resume_graph 规定 state_schema_version 必须是受支持的版本（也就是 1），
            # 因此抛出 ValueError。
            resume_graph(graph, thread_id="old-thread", approved=True)
