"""把 Agent 运行轨迹写入 JSONL，并提供读取和回放摘要能力。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from kaiji.models import StopReason, TraceEvent


class TraceRecord(BaseModel):
    """汇总一次完整运行的时间线、停止摘要和附加元数据。

    ``events`` 是按发生顺序保存的审计时间线，因此必须以唯一的 ``stop``
    事件结束。``stop_reason`` 是便于索引和聚合的快速摘要，必须与末尾 stop
    事件 ``data.reason`` 表示同一个 :class:`StopReason`。本模型只检查轨迹
    结构是否自洽，不判断最终回答或其他业务结果是否正确。
    """

    run_id: str = Field(min_length=1)
    events: list[TraceEvent] = Field(min_length=1)
    stop_reason: StopReason
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def timeline_has_one_consistent_terminal_stop(self) -> TraceRecord:
        """要求唯一 stop 位于末尾，且停止原因与快速摘要一致。"""

        stop_events = [event for event in self.events if event.event == "stop"]
        if len(stop_events) != 1:
            raise ValueError("完整轨迹必须恰好包含一个 stop 事件")
        if self.events[-1].event != "stop":
            raise ValueError("stop 事件必须是完整轨迹的最后一项")

        raw_reason = stop_events[0].data.get("reason")
        try:
            event_reason = StopReason(raw_reason)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "stop 事件的 data.reason 必须是合法 StopReason"
            ) from exc
        if event_reason != self.stop_reason:
            raise ValueError("stop 事件原因必须与 TraceRecord.stop_reason 一致")
        return self


class JsonlTraceStore:
    """使用逐行 JSON 格式持久化和查询不可变的运行轨迹。

    每个非空物理行必须恰好包含一条合法 ``TraceRecord``，文件顺序就是
    追加顺序。``run_id`` 在同一个 Store 中必须唯一，重复追加会显式失败，
    从而避免 ``load()`` 究竟读取第一条还是最后一条的歧义。
    """

    def __init__(self, path: Path) -> None:
        """指定轨迹 JSONL 文件的位置。"""

        self._path = path

    def iter_records(self) -> list[TraceRecord]:
        """按追加顺序读取轨迹，忽略空行并拒绝非法行或重复标识。"""

        if not self._path.exists():
            return []

        records: list[TraceRecord] = []
        seen_run_ids: set[str] = set()
        for line_number, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                record = TraceRecord.model_validate_json(line)
            except ValidationError as exc:
                raise ValueError(
                    f"轨迹文件第 {line_number} 行不是合法 TraceRecord"
                ) from exc
            if record.run_id in seen_run_ids:
                raise ValueError(
                    f"轨迹文件第 {line_number} 行包含重复 run_id: {record.run_id}"
                )
            seen_run_ids.add(record.run_id)
            records.append(record)
        return records

    def load(self, run_id: str) -> TraceRecord | None:
        """按唯一运行标识查找轨迹；文件或标识不存在时返回空值。"""

        for record in self.iter_records():
            if record.run_id == run_id:
                return record
        return None

    def append(self, record: TraceRecord) -> None:
        """把唯一运行轨迹追加为一个物理行；重复标识会被拒绝。"""

        if self.load(record.run_id) is not None:
            raise ValueError(f"run_id 已存在，不能重复追加: {record.run_id}")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        needs_separator = False
        if self._path.exists() and self._path.stat().st_size > 0:
            with self._path.open("rb") as existing:
                existing.seek(-1, 2)
                needs_separator = existing.read(1) not in {b"\n", b"\r"}
        with self._path.open("a", encoding="utf-8") as stream:
            if needs_separator:
                # 外部写入的合法 JSONL 可能缺少末尾换行；先补分隔符再追加。
                stream.write("\n")
            stream.write(record.model_dump_json())
            stream.write("\n")

    def replay_summary(self, run_id: str) -> list[str]:
        """按事件顺序生成具有规范化 ``data`` 文本的逐事件摘要。"""

        record = self.load(run_id)
        if record is None:
            raise KeyError(f"unknown run_id: {run_id}")
        summaries = []
        for event in record.events:
            data_text = json.dumps(
                event.data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            summaries.append(
                f"step={event.step} event={event.event} data={data_text}"
            )
        return summaries
