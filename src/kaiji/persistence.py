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
        # TODO 8a: Upsert state under thread_id/checkpoint_id and write atomically.
        del checkpoint

    def load(self, thread_id: str, checkpoint_id: str) -> Checkpoint | None:
        # TODO 8b: Return None when absent; otherwise reconstruct Checkpoint.
        del thread_id, checkpoint_id
        return None

    def latest(self, thread_id: str) -> Checkpoint | None:
        # TODO 8c: JSON preserves insertion order. Restore the last checkpoint.
        del thread_id
        return None

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
        # TODO 9a: Persist one selected fact under user_id/key.
        del user_id, entry

    def get(self, user_id: str, key: str) -> MemoryEntry | None:
        # TODO 9b: Restore and validate one MemoryEntry, or return None.
        del user_id, key
        return None

    def list(self, user_id: str) -> list[MemoryEntry]:
        # TODO 9c: Return all validated facts for a user.
        del user_id
        return []

    def _read_all(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))
