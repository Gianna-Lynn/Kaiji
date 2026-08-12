"""集成官方 SQLite Checkpointer，并明确连接生命周期和本地适用边界。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def sqlite_checkpointer(path: Path) -> Iterator[SqliteSaver]:
    """打开本地同步 SQLite Saver，并在退出时关闭其数据库连接。"""
    # TODO 14d（L2）：创建父目录，通过官方 from_conn_string 打开同步 Saver，
    # yield 给调用方，并确保离开 context 后连接关闭。不得缓存全局连接或把
    # JsonCheckpointStore 冒充 LangGraph Checkpointer。
    raise NotImplementedError("TODO 14d：管理 SQLite Saver 生命周期")
