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
        # TODO 10a: Create the parent and append exactly one JSON object per line.
        del record

    def load(self, run_id: str) -> TraceRecord | None:
        # TODO 10b: Find a record by run_id without changing file order.
        del run_id
        return None

    def iter_records(self) -> list[TraceRecord]:
        # TODO 10c: Missing files are empty stores; validate every nonblank line.
        return []

    def replay_summary(self, run_id: str) -> list[str]:
        # TODO 10d: Raise KeyError for an unknown run and produce stable,
        # human-readable step/event/data lines for a known run.
        del run_id
        return []
