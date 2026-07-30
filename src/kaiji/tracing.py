"""保存、读取并以人类可读形式重放 Kaiji 的 JSONL Trace。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from kaiji.models import StopReason, TraceEvent


class TraceRecord(BaseModel):
    """保存一次运行的全部事件、停止原因和附加元数据。"""

    run_id: str
    events: list[TraceEvent]
    stop_reason: StopReason
    metadata: dict[str, Any] = Field(default_factory=dict)


class JsonlTraceStore:
    """以每行一条 JSON 的格式持久化和重放 Trace。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, record: TraceRecord) -> None:
        """追加一条 TraceRecord；练习版需要补全 JSONL 写入。"""
        # TODO 10a：创建父目录，并保证每行只追加一个 JSON 对象。
        del record

    def load(self, run_id: str) -> TraceRecord | None:
        """按 run_id 查找记录，且不改变文件顺序。"""
        # TODO 10b：按 run_id 查找记录，不改变文件顺序。
        del run_id
        return None

    def iter_records(self) -> list[TraceRecord]:
        """按文件顺序返回所有经过模型校验的非空记录。"""
        # TODO 10c：文件不存在视为空存储；校验每一条非空行。
        return []

    def replay_summary(self, run_id: str) -> list[str]:
        """把指定运行转换为稳定、便于人阅读的事件摘要。"""
        # TODO 10d：未知 run 抛 KeyError；已知 run 生成稳定且便于阅读的
        # step/event/data 文本行。
        del run_id
        return []
