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
    """注册两个 Azusa 语义工具，并保留适配器错误的重试属性。"""

    if type(tool_timeout_seconds) is not float and type(tool_timeout_seconds) is not int:
        raise TypeError("tool_timeout_seconds 必须是真正的有限正数")
    if isinstance(tool_timeout_seconds, bool) or not math.isfinite(tool_timeout_seconds) or tool_timeout_seconds <= 0:
        raise ValueError("tool_timeout_seconds 必须是真正的有限正数")
    registry = ToolRegistry()

    def execution_error(exc: AzusaAdapterError) -> ToolExecutionError:
        """在 Registry 边界再次守住严格 bool，不把非法值交给真值判断。"""
        if type(exc.retryable) is not bool:
            return ToolExecutionError("Adapter 返回了非法 retryable", retryable=False)
        return ToolExecutionError(str(exc), retryable=exc.retryable)

    def search(args: BaseModel) -> dict:
        """校验搜索参数并调用 AzusaAdapter。"""

        parsed = SearchAzusaArgs.model_validate(args)
        try:
            return adapter.search_azusa(
                parsed.query, parsed.k, parsed.mode
            ).model_dump(mode="json")
        except AzusaAdapterError as exc:
            raise execution_error(exc) from exc

    def get_chunk(args: BaseModel) -> dict:
        """校验证据编号并调用 AzusaAdapter。"""

        parsed = GetChunkArgs.model_validate(args)
        try:
            return adapter.get_chunk_by_id(parsed.chunk_id).model_dump(mode="json")
        except AzusaAdapterError as exc:
            raise execution_error(exc) from exc

    registry.register(
        ToolDefinition(
            name="search_azusa",
            description="按查询、数量和检索模式搜索 Azusa 证据。",
            arguments_model=SearchAzusaArgs,
            result_model=SearchResponse,
            handler=search,
            timeout_seconds=tool_timeout_seconds,
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
            timeout_seconds=tool_timeout_seconds,
            max_retries=1,
        )
    )
    return registry
