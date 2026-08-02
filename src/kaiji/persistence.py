"""提供轻量 JSON 检查点与长期事实存储的教学脚手架。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


_MISSING = object()


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """读取 JSON 对象根；文件不存在时返回空字典，损坏外壳则显式失败。

    这是 TODO 8/9 共用的已给辅助函数。它只校验文件最外层必须是对象；线程、
    用户和记录内部的结构仍由各 Store 的 ``_read_all`` 负责。
    """

    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON 根必须是对象")
    return payload


def _write_json_atomic(path: Path, payload: Any) -> None:
    """先写同目录临时文件再原子替换目标；此辅助结构不属于练习。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


class Checkpoint(BaseModel):
    """表示某个线程在指定检查点保存的完整执行状态。"""

    thread_id: str
    checkpoint_id: str
    state: dict[str, Any]


class JsonCheckpointStore:
    """保存完整执行快照；它不承担长期语义记忆职责。

    JSON 第一层按 ``thread_id`` 隔离，第二层按 ``checkpoint_id`` 保存
    ``state``。``latest`` 的“最新”指最后一次成功调用 ``save`` 的检查点，
    不是 ID 字典序，也不是第一次插入的顺序；覆盖旧 ID 后，该 ID 必须移到
    线程末尾。Store 只校验 ``state`` 是 JSON 对象，具体 AgentState 的业务
    Schema 仍由调用方校验。多进程写锁、冲突合并和临时文件清扫不在本课范围内。
    """

    def __init__(self, path: Path) -> None:
        """指定检查点 JSON 文件的位置。"""

        self._path = path

    def _read_all(self) -> dict[str, dict[str, dict[str, Any]]]:
        """读取全部检查点，并校验线程外壳与每个 ``state`` 都是对象。

        TODO 8a 应复用 ``_read_json_object``。每个线程值必须是对象，每个
        checkpoint 的 ``state`` 也必须是对象；非法 JSON、列表、``null`` 或
        其他损坏结构必须报错，不能被当成空 Store 或未命中。后续保存与恢复
        都依赖这一个读取边界，因此必须先完成它，不能在各方法中另写宽松解析。
        """

        raise NotImplementedError("TODO 8a：实现检查点存储结构校验")

    def save(self, checkpoint: Checkpoint) -> None:
        """保存或覆盖快照，并把它标记为该线程最后一次成功保存。

        TODO 8b 必须先读取并校验旧数据，再按 ``thread_id/checkpoint_id`` 保存
        ``state``。普通字典覆盖旧键不会改变顺序，因此覆盖时还要兑现上面的
        ``latest`` 语义。最后使用已给的 ``_write_json_atomic``；序列化或 replace
        失败时旧目标文件以及旧 ``latest`` 都必须保持不变。
        """

        raise NotImplementedError("TODO 8b：实现检查点原子保存")

    def load(self, thread_id: str, checkpoint_id: str) -> Checkpoint | None:
        """读取指定检查点，找不到时返回 ``None``。

        TODO 8c 必须区分“键不存在”和“键存在但值损坏”：只有前者返回
        ``None``；后者必须显式报错。存在的数据要重新构造 ``Checkpoint``，
        不能未经模型校验直接返回原始字典。
        """

        raise NotImplementedError("TODO 8c：实现指定检查点恢复")

    def latest(self, thread_id: str) -> Checkpoint | None:
        """读取指定线程最后一次成功保存的检查点。

        TODO 8d 对不存在或没有检查点的线程返回 ``None``；否则按保存顺序取
        最后一个 ID，并重新构造 ``Checkpoint``。不得按 ID 排序猜测最新值。
        """

        raise NotImplementedError("TODO 8d：实现最近检查点恢复")


class MemoryEntry(BaseModel):
    """表示由应用策略筛选后允许长期保存的一条事实。"""

    key: str
    value: str
    tags: list[str] = Field(default_factory=list)


class JsonMemoryStore:
    """保存应用策略选中的少量持久事实，而不是完整运行状态。

    JSON 第一层按 ``user_id`` 隔离，第二层按 ``entry.key`` 保存完整
    ``MemoryEntry``。相同用户和键覆盖旧值，但维持该键第一次插入的位置，因此
    ``list`` 按首次插入顺序返回。是否值得长期保存由调用方决定；Store 不分析
    Message，也不自动从 AgentState 提取事实。多进程并发写入不在本课范围内。
    """

    def __init__(self, path: Path) -> None:
        """指定长期事实 JSON 文件的位置。"""

        self._path = path

    def _read_all(self) -> dict[str, dict[str, Any]]:
        """读取全部事实，并校验用户外壳、记录 Schema 与内外键一致性。

        TODO 9a 应复用 ``_read_json_object``。每个用户对应的值必须是对象；还要
        逐条构造 ``MemoryEntry`` 并确认外层 key 等于 ``entry.key``。非法 JSON、
        列表、``null``、字段错误或内外键冲突都必须报错，不能被当成空 Store，
        也不能让后续 ``put`` 在损坏数据上继续写入。后续方法统一复用这个边界。
        """

        raise NotImplementedError("TODO 9a：实现长期事实存储结构校验")

    def put(self, user_id: str, entry: MemoryEntry) -> None:
        """按用户和键保存或覆盖一条长期事实。

        TODO 9b 必须先读取并校验旧数据，再把 ``entry.model_dump()`` 保存到
        ``user_id/entry.key``，最后复用原子写入辅助函数。覆盖不得改变该键的
        首次插入位置，不同用户即使键相同也不能串写。
        """

        raise NotImplementedError("TODO 9b：实现长期事实保存")

    def get(self, user_id: str, key: str) -> MemoryEntry | None:
        """读取并校验一条长期事实，找不到时返回 ``None``。

        TODO 9c 只有在外层键真正不存在时返回 ``None``；已存在但为 ``null``、
        空对象或字段非法的记录必须让 ``MemoryEntry`` 校验报错。存储用的外层
        ``key`` 还必须等于记录内部 ``entry.key``，否则显式报错。
        """

        raise NotImplementedError("TODO 9c：实现单条长期事实恢复")

    def list(self, user_id: str) -> list[MemoryEntry]:
        """按首次插入顺序返回指定用户的全部已校验事实。

        TODO 9d 对不存在的用户返回空列表；对每条已存在记录都重新执行
        ``MemoryEntry`` 校验和内外 ``key`` 一致性检查。``get`` 与 ``list``
        对同一条损坏记录必须一致报错，不能由其中一条路径掩盖损坏。
        """

        raise NotImplementedError("TODO 9d：实现长期事实列表恢复")
