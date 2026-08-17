"""提供轻量 JSON 检查点与长期事实存储的教学脚手架。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


_MISSING = object()


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """读取 JSON 对象根；文件不存在时返回空字典，损坏外壳则显式失败。
    label 仅用于给错误信息标明“哪一类 JSON 文件损坏”.

    这是 TODO 8/9 共用的已给辅助函数。它只校验文件最外层必须是对象；线程、
    用户和记录内部的结构仍由各 Store 的 ``_read_all`` 负责。
    """

    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            f"{label} 文件最外层应是 JSON 对象(形如 {{...}}),"
            f"实际是 {type(payload).__name__}"
        )
    return payload


def _write_json_atomic(path: Path, payload: Any) -> None:
    """先写同目录临时文件再原子替换目标；此辅助结构不属于练习。"""

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

    @field_validator("thread_id", "checkpoint_id")  # 把规则挂在模型上，任何一条构造路径（save() 里新建、load() 里恢复、model_validate 反序列化）都会自动走同一条校验，不用每个方法重复写。
    @classmethod
    def _reject_blank_identifiers(cls, value: str) -> str:
        """身份字段不允许空字符串或者纯空白"""
        if not value.strip():
            raise ValueError("thread_id / checkpoint_id 不允许空字符串或者纯空白")
        return value    # 验证完原样 return value，不改写输入

class JsonCheckpointStore:
    """保存完整执行快照. 不承担长期语义记忆职责.

    JSON 第一层按 ``thread_id`` 隔离，第二层按 ``checkpoint_id`` 保存
    ``state``。``latest`` 的“最新”指最后一次成功调用 ``save`` 的检查点，
    不是 ID 字典序，也不是第一次插入的顺序；覆盖旧 ID 后，该 ID 必须移到
    线程末尾。Store 只校验 ``state`` 是 JSON 对象，具体 AgentState 的业务
    Schema 仍由调用方校验。多进程写锁、冲突合并和临时文件清扫不在本课范围内。

    JSON文件示例:
    {
        "thread-alice": {       # thread_id
            "step-1": {         # checkpoint_id
                                # checkpoint_id这个键对应的值是一个字典, 这个字典是state
                "messages": ["用户问题", "工具调用结果"],
                "step_count": 2,
                "status": "waiting_for_approval",
                
            },
            "step-2": {         # checkpoint_id
                ...
            },
            "waiting-approval": {   # checkpoint_id
                ...
            },
        },

        "thread-bob": {         # thread_id
            ...
        }
    }

    """

    def __init__(self, path: Path) -> None:
        """指定检查点 JSON 文件的位置。"""

        self._path = path

    def _read_all(self) -> dict[str, dict[str, dict[str, Any]]]:
        """读取全部检查点，并校验线程外壳与每个 ``state`` 都是对象。
        对象: JSON对象. 在Python中是dict.

        TODO 8a 应复用 ``_read_json_object``。每个线程值必须是对象，每个
        checkpoint 的 ``state`` 也必须是对象；非法 JSON、列表、``null`` 或
        其他损坏结构必须报错，不能被当成空 Store 或未命中。后续保存与恢复
        都依赖这一个读取边界，因此必须先完成它，不能在各方法中另写宽松解析。
        """
        ckpt_dict = _read_json_object(path=self._path, label="我不知道") # 只校验文件最外层必须是对象
        for thread_id, thread_info in ckpt_dict.items(): # .items()方法可以同时获得键和值
            # 这里的thread_info应当是一个字典, 形如:
            # {
            # "step-1": {
            #      "messages": ["用户问题", "工具调用结果"],
            #      "step_count": 2,
            #      "status": "waiting_for_approval",
            # },
            # "step-2": {
            #     ...
            # },
            # "waiting-approval": {
            #     ...
            # },
            # }

            # 校验1: thread_id键不能是空白
            if not thread_id.strip():   # if "是空白"
                raise ValueError("thread_id should not be blank.")

            # 校验2: thread_info值应当是一个dict
            if not isinstance(thread_info, dict):
                raise ValueError("A thread_info in a thread should be dict.")
            for state_id, state_info in thread_info.items():
            # 这里的一个state_info形如:
            # {
            #     "messages": ["用户问题", "工具调用结果"],
            #     "step_count": 2,
            #     "status": "waiting_for_approval",
            # }
                # 校验3: state_id不能是空白.
                if not state_id.strip():
                    raise ValueError("state_id should not be blank.")
                # 校验4: state_info值应当是一个dict
                if not isinstance(state_info, dict):
                    raise ValueError("A state_info in a state should be dict.")
                # 校验5: state_info可以严格JSON序列化.
                json.dumps(state_info, allow_nan=False) # json.dumps 遇到 object() 这种不可序列化的东西，抛 TypeError
        return ckpt_dict

    def save(self, checkpoint: Checkpoint) -> None:
        """保存或覆盖快照，并把它标记为该线程最后一次成功保存。

        TODO 8b 必须先读取并校验旧数据，再按 ``thread_id/checkpoint_id`` 保存
        ``state``。普通字典覆盖旧键不会改变顺序，因此覆盖时还要兑现上面的
        ``latest`` 语义。最后使用已给的 ``_write_json_atomic``；序列化或 replace
        失败时旧目标文件以及旧 ``latest`` 都必须保持不变。
        """
        # checkpoint 的数据结构:
        # thread_id: str
        # checkpoint_id: str
        # state: dict[str, Any]

        ckpt_dict = self._read_all()

        # region ckpt_dict示例:
        #     {
        #         "thread-alice": {       # thread_id
        #             "step-1": {         # checkpoint_id
        #                                 # checkpoint_id这个键对应的值是一个字典, 这个字典是state
        #                 "messages": ["用户问题", "工具调用结果"],
        #                 "step_count": 2,
        #                 "status": "waiting_for_approval",
                        
        #             },
        #             "step-2": {         # checkpoint_id
        #                 ...
        #             },
        #             "waiting-approval": {   # checkpoint_id
        #                 ...
        #             },
        #         },

        #         "thread-bob": {         # thread_id
        #             ...
        #         }
        #     }
        # endregion

        if checkpoint.thread_id in ckpt_dict:
            # 存在线程
            thread_info = ckpt_dict.get(checkpoint.thread_id)
            if checkpoint.checkpoint_id in thread_info: 
                # 已存在旧快照, 覆盖旧快照, 移动到线程末尾.
                # 移动到整个字典的末尾: 通过"移除+重新添加"的办法.
                thread_info.pop(checkpoint.checkpoint_id)
                thread_info[checkpoint.checkpoint_id] = checkpoint.state
            else:
                # 不存在旧快照, 新建快照.
                thread_info[checkpoint.checkpoint_id] = checkpoint.state
        else:
            # 不存在线程
            ckpt_dict[checkpoint.thread_id] = {
                checkpoint.checkpoint_id : checkpoint.state
            }
        json.dumps(ckpt_dict, allow_nan=False) # json.dumps 遇到 object() 这种不可序列化的东西，抛 TypeError
        _write_json_atomic(self._path, ckpt_dict)
        return

    def load(self, thread_id: str, checkpoint_id: str) -> Checkpoint | None:
        """读取指定检查点，找不到时返回 ``None``。

        TODO 8c 必须区分“键不存在”和“键存在但值损坏”：只有前者返回
        ``None``；后者必须显式报错。存在的数据要重新构造 ``Checkpoint``，
        不能未经模型校验直接返回原始字典。
        """

        if not thread_id.strip():
            raise ValueError("thread_id should not be blank.")
        if not checkpoint_id.strip():
            raise ValueError("checkpoint_id should not be blank.")
        
        ckpt_dict = self._read_all()
        if thread_id in ckpt_dict:
            # 线程键thread_id存在
            thread_info = ckpt_dict.get(thread_id)
            if checkpoint_id in thread_info:
                # checkpoint键存在
                state = thread_info[checkpoint_id]
                # 校验值是不是损坏. 下面这一句会自动进行pydantic校验, 不需要自己再显式校验.
                return Checkpoint(thread_id=thread_id, checkpoint_id=checkpoint_id,state=state) 
            else:
                # checkpoint键不存在
                return None
        else:
            # 线程键thread_id不存在
            return None

    def latest(self, thread_id: str) -> Checkpoint | None:
        """读取指定线程最后一次成功保存的检查点。

        TODO 8d 对不存在或没有检查点的线程返回 ``None``；否则按保存顺序取
        最后一个 ID，并重新构造 ``Checkpoint``。不得按 ID 排序猜测最新值。
        """

        if not thread_id.strip():
            raise ValueError("thread_id should not be blank.")

        ckpt_dict = self._read_all()
        if thread_id in ckpt_dict:
            # 线程键thread_id存在
            thread_info = ckpt_dict.get(thread_id)
            if not thread_info:
                # thread_info这个字典是空的.
                return None

            state_id = next(reversed(thread_info))
            state_info = thread_info[state_id]

            # if state_info is None:    # _read_all() 已经保证 checkpoint 对应的 state 必须是字典。如果它是 None，应该在读取边界就报错；
            #     return None
            return Checkpoint(
                thread_id=thread_id,
                checkpoint_id=state_id,
                state=state_info,
            )

        else:
            # 线程键thread_id不存在
            return None


class MemoryEntry(BaseModel):
    """表示由应用策略筛选后允许长期保存的一条事实。"""

    key: str
    value: str
    tags: list[str] = Field(default_factory=list)


class JsonMemoryStore:
    """保存应用策略选中的少量持久事实，而不是完整运行状态。

    JSON 第一层按 ``user_id`` 隔离，第二层按 ``entry.key`` 保存完整
    ``MemoryEntry``。相同用户和键覆盖旧值，但维持该键第一次插入的位置，因此
    ``list`` 按首次插入顺序返回。是否值得长期保存由调用方决定；Store 不分析
    Message，也不自动从 AgentState 提取事实。多进程并发写入不在本课范围内。
    """

    def __init__(self, path: Path) -> None:
        """指定长期事实 JSON 文件的位置。"""

        self._path = path

    def _read_all(self) -> dict[str, dict[str, Any]]:
        """读取全部事实，并校验用户外壳、记录 Schema 与内外键一致性。

        TODO 9a 应复用 ``_read_json_object``。每个用户对应的值必须是对象；还要
        逐条构造 ``MemoryEntry`` 并确认外层 key 等于 ``entry.key``。非法 JSON、
        列表、``null``、字段错误或内外键冲突都必须报错，不能被当成空 Store，
        也不能让后续 ``put`` 在损坏数据上继续写入。后续方法统一复用这个边界。
        """

        raise NotImplementedError("TODO 9a：实现长期事实存储结构校验")

    def put(self, user_id: str, entry: MemoryEntry) -> None:
        """按用户和键保存或覆盖一条长期事实。

        TODO 9b 必须先读取并校验旧数据，再把 ``entry.model_dump()`` 保存到
        ``user_id/entry.key``，最后复用原子写入辅助函数。覆盖不得改变该键的
        首次插入位置，不同用户即使键相同也不能串写。
        """

        raise NotImplementedError("TODO 9b：实现长期事实保存")

    def get(self, user_id: str, key: str) -> MemoryEntry | None:
        """读取并校验一条长期事实，找不到时返回 ``None``。

        TODO 9c 只有在外层键真正不存在时返回 ``None``；已存在但为 ``null``、
        空对象或字段非法的记录必须让 ``MemoryEntry`` 校验报错。存储用的外层
        ``key`` 还必须等于记录内部 ``entry.key``，否则显式报错。
        """

        raise NotImplementedError("TODO 9c：实现单条长期事实恢复")

    def list(self, user_id: str) -> list[MemoryEntry]:
        """按首次插入顺序返回指定用户的全部已校验事实。

        TODO 9d 对不存在的用户返回空列表；对每条已存在记录都重新执行
        ``MemoryEntry`` 校验和内外 ``key`` 一致性检查。``get`` 与 ``list``
        对同一条损坏记录必须一致报错，不能由其中一条路径掩盖损坏。
        """

        raise NotImplementedError("TODO 9d：实现长期事实列表恢复")
