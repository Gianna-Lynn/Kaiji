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
    query: str = Field(min_length=2)
    k: int = Field(default=3, ge=1, le=20)
    mode: str = Field(default="hybrid")


class GetChunkArgs(BaseModel):
    chunk_id: str = Field(min_length=1)


def build_azusa_registry(adapter: AzusaAdapter) -> ToolRegistry:
    # TODO 7d: Register search_azusa and get_chunk_by_id with their Pydantic
    # argument/result models. Translate AzusaAdapterError into
    # ToolExecutionError without losing retryable, and allow one retry.
    registry = ToolRegistry()
    del adapter
    return registry
