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
    registry = ToolRegistry()

    def search(args: BaseModel) -> dict:
        parsed = SearchAzusaArgs.model_validate(args)
        try:
            return adapter.search_azusa(parsed.query, parsed.k, parsed.mode).model_dump()
        except AzusaAdapterError as exc:
            raise ToolExecutionError(str(exc), retryable=exc.retryable) from exc

    def get_chunk(args: BaseModel) -> dict:
        parsed = GetChunkArgs.model_validate(args)
        try:
            return adapter.get_chunk_by_id(parsed.chunk_id).model_dump()
        except AzusaAdapterError as exc:
            raise ToolExecutionError(str(exc), retryable=exc.retryable) from exc

    registry.register(
        ToolDefinition(
            name="search_azusa",
            description="Search Azusa evidence by query, k, and retrieval mode.",
            arguments_model=SearchAzusaArgs,
            result_model=SearchResponse,
            handler=search,
            max_retries=1,
        )
    )
    registry.register(
        ToolDefinition(
            name="get_chunk_by_id",
            description="Fetch one complete evidence chunk by stable chunk id.",
            arguments_model=GetChunkArgs,
            result_model=ChunkResponse,
            handler=get_chunk,
            max_retries=1,
        )
    )
    return registry
