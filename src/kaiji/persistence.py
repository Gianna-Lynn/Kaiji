"""提供 JSON Checkpoint 与最小长期 Memory 的持久化教学实现。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


class Checkpoint(BaseModel):
    """保存某线程在特定 checkpoint_id 下的完整可恢复状态。"""

    thread_id: str
    checkpoint_id: str
    state: dict[str, Any]


class JsonCheckpointStore:
    """保存完整执行快照；它不等同于长期语义 Memory。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, checkpoint: Checkpoint) -> None:
        """原子保存或覆盖指定线程和编号的快照。"""
        # TODO 8a：在 thread_id/checkpoint_id 下写入 state，并原子落盘。
        del checkpoint

    def load(self, thread_id: str, checkpoint_id: str) -> Checkpoint | None:
        """按线程和编号恢复快照；不存在时返回 None。"""
        # TODO 8b：不存在时返回 None，否则重建 Checkpoint。
        del thread_id, checkpoint_id
        return None

    def latest(self, thread_id: str) -> Checkpoint | None:
        """恢复某线程最后写入的快照。"""
        # TODO 8c：JSON 保留插入顺序，因此恢复最后一个 checkpoint。
        del thread_id
        return None

    def _read_all(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))


class MemoryEntry(BaseModel):
    """表示应用策略选择长期保留的一条用户事实。"""

    key: str
    value: str
    tags: list[str] = Field(default_factory=list)


class JsonMemoryStore:
    """保存应用选择的少量持久事实，而不是完整运行状态。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def put(self, user_id: str, entry: MemoryEntry) -> None:
        """按用户和键保存一条精选事实。"""
        # TODO 9a：在 user_id/key 下持久化一条精选事实。
        del user_id, entry

    def get(self, user_id: str, key: str) -> MemoryEntry | None:
        """恢复并校验一条事实；不存在时返回 None。"""
        # TODO 9b：恢复并校验一个 MemoryEntry，不存在时返回 None。
        del user_id, key
        return None

    def list(self, user_id: str) -> list[MemoryEntry]:
        """返回指定用户的全部已校验事实。"""
        # TODO 9c：返回某个用户的所有已校验事实。
        del user_id
        return []

    def _read_all(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))
