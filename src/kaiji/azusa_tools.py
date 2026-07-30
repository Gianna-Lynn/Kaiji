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
    mode: str = Field(default="hybrid")


class GetChunkArgs(BaseModel):
    """约束 get_chunk_by_id 工具的稳定证据编号。"""

    chunk_id: str = Field(min_length=1)


def build_azusa_registry(adapter: AzusaAdapter) -> ToolRegistry:
    """把两个 Azusa 语义调用注册成 Kaiji 工具；练习版需要补全映射。"""
    # TODO 7d：使用各自的 Pydantic 参数/结果模型注册 search_azusa 和
    # get_chunk_by_id。把 AzusaAdapterError 转成 ToolExecutionError 时不得
    # 丢失 retryable，并允许最多一次重试。
    registry = ToolRegistry()
    del adapter
    return registry
