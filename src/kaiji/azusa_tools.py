"""把 Azusa Adapter 包装成经过 Pydantic 校验的 Kaiji 工具。"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from kaiji.adapters.azusa import (
    AzusaAdapter,
    AzusaAdapterError,
    ChunkResponse,
    SearchResponse,
)
from kaiji.tools import ToolDefinition, ToolExecutionError, ToolRegistry


class SearchAzusaArgs(BaseModel):
    """约束 search_azusa 工具的查询、数量和检索模式。"""

    model_config = ConfigDict(extra="forbid")
    query: StrictStr = Field(min_length=2)
    k: StrictInt = Field(default=3, ge=1, le=20)
    mode: StrictStr = Field(default="hybrid", min_length=1)

    @field_validator("query", "mode")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("参数不得为空或纯空白")
        return value


class GetChunkArgs(BaseModel):
    """约束 get_chunk_by_id 工具的稳定证据编号。"""

    model_config = ConfigDict(extra="forbid")
    chunk_id: StrictStr = Field(min_length=1)

    @field_validator("chunk_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("chunk_id 不得为空或纯空白")
        return value


def build_azusa_registry(adapter: AzusaAdapter, *, tool_timeout_seconds: float = 4.0) -> ToolRegistry:
    """把两个稳定 Azusa 语义调用注册成经过校验的 Kaiji 工具。

    TODO 7d 必须注册且只注册 ``search_azusa`` 与 ``get_chunk_by_id``：参数模型
    分别为 ``SearchAzusaArgs``、``GetChunkArgs``，结果模型分别为
    ``SearchResponse``、``ChunkResponse``，两者的 ``max_retries`` 都是 1。

    handler 接收的是 Executor 已用参数模型校验过的 ``BaseModel``，仍应通过对应
    参数模型恢复明确类型，并把默认值和显式值原样、恰好一次地传给 Adapter。
    Adapter 返回 Pydantic 对象，handler 返回 JSON 兼容字典；若 Adapter 抛出
    ``AzusaAdapterError``，必须转成 ``ToolExecutionError`` 并原样传播
    ``retryable``。参数校验失败发生在 handler 之前，此时 Adapter 不应被调用，
    ``attempts`` 也应保持为 0；真正的有限重试只由 ``ToolExecutor`` 执行。
    """

    if type(tool_timeout_seconds) is not float and type(tool_timeout_seconds) is not int:
        raise TypeError("tool_timeout_seconds 必须是真正的有限正数")
    if isinstance(tool_timeout_seconds, bool) or not math.isfinite(tool_timeout_seconds) or tool_timeout_seconds <= 0:
        raise ValueError("tool_timeout_seconds 必须是真正的有限正数")

    # TODO 7d：定义两个 handler、构造精确的 ToolDefinition 并完成注册。
    # 工具名称、说明、参数/结果模型和 max_retries 都属于可执行契约，不可省略。
    raise NotImplementedError("TODO 7d：注册两个 Azusa 工具及其重试边界")
