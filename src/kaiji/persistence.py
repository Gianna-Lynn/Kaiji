"""提供轻量 JSON 检查点与长期事实存储的教学脚手架。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, ConfigDict


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
    model_config = ConfigDict(extra="forbid") # 禁止额外字段, 形如unexpected="bad",

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

    JSON 文件结构形如：
    checkpoints_by_thread
            └── thread_id
                └── checkpoint_id
                    └── state
    
    一个JSON文件的例子:
    {
        "thread-alice": {                    # thread_id
            "checkpoint-before-search": {    # checkpoint_id
                "messages": ["用户问题", "工具调用结果"],
                "step_count": 2,
                "status": "waiting_for_approval"
            },
            "checkpoint-after-approval": {   # checkpoint_id
                ...
            },
            "checkpoint-final": {             # checkpoint_id
                ...
            }
        },

        "thread-bob": {                      # thread_id
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
        checkpoints_by_thread = _read_json_object(
            path=self._path,
            label="检查点",
        )
        for thread_id, thread_checkpoints in checkpoints_by_thread.items():
            # thread_checkpoints 是该线程按 checkpoint_id 建立的字典，形如：
            # {
            # "checkpoint-before-search": {
            #      "messages": ["用户问题", "工具调用结果"],
            #      "step_count": 2,
            #      "status": "waiting_for_approval",
            # },
            # "checkpoint-after-approval": {
            #     ...
            # },
            # "checkpoint-final": {
            #     ...
            # },
            # }

            # 校验 1：thread_id 键不能是空白。
            if not thread_id.strip():
                raise ValueError("thread_id 不得为空白")

            # 校验 2：线程值必须是 checkpoint_id 到 state 的字典。
            if not isinstance(thread_checkpoints, dict):
                raise ValueError("线程对应的检查点集合必须是字典")
            for checkpoint_id, state in thread_checkpoints.items():
            # state 是某个 checkpoint_id 对应的业务状态，形如：
            # {
            #     "messages": ["用户问题", "工具调用结果"],
            #     "step_count": 2,
            #     "status": "waiting_for_approval",
            # }
                # 校验 3：checkpoint_id 键不能是空白。
                if not checkpoint_id.strip():
                    raise ValueError("checkpoint_id 不得为空白")
                # 校验 4：state 必须是字典。
                if not isinstance(state, dict):
                    raise ValueError("检查点对应的 state 必须是字典")
                # 校验 5：state 必须能够以严格 JSON 序列化。
                json.dumps(state, allow_nan=False)
        return checkpoints_by_thread

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

        checkpoints_by_thread = self._read_all()

        # region checkpoints_by_thread 示例：
        #     {
        #         "thread-alice": {                    # thread_id
        #             "checkpoint-before-search": {    # checkpoint_id
        #                 "messages": ["用户问题", "工具调用结果"],
        #                 "step_count": 2,
        #                 "status": "waiting_for_approval",
        #             },
        #             "checkpoint-after-approval": {
        #                 ...
        #             },
        #             "checkpoint-final": {
        #                 ...
        #             },
        #         },

        #         "thread-bob": {         # thread_id
        #             ...
        #         }
        #     }
        # endregion

        if checkpoint.thread_id in checkpoints_by_thread:
            # 线程已存在。
            thread_checkpoints = checkpoints_by_thread.get(checkpoint.thread_id)
            if checkpoint.checkpoint_id in thread_checkpoints:
                # 已存在旧检查点；删除后重新添加，使它移动到线程末尾。
                thread_checkpoints.pop(checkpoint.checkpoint_id)
                thread_checkpoints[checkpoint.checkpoint_id] = checkpoint.state
            else:
                # 该 checkpoint_id 尚不存在，追加新的 state。
                thread_checkpoints[checkpoint.checkpoint_id] = checkpoint.state
        else:
            # 线程尚不存在，创建它的检查点字典。
            checkpoints_by_thread[checkpoint.thread_id] = {
                checkpoint.checkpoint_id: checkpoint.state
            }
        json.dumps(checkpoints_by_thread, allow_nan=False)
        _write_json_atomic(self._path, checkpoints_by_thread)
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
        
        checkpoints_by_thread = self._read_all()
        if thread_id in checkpoints_by_thread:
            # thread_id 存在。
            thread_checkpoints = checkpoints_by_thread.get(thread_id)
            if checkpoint_id in thread_checkpoints:
                # checkpoint_id 存在；Checkpoint 构造会重新校验恢复数据。
                state = thread_checkpoints[checkpoint_id]
                return Checkpoint(
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                    state=state,
                )
            else:
                # checkpoint_id 不存在。
                return None
        else:
            # thread_id 不存在。
            return None

    def latest(self, thread_id: str) -> Checkpoint | None:
        """读取指定线程最后一次成功保存的检查点。

        TODO 8d 对不存在或没有检查点的线程返回 ``None``；否则按保存顺序取
        最后一个 ID，并重新构造 ``Checkpoint``。不得按 ID 排序猜测最新值。
        """

        if not thread_id.strip():
            raise ValueError("thread_id should not be blank.")

        checkpoints_by_thread = self._read_all()
        if thread_id in checkpoints_by_thread:
            # thread_id 存在。
            thread_checkpoints = checkpoints_by_thread.get(thread_id)
            if not thread_checkpoints:
                # 线程存在，但还没有保存任何检查点。
                return None

            checkpoint_id = next(reversed(thread_checkpoints))
            state = thread_checkpoints[checkpoint_id]
            return Checkpoint(
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                state=state,
            )

        else:
            # thread_id 不存在。
            return None


class MemoryEntry(BaseModel):
    """表示由应用策略筛选后允许长期保存的一条事实。"""
    model_config = ConfigDict(extra="forbid") # 禁止额外字段, 形如unexpected="bad"
    key: str
    value: str
    tags: list[str] = Field(default_factory=list)

    @field_validator("key")  # 把规则挂在模型上，任何一条构造路径（save() 里新建、load() 里恢复、model_validate 反序列化）都会自动走同一条校验，不用每个方法重复写。
    @classmethod
    def _reject_blank_identifiers(cls, value: str) -> str:
        """事实键不允许空字符串或者纯空白"""
        if not value.strip():
            raise ValueError("key 不允许空字符串或者纯空白")
        return value    # 验证完原样 return value，不改写输入


class JsonMemoryStore:
    """保存应用策略选中的少量持久事实，而不是完整运行状态。

    JSON 第一层按 ``user_id`` 隔离，第二层按 ``entry.key`` 保存完整
    ``MemoryEntry``。相同用户和键覆盖旧值，但维持该键第一次插入的位置，因此
    ``list`` 按首次插入顺序返回。是否值得长期保存由调用方决定；Store 不分析
    Message，也不自动从 AgentState 提取事实。多进程并发写入不在本课范围内。

    JSON 文件结构形如：
    entries_by_user
        └── user_id
            └── entry.key
                └── MemoryEntry 字段（key、value、tags）

    JSON 文件示例：
    {
        "user-alice": {                 # user_id
            "favorite-color": {         # entry.key，同时也是第二层键
                "key": "favorite-color",
                "value": "蓝色",
                "tags": ["偏好", "颜色"]
            },
            "timezone": {
                "key": "timezone",
                "value": "Asia/Shanghai",
                "tags": ["设置"]
            }
        },
        "user-bob": {
            "favorite-color": {
                "key": "favorite-color",
                "value": "绿色",
                "tags": ["偏好", "颜色"]
            }
        }
    }
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

        entries_by_user = _read_json_object(path=self._path, label="长期事实")
        for user_id, user_entries in entries_by_user.items():
            # 校验 1: user_id 键不能是空白.
            if not user_id.strip():
                raise ValueError("user_id 不得为空白")
            # 校验 2: user_entries 须是字典
            if not isinstance(user_entries, dict):
                raise ValueError("user_entries 必须是字典")
            for entry_key, entry in user_entries.items():
                # entry形如:
                # "timezone": {
                #     "key": "timezone",
                #     "value": "Asia/Shanghai",
                #     "tags": ["设置"]
                # } 
                # 校验 3: entry_key 不能是空白
                if not entry_key.strip():
                    raise ValueError("entry_key 不得为空白")
                # # 校验 4: entry 必须是字典.       # 这个校验交给下面的MemoryEntry.model_validate()完成, 否则会抢先抛出valueerror
                # if not isinstance(entry, dict):
                #     raise ValueError("entry 必须是字典")
                # 校验 5: entry 必须能够以严格 JSON 序列化.
                json.dumps(entry, allow_nan=False)

                # 应先进行 MemoryEntry 模型校验，再比较内外键。
                # 这样字段缺失或类型错误会得到 ValidationError，内外键不一致才得到 ValueError。
                
                # 校验 6 :逐条构造 MemoryEntry
                validated_entry = MemoryEntry.model_validate(entry)
                # 校验 7: 外层 entry_key 必须等于validated_entry.key
                if entry_key != validated_entry.key:
                    raise ValueError("外层 entry_key 必须等于内层entry.key")

        return entries_by_user
    def put(self, user_id: str, entry: MemoryEntry) -> None:
        """按用户和键保存或覆盖一条长期事实。

        TODO 9b 必须先读取并校验旧数据，再把 ``entry.model_dump()`` 保存到
        ``user_id/entry.key``，最后复用原子写入辅助函数。覆盖不得改变该键的
        首次插入位置，不同用户即使键相同也不能串写。
        """

        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id should be a non-blank str.")

        entries_by_user = self._read_all()
        if user_id in entries_by_user:
            # 用户已存在
            entries = entries_by_user.get(user_id)
            # "user-alice": {                 # user_id
            #  entries形如:               
            #     "favorite-color": {         # entry.key，同时也是第二层键
            #         "key": "favorite-color",
            #         "value": "蓝色",
            #         "tags": ["偏好", "颜色"]
            #     },
            #     "timezone": {
            #         "key": "timezone",
            #         "value": "Asia/Shanghai",
            #         "tags": ["设置"]
            #     }
            # },
            entries[entry.key] = entry.model_dump()
            

        else:
            # 用户不存在. 创建用户的记忆字典.
            entries_by_user[user_id] = {
                entry.key : entry.model_dump()
            }
        json.dumps(entries_by_user, allow_nan=False)
        _write_json_atomic(self._path, entries_by_user)
        return

    def get(self, user_id: str, key: str) -> MemoryEntry | None:
        """读取并校验一条长期事实，找不到时返回 ``None``。

        TODO 9c 只有在外层键真正不存在时返回 ``None``；已存在但为 ``null``、
        空对象或字段非法的记录必须让 ``MemoryEntry`` 校验报错。存储用的外层
        ``key`` 还必须等于记录内部 ``entry.key``，否则显式报错。
        """
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id should be a non-blank str.")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("key should be a non-blank str.")

        entries_by_user = self._read_all()
        if user_id in entries_by_user:
            # user_id存在.
            entries = entries_by_user.get(user_id)
            # "user-alice": {                 # user_id
            #  entries形如:               
            #     "favorite-color": {         # entry.key，同时也是第二层键
            #         "key": "favorite-color",
            #         "value": "蓝色",
            #         "tags": ["偏好", "颜色"]
            #     },
            #     "timezone": {
            #         "key": "timezone",
            #         "value": "Asia/Shanghai",
            #         "tags": ["设置"]
            #     }
            # },
            if key in entries:
                # key存在
                entry = entries[key]
                if key != entry["key"]:
                    raise ValueError("外层 key 不等于记录内部 entry.key ")
                return MemoryEntry.model_validate(entry)
            else:
                # key不存在
                return None
        else:
            # user_id不存在.
            return None

    def list(self, user_id: str) -> list[MemoryEntry]:
        """按首次插入顺序返回指定用户的全部已校验事实。

        TODO 9d 对不存在的用户返回空列表；对每条已存在记录都重新执行
        ``MemoryEntry`` 校验和内外 ``key`` 一致性检查。``get`` 与 ``list``
        对同一条损坏记录必须一致报错，不能由其中一条路径掩盖损坏。
        """
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id should be a non-blank str.")
        
        entries_by_user = self._read_all()
        memory_entry_list = []
        if user_id in entries_by_user:
            # user_id存在.
            entries = entries_by_user.get(user_id)
            # "user-alice": {                 # user_id                
            #  entries形如:               
            #     "favorite-color": {         # entry.key，同时也是第二层键
            #         "key": "favorite-color",
            #         "value": "蓝色",
            #         "tags": ["偏好", "颜色"]
            #     },
            #     "timezone": {
            #         "key": "timezone",
            #         "value": "Asia/Shanghai",
            #         "tags": ["设置"]
            #     }
            # },
            for key, entry in entries.items():
                # 应先进行 MemoryEntry 模型校验，再比较内外键。
                # 这样字段缺失或类型错误会得到 ValidationError，内外键不一致才得到 ValueError。
                memory_entry = MemoryEntry.model_validate(entry)
                if key != memory_entry.key:
                    raise ValueError("外层 key 不等于记录内部 entry.key ")
                memory_entry_list.append(MemoryEntry.model_validate(entry))
            return memory_entry_list

        else:
            # user_id不存在.
            return []             