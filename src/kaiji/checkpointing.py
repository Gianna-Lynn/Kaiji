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

    # path 形如 data/checkpoints/run.sqlite, 或者形如 run.sqlite, 是SQLite数据库文件的文件位置.
    # 获取数据库文件的父目录, 这样就可以写入.
    path.parent.mkdir(parents=True, exist_ok=True) # parents=True: 父目录不存在时, 连上级目录一起创建. exist_ok=True: 目录已经存在时不报错.
    sqlite_saver = SqliteSaver.from_conn_string(str(path))
    with sqlite_saver as saver:
        yield saver
    # sqlite_saver是from_conn_string的返回值. 它是一个上下文管理器,
    # 数据类型是_GeneratorContextManager[SqliteSaver, None, None]
    # 承担的是"管理Saver生命周期"的角色.
    # saver是进入上下文以后得到的真正SqliteSaver实例.

    # 什么是上下文?
    # with ... as ...:  # 这句话的功能是, 进入上下文.
    #    dosomething()  # 这里的dosomething()就是上下文.

    # 什么是上下文管理器?
    # 上下文管理器是一个对象, 负责定义:
    # 1. 进入这段代码之前要做什么
    # 2. 离开这段代码之后要做什么
    # 3. 中途发生异常时如何清理

    # with语句的一般用途?
    # with语句展开成伪代码, 可以写成如下形式:
    # 打开资源()
    # try:
    #     使用资源()
    # finally:
    #     关闭资源()

    # with语句在这里的用途?
    # 进入 sqlite_saver 管理的上下文；
    # 获取真正的 SqliteSaver，命名为 saver；
    # 在 yield 处把它交给外部；
    # 外部 with 结束后回到这里；
    # 离开内层 with，自动关闭连接

    # yield的作用?
    # yield暂时把一个值交出去, 并暂停当前的函数, 将来再次从这里继续执行.

    # @contextmanager装饰器的作用?
    # 这个装饰器会把整个函数: def sqlite_checkpointer(path: Path)当做一个上下文管理器使用.
    # 注意区别于with sqlite_saver as saver: 这里面创建的小的上下文管理器.
    # 也就是说, def sqlite_checkpointer(path: Path), 被设计成用这种方法调用:
    # with sqlite_checkpointer(...) as saver:
    #     ...
