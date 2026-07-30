"""提供轻量 JSON 检查点与长期事实存储，服务于教学和本地运行。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _write_json_atomic(path: Path, payload: Any) -> None:
    """先写临时文件再原子替换目标，降低中途失败造成的数据损坏风险。"""

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
    """保存完整执行快照；它不承担长期语义记忆职责。"""

    def __init__(self, path: Path) -> None:
        """指定检查点 JSON 文件的位置。"""

        self._path = path

    def save(self, checkpoint: Checkpoint) -> None:
        """按线程和检查点标识保存或覆盖一份执行快照。"""

        data = self._read_all()
        data.setdefault(checkpoint.thread_id, {})[checkpoint.checkpoint_id] = (
            checkpoint.state
        )
        _write_json_atomic(self._path, data)

    def load(self, thread_id: str, checkpoint_id: str) -> Checkpoint | None:
        """读取指定检查点；找不到时返回空值。"""

        state = self._read_all().get(thread_id, {}).get(checkpoint_id)
        if state is None:
            return None
        return Checkpoint(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            state=state,
        )

    def latest(self, thread_id: str) -> Checkpoint | None:
        """读取指定线程最近保存的检查点。"""

        checkpoints = self._read_all().get(thread_id, {})
        if not checkpoints:
            return None
        checkpoint_id = next(reversed(checkpoints))
        return Checkpoint(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            state=checkpoints[checkpoint_id],
        )

    def _read_all(self) -> dict[str, dict[str, dict[str, Any]]]:
        """读取全部检查点数据；文件不存在时返回空字典。"""

        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))


class MemoryEntry(BaseModel):
    """表示由应用策略筛选后允许长期保存的一条事实。"""

    key: str
    value: str
    tags: list[str] = Field(default_factory=list)


class JsonMemoryStore:
    """保存应用策略选中的少量持久事实，而不是完整运行状态。"""

    def __init__(self, path: Path) -> None:
        """指定长期事实 JSON 文件的位置。"""

        self._path = path

    def put(self, user_id: str, entry: MemoryEntry) -> None:
        """按用户和键保存或覆盖一条长期事实。"""

        data = self._read_all()
        data.setdefault(user_id, {})[entry.key] = entry.model_dump()
        _write_json_atomic(self._path, data)

    def get(self, user_id: str, key: str) -> MemoryEntry | None:
        """读取一条长期事实；找不到时返回空值。"""

        payload = self._read_all().get(user_id, {}).get(key)
        return MemoryEntry.model_validate(payload) if payload else None

    def list(self, user_id: str) -> list[MemoryEntry]:
        """列出指定用户保存的全部长期事实。"""

        return [
            MemoryEntry.model_validate(item)
            for item in self._read_all().get(user_id, {}).values()
        ]

    def _read_all(self) -> dict[str, dict[str, dict[str, Any]]]:
        """读取全部长期事实；文件不存在时返回空字典。"""

        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))
