"""把 Azusa Adapter 包装成经过 Pydantic 校验的 Kaiji 工具。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from kaiji.adapters.azusa import (
    AzusaAdapter,
    AzusaAdapterError,
    ChunkResponse,
    SearchResponse,
)
from kaiji.tools import ToolDefinition, ToolExecutionError, ToolRegistry


class SearchAzusaArgs(BaseModel):
    """约束 search_azusa 工具的查询、数量和检索模式。"""

    query: str = Field(min_length=2)
    k: int = Field(default=3, ge=1, le=20)
    mode: str = Field(default="hybrid", min_length=1)


class GetChunkArgs(BaseModel):
    """约束 get_chunk_by_id 工具的稳定证据编号。"""

    chunk_id: str = Field(min_length=1)


def build_azusa_registry(adapter: AzusaAdapter) -> ToolRegistry:
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

    # TODO 7d：定义两个 handler、构造精确的 ToolDefinition 并完成注册。
    # 工具名称、说明、参数/结果模型和 max_retries 都属于可执行契约，不可省略。
    raise NotImplementedError("TODO 7d：注册两个 Azusa 工具及其重试边界")
