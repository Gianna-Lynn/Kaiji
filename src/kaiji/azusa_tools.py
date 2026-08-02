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
    """注册两个 Azusa 语义工具，并保留适配器错误的重试属性。"""

    registry = ToolRegistry()

    def search(args: BaseModel) -> dict:
        """校验搜索参数并调用 AzusaAdapter。"""

        parsed = SearchAzusaArgs.model_validate(args)
        try:
            return adapter.search_azusa(
                parsed.query, parsed.k, parsed.mode
            ).model_dump(mode="json")
        except AzusaAdapterError as exc:
            raise ToolExecutionError(str(exc), retryable=exc.retryable) from exc

    def get_chunk(args: BaseModel) -> dict:
        """校验证据编号并调用 AzusaAdapter。"""

        parsed = GetChunkArgs.model_validate(args)
        try:
            return adapter.get_chunk_by_id(parsed.chunk_id).model_dump(mode="json")
        except AzusaAdapterError as exc:
            raise ToolExecutionError(str(exc), retryable=exc.retryable) from exc

    registry.register(
        ToolDefinition(
            name="search_azusa",
            description="按查询、数量和检索模式搜索 Azusa 证据。",
            arguments_model=SearchAzusaArgs,
            result_model=SearchResponse,
            handler=search,
            max_retries=1,
        )
    )
    registry.register(
        ToolDefinition(
            name="get_chunk_by_id",
            description="按稳定 chunk_id 读取一条完整证据。",
            arguments_model=GetChunkArgs,
            result_model=ChunkResponse,
            handler=get_chunk,
            max_retries=1,
        )
    )
    return registry
