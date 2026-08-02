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

        # TODO 10a：校验一条完整 Trace 的终止结构。
        #
        # 学习目标：区分“事件时间线中的事实”和“记录顶部的检索摘要”，并用
        # 模型不变量阻止两份信息互相矛盾。输入就是已经通过字段校验的 self，
        # 成功时必须原样返回 self，违反以下任一契约时抛出 ValueError：
        # 1. events 中必须恰好出现一个 event == "stop" 的 TraceEvent；
        # 2. 该 stop 必须是 events 的最后一项；
        # 3. stop.data 是结构化事件数据，不是 Message.content。取其中的
        #    reason，并通过 StopReason(...) 校验它是合法枚举值；
        # 4. 事件里的 reason 必须等于顶部 stop_reason 摘要。
        #
        # 非目标：这里不判断回答质量、工具结果是否真的正确，也不推测缺失的
        # stop。当前占位实现只维持合法样例可构造，结构异常测试应当失败。
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

        # TODO 10b：先实现严格的 JSONL 读取边界。
        #
        # 1. 文件不存在时返回 []，空白物理行不代表 TraceRecord；
        # 2. 每个非空行分别交给 TraceRecord.model_validate_json()。这里输入是
        #    JSON 字符串，输出才是 TraceRecord，不能只 json.loads 成裸 dict；
        # 3. 捕获 Pydantic ValidationError，并改抛含物理行号的 ValueError，
        #    同时用异常链保留原始原因；
        # 4. 按文件顺序返回记录，并再次拒绝文件中已有的重复 run_id。即使文件
        #    由外部程序写入，也不能让唯一性契约失效。
        raise NotImplementedError("TODO 10b：实现严格 JSONL 迭代读取")

    def load(self, run_id: str) -> TraceRecord | None:
        """按唯一运行标识查找轨迹；文件或标识不存在时返回空值。"""

        # TODO 10c：复用 iter_records() 按文件顺序查找 run_id。
        #
        # run_id 是记录标识，不是行号或 JSON 路径。找到时返回 TraceRecord；
        # 未找到（包括存储文件不存在）返回 None。不要在这里另写一套宽松解析，
        # 否则 load 与 iter_records 会对非法行、重复标识产生不同结论。
        raise NotImplementedError("TODO 10c：实现按 run_id 加载")

    def append(self, record: TraceRecord) -> None:
        """把唯一运行轨迹追加为一个物理行；重复标识会被拒绝。"""

        # TODO 10d：在 10b/10c 之后实现安全的唯一追加。
        #
        # - record 是 Python/Pydantic 对象；落盘边界需要把它序列化成 JSON
        #   字符串。优先使用 model_dump_json()，不要把 str(record) 当 JSON；
        # - 先通过 load(record.run_id) 拒绝重复标识，重复时抛出 ValueError；
        # - 创建缺失的父目录，以 UTF-8 和追加模式写入；
        # - 一条 record 必须只占一个物理行。字段中的换行应由 JSON 转义；
        # - 若已有非空文件最后一个字节不是 CR/LF，先补物理行分隔符，再写新 JSON；
        # - 写完追加一个真正的换行符，不得覆盖已有轨迹。
        raise NotImplementedError("TODO 10d：实现唯一且分行安全的 JSONL 追加")

    def replay_summary(self, run_id: str) -> list[str]:
        """按事件顺序生成具有规范化 ``data`` 文本的逐事件摘要。"""

        # TODO 10e：生成确定性的 Trace 回放文本。
        #
        # - 通过 load(run_id) 取得记录；未知标识抛出 KeyError；
        # - 每个 TraceEvent 生成一行：
        #   "step=<step> event=<event> data=<规范 JSON>"；
        # - event.data 是供审计的结构化字典，不是 Message.content，也不是已经
        #   序列化好的 JSON 字符串。使用 json.dumps() 统一转为 JSON，并固定
        #   ensure_ascii=False、sort_keys=True、separators=(",", ":")、
        #   allow_nan=False，使相同事件在不同运行中得到相同摘要；
        # - 保持 events 原顺序，不按 step 或 event 名重新排序。
        #
        # 当前占位不生成摘要，回放相关测试应当失败。
        raise NotImplementedError("TODO 10e：实现稳定 Trace 回放")
