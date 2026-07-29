from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from kaiji.models import StopReason, TraceEvent


class TraceRecord(BaseModel):
    run_id: str
    events: list[TraceEvent]
    stop_reason: StopReason
    metadata: dict[str, Any] = Field(default_factory=dict)


class JsonlTraceStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, record: TraceRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(record.model_dump_json())
            stream.write("\n")

    def load(self, run_id: str) -> TraceRecord | None:
        for record in self.iter_records():
            if record.run_id == run_id:
                return record
        return None

    def iter_records(self) -> list[TraceRecord]:
        if not self._path.exists():
            return []
        return [
            TraceRecord.model_validate_json(line)
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def replay_summary(self, run_id: str) -> list[str]:
        record = self.load(run_id)
        if record is None:
            raise KeyError(f"unknown run_id: {run_id}")
        return [
            f"step={event.step} event={event.event} data="
            f"{json.dumps(event.data, ensure_ascii=False, default=str)}"
            for event in record.events
        ]
