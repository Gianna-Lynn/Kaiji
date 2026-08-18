"""把 Agent 运行轨迹写入 JSONL，并提供读取和回放摘要能力。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator, ValidationError, ConfigDict

from kaiji.models import StopReason, TraceEvent
import json


class TraceRecord(BaseModel):
    """汇总一次完整运行的时间线、停止摘要和附加元数据。

    ``events`` 是按发生顺序保存的审计时间线，因此必须以唯一的 ``stop``
    事件结束。``stop_reason`` 是便于索引和聚合的快速摘要，必须与末尾 stop
    事件 ``data.reason`` 表示同一个 :class:`StopReason`。本模型只检查轨迹
    结构是否自洽，不判断最终回答或其他业务结果是否正确。
    """
    model_config = ConfigDict(extra="forbid")
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
        # stop。
        stop_count = 0
        last_stop = 0
        current_step = 0
        for i in range(0, len(self.events)):
            if self.events[i].event == "stop":
                stop_count += 1
                last_stop = i
            if self.events[i].step >= current_step:
                current_step = self.events[i].step
            else:
                raise ValueError(f"events 的 step 需要保持非递减") # step 表示“逻辑步骤”，不是事件在列表中的序号。同一个逻辑步骤里可能产生多个事件. 需要保持非递减.
        if stop_count != 1:
            raise ValueError(f"要求恰好包含一个 stop 事件: 实际存在{stop_count}个stop事件.")
        if last_stop != len(self.events) - 1:
            raise ValueError(
                f"stop 事件必须是 events 的最后一项: 当前下标为{last_stop}, "
                f"要求下标为{len(self.events) - 1}."
            )
        stop_event: TraceEvent = self.events[last_stop]

        # data["reason"] 有两种形态：内存中直接构造时是 StopReason 枚举，
        # 经 JSON 落盘再读回后是其字符串值。StopReason(...) 对两者兼容——
        # 枚举原样返回，字符串查表转换；非法值抛 ValueError，下面统一转成校验错误。
        stop_event_reason = stop_event.data.get("reason")
        try:
            trace_reason = StopReason(stop_event_reason)
        except(TypeError, ValueError) as exc:
            raise ValueError("data.reason 必须是合法 StopReason") from exc
        if trace_reason != self.stop_reason:
            raise ValueError("事件里的 reason 必须与 TraceRecord.stop_reason 一致")

        try:
            json.dumps(self.metadata, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("不满足有限JSON, 存在nan/infinity/object")

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
        
        if not self._path.exists():
            return []
        trace_record_list: list[TraceRecord]= []
        with self._path.open("r", encoding="utf-8") as file:
            seen_run_ids: set[str] = set()
            for line_number, line in enumerate(file, start=1):  # line 是字符串，并且通常包含结尾换行符
                if not line.strip():    # 跳过空白行
                    continue
                try:
                    record = TraceRecord.model_validate_json(line) # 解析这一行 JSON；把解析结果校验成 TraceRecord 对象. json.loads(line)只能把JSON字符串
                except ValidationError as exc:
                    raise ValueError(f"第 {line_number} 行不是合法 TraceRecord") from exc
                if record.run_id in seen_run_ids:
                    raise ValueError(f"第 {line_number} 行包含重复 run_id: {record.run_id}")
                else:
                    seen_run_ids.add(record.run_id)
                trace_record_list.append(record)
        return trace_record_list

    def load(self, run_id: str) -> TraceRecord | None:
        """按唯一运行标识查找轨迹；文件或标识不存在时返回空值。"""

        # TODO 10c：复用 iter_records() 按文件顺序查找 run_id。
        #
        # run_id 是记录标识，不是行号或 JSON 路径。找到时返回 TraceRecord；
        # 未找到（包括存储文件不存在）返回 None。不要在这里另写一套宽松解析，
        # 否则 load 与 iter_records 会对非法行、重复标识产生不同结论。

        trace_record_list = self.iter_records()
        for trace_record in trace_record_list:
            if trace_record.run_id == run_id:
                return trace_record
        return None

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
        if self.load(record.run_id) is not None:
            raise ValueError("run_id 已存在")
        record_json_str = record.model_dump_json() # 返回json字符串, 类型是str
        self._path.parent.mkdir(parents=True, exist_ok=True)    # parents=True: 父目录不存在时, 连上级目录一起创建. exist_ok=True: 目录已经存在时不报错. 
        require_lf = False
        if self._path.exists() and self._path.stat().st_size > 0:  # 假如已有文件, 而且非空, 那么检查末尾是否存在换行. 
            with self._path.open("rb") as file:     # rb表示按照字节读取: read byte
                file.seek(-1, 2)    # 以文件末尾（2 = os.SEEK_END）为基准，向后倒退 1 个字节（-1）。
                last_byte = file.read(1)    # 读取最后一个字节. last_byte的类型是bytes
                if last_byte not in (b'\n', b'\r'):   # b'\n'前面的b可以把字符串'\n'转成字节, 完成字节和字节的比较. 
                    require_lf = True # 追加前需要补换行
        with self._path.open("a", encoding="utf-8") as file:    # "a"是append, 追加模式: 文件不存在时创建; 存在时不覆盖原内容; 写入内容放在文件末尾. 不能使用"w", "w"会清空源文件.
            if require_lf:
                file.write("\n") 
            file.write(record_json_str)
            file.write("\n")

        
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

        
        trace_record = self.load(run_id)
        if trace_record is None:
            raise KeyError("run_id是未知标识, unknown run_id")
        replay_summary_list : list[str] = []
        # trace_record内部形如:
        # run_id: str = Field(min_length=1)
        # events: list[TraceEvent] = Field(min_length=1)
        # stop_reason: StopReason
        # metadata: dict[str, Any] = Field(default_factory=dict)
        for event in trace_record.events:
            # event 内部形如:
            # model_config = ConfigDict(extra="forbid")
            # step: StrictInt = Field(ge=1)
            # event: Literal["model_action", "model_failure", "tool_result", "stop"]
            # data: dict[str, Any]
            event_data_json_str = json.dumps(
                event.data,         
                ensure_ascii=False,     # 保留中文
                sort_keys=True,         # 字典键按照字母顺序排序, 保证稳定的输出
                separators=(",",":"),   # 去除JSON中多余的空格. 令 {"a": 2, "b": 3} 变成 {"a":2,"b":3}, 去除元素之间和冒号后面的空格.
                allow_nan=False,        # 禁止 float("nan"), float("inf"), float("-inf"). 这些不是标准JSON值.
            )
            # 拼装: "step=<step> event=<event> data=<规范 JSON>"；
            replay_summary_list.append( f"step={event.step} event={event.event} data={event_data_json_str}" )
        return replay_summary_list
