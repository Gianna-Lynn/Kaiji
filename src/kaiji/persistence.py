"""提供轻量 JSON 检查点与长期事实存储，服务于教学和本地运行。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


_MISSING = object()


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """读取 JSON 对象根；文件不存在时返回空字典，损坏外壳则显式失败。"""

    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON 根必须是对象")
    return payload


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
    """保存完整执行快照；它不承担长期语义记忆职责。

    ``latest`` 的“最新”指最后一次成功调用 ``save`` 的检查点，而不是
    checkpoint_id 的字典序或首次插入顺序。因此覆盖旧 ID 时也会把该 ID
    移到线程末尾。本实现只面向单进程教学和本地运行，并发写入不在课程范围内。
    """

    def __init__(self, path: Path) -> None:
        """指定检查点 JSON 文件的位置。"""

        self._path = path

    def save(self, checkpoint: Checkpoint) -> None:
        """保存或覆盖快照，并把它标记为该线程最后一次成功保存。"""

        data = self._read_all()
        checkpoints = data.setdefault(checkpoint.thread_id, {})
        # 普通字典覆盖旧键不会改变顺序；先删除再写入才能兑现 latest 语义。
        checkpoints.pop(checkpoint.checkpoint_id, None)
        checkpoints[checkpoint.checkpoint_id] = checkpoint.state
        _write_json_atomic(self._path, data)

    def load(self, thread_id: str, checkpoint_id: str) -> Checkpoint | None:
        """读取指定检查点；找不到时返回空值。"""

        state = self._read_all().get(thread_id, {}).get(
            checkpoint_id,
            _MISSING,
        )
        if state is _MISSING:
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
        """读取全部检查点，并校验线程外壳与每个 ``state`` 都是对象。"""

        payload = _read_json_object(self._path, label="Checkpoint")
        checkpoints_by_thread: dict[str, dict[str, dict[str, Any]]] = {}
        for thread_id, checkpoints in payload.items():
            if not isinstance(checkpoints, dict):
                raise ValueError(
                    f"Checkpoint 线程 {thread_id!r} 的内容必须是对象"
                )
            validated_checkpoints: dict[str, dict[str, Any]] = {}
            for checkpoint_id, state in checkpoints.items():
                if not isinstance(state, dict):
                    raise ValueError(
                        "Checkpoint state 必须是对象: "
                        f"{thread_id!r}/{checkpoint_id!r}"
                    )
                validated_checkpoints[checkpoint_id] = state
            checkpoints_by_thread[thread_id] = validated_checkpoints
        return checkpoints_by_thread


class MemoryEntry(BaseModel):
    """表示由应用策略筛选后允许长期保存的一条事实。"""

    key: str
    value: str
    tags: list[str] = Field(default_factory=list)


class JsonMemoryStore:
    """保存应用策略选中的少量持久事实，而不是完整运行状态。

    相同用户和键会覆盖原值，但维持该键首次插入的位置；``list`` 因而按
    首次插入顺序返回。是否值得长期保存由调用方决定，本存储不会分析内容。
    本实现同样只面向单进程教学，并发写入不在课程范围内。
    """

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

        payload = self._read_all().get(user_id, {}).get(key, _MISSING)
        if payload is _MISSING:
            return None
        entry = MemoryEntry.model_validate(payload)
        if entry.key != key:
            raise ValueError("Memory 存储键与记录中的 entry.key 不一致")
        return entry

    def list(self, user_id: str) -> list[MemoryEntry]:
        """列出指定用户保存的全部长期事实。"""

        entries = []
        for key, payload in self._read_all().get(user_id, {}).items():
            entry = MemoryEntry.model_validate(payload)
            if entry.key != key:
                raise ValueError("Memory 存储键与记录中的 entry.key 不一致")
            entries.append(entry)
        return entries

    def _read_all(self) -> dict[str, dict[str, Any]]:
        """读取全部长期事实，并校验根对象和每个用户的记录外壳。"""

        payload = _read_json_object(self._path, label="Memory")
        entries_by_user: dict[str, dict[str, Any]] = {}
        for user_id, entries in payload.items():
            if not isinstance(entries, dict):
                raise ValueError(f"Memory 用户 {user_id!r} 的内容必须是对象")
            entries_by_user[user_id] = entries
        return entries_by_user
