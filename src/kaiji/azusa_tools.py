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
    # LLM传入的参数可能不可靠, 比如把数字传给字符串参数, 或者被注入攻击传入非法参数. 为此, 需要在调用前完成校验.

    model_config = ConfigDict(extra="forbid")

    # 关于下面的query字段:
    # Field 是 Pydantic 用来“描述模型字段规则”的工具。
    # 它把一个普通属性变成带有默认值、校验约束和 Schema 元数据的字段.
    # 在这里, query是字段名, StrictStr是字段类型, Field(...)是字段的额外规则和说明
    # min_length=2 表示字符串至少两个字符.
    # description="自然语言..." 表示给调用方或 LLM 看的语义说明.
    # 最终生成schema, 形如:
    #    {
    #     "type": "string",
    #     "minLength": 2,
    #     "description": "自然语言查询内容"
    #     }
    query: StrictStr = Field(
        min_length=2,
        description="自然语言查询内容",
    )
    k: StrictInt = Field(default=3, ge=1, le=20)
    mode: StrictStr = Field(default="hybrid", min_length=1)

    @field_validator("query", "mode") # Pydantic字段验证器.当用户传入"query"或"mode"这两个参数时,必须通过下面的reject_blank函数进行检查.
    @classmethod   # 把reject_blank声明为一个类方法,传入的第一个参数不再是self, 而是class. 不需要实例化对象, 也可以进行校验.
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
    """构建只包含检索与证据读取工具的 Azusa 注册表。

    TODO 7d: 注册 ``search_azusa`` 与 ``get_chunk_by_id`` 两个工具，
    参数/结果模型分别为 ``SearchAzusaArgs`` / ``SearchResponse`` 和
    ``GetChunkArgs`` / ``ChunkResponse``；
    两者共用 ``tool_timeout_seconds``作为单次执行等待上限，
    ``max_retries`` 均为 1。

    本函数只负责组装注册表，不调用 Adapter，也不发 HTTP 请求。
    handler每次只调用一次对应的 Adapter 方法，把响应转成 JSON 兼容字典；
    Adapter 报错统一转为 ``ToolExecutionError``，并保留 ``retryable``
    标记。参数校验、是否重试和超时控制都由 ToolExecutor 在真正执行
    工具时完成，本函数不参与。

    Args:
        adapter: 提供 search_azusa 和 get_chunk_by_id 方法的适配器。
        tool_timeout_seconds: 单次工具执行尝试的等待上限（秒），默认
            4.0。只接受有限正数 int 或 float，不接受 bool；它不是
            Adapter 的 HTTP 超时设置，也不保证超时能中断已开始的 handler。

    Returns:
        仅注册上述两个工具的 ToolRegistry。

    Raises:
        TypeError: tool_timeout_seconds 不是 int 或 float，或传入了 bool。
        ValueError: tool_timeout_seconds 非有限或不大于零。
    """

    if type(tool_timeout_seconds) is not float and type(tool_timeout_seconds) is not int:
        raise TypeError("tool_timeout_seconds 必须是真正的有限正数")
    if isinstance(tool_timeout_seconds, bool) or not math.isfinite(tool_timeout_seconds) or tool_timeout_seconds <= 0:
        raise ValueError("tool_timeout_seconds 必须是真正的有限正数")

    def search_handler(arguments: BaseModel) -> dict:
        params = SearchAzusaArgs.model_validate(arguments)
        try:
            search_resp = adapter.search_azusa(params.query, params.k, params.mode)
            return search_resp.model_dump(mode="json")
        except AzusaAdapterError as e:
            raise ToolExecutionError(str(e),retryable=e.retryable)

    def chunk_handler(arguments: BaseModel) -> dict:
        params = GetChunkArgs.model_validate(arguments)
        try:
            chunk_resp = adapter.get_chunk_by_id(params.chunk_id)
            return chunk_resp.model_dump(mode="json")
        except AzusaAdapterError as e:
            raise ToolExecutionError(str(e),retryable=e.retryable)
        
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="search_azusa",
            description="搜索 Azusa 中的证据片段，返回包含 chunk_id 的检索结果。",
            arguments_model=SearchAzusaArgs,
            result_model=SearchResponse,
            handler=search_handler,
            timeout_seconds=tool_timeout_seconds,
            max_retries=1,
        )
    )

    registry.register(
        ToolDefinition(
            name="get_chunk_by_id",
            description="输入chunk_id, 返回完整的证据原文和metadata",
            arguments_model=GetChunkArgs,
            result_model=ChunkResponse,
            handler=chunk_handler,
            timeout_seconds=tool_timeout_seconds,
            max_retries=1,
        )
    )

    return registry
