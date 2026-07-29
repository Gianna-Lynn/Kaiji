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
    thread_id: str
    checkpoint_id: str
    state: dict[str, Any]


class JsonCheckpointStore:
    """Full execution snapshots. This is not long-term semantic memory."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, checkpoint: Checkpoint) -> None:
        data = self._read_all()
        data.setdefault(checkpoint.thread_id, {})[checkpoint.checkpoint_id] = (
            checkpoint.state
        )
        _write_json_atomic(self._path, data)

    def load(self, thread_id: str, checkpoint_id: str) -> Checkpoint | None:
        state = self._read_all().get(thread_id, {}).get(checkpoint_id)
        if state is None:
            return None
        return Checkpoint(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            state=state,
        )

    def latest(self, thread_id: str) -> Checkpoint | None:
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
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))


class MemoryEntry(BaseModel):
    key: str
    value: str
    tags: list[str] = Field(default_factory=list)


class JsonMemoryStore:
    """Small durable facts selected by application policy, not full state."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def put(self, user_id: str, entry: MemoryEntry) -> None:
        data = self._read_all()
        data.setdefault(user_id, {})[entry.key] = entry.model_dump()
        _write_json_atomic(self._path, data)

    def get(self, user_id: str, key: str) -> MemoryEntry | None:
        payload = self._read_all().get(user_id, {}).get(key)
        return MemoryEntry.model_validate(payload) if payload else None

    def list(self, user_id: str) -> list[MemoryEntry]:
        return [
            MemoryEntry.model_validate(item)
            for item in self._read_all().get(user_id, {}).values()
        ]

    def _read_all(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))
