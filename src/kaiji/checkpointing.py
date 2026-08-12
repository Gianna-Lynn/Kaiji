"""集成官方 SQLite Checkpointer，并明确连接生命周期和本地适用边界。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def sqlite_checkpointer(path: Path) -> Iterator[SqliteSaver]:
    """打开本地同步 SQLite Saver，并在退出时关闭其数据库连接。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        yield saver
