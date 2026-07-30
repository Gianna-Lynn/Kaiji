"""把 Agent 运行轨迹写入 JSONL，并提供读取和回放摘要能力。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from kaiji.models import StopReason, TraceEvent


class TraceRecord(BaseModel):
    """汇总一次运行的事件、停止原因和附加元数据。"""

    run_id: str
    events: list[TraceEvent]
    stop_reason: StopReason
    metadata: dict[str, Any] = Field(default_factory=dict)


class JsonlTraceStore:
    """使用逐行 JSON 格式持久化和查询运行轨迹。"""

    def __init__(self, path: Path) -> None:
        """指定轨迹 JSONL 文件的位置。"""

        self._path = path

    def append(self, record: TraceRecord) -> None:
        """把一条完整运行轨迹追加到文件末尾。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as stream:
            stream.write(record.model_dump_json())
            stream.write("\n")

    def load(self, run_id: str) -> TraceRecord | None:
        """按运行标识查找轨迹；找不到时返回空值。"""

        for record in self.iter_records():
            if record.run_id == run_id:
                return record
        return None

    def iter_records(self) -> list[TraceRecord]:
        """读取当前文件中的全部有效轨迹。"""

        if not self._path.exists():
            return []
        return [
            TraceRecord.model_validate_json(line)
            for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def replay_summary(self, run_id: str) -> list[str]:
        """生成人可读的逐事件回放摘要。"""

        record = self.load(run_id)
        if record is None:
            raise KeyError(f"unknown run_id: {run_id}")
        return [
            f"step={event.step} event={event.event} data="
            f"{json.dumps(event.data, ensure_ascii=False, default=str)}"
            for event in record.events
        ]
