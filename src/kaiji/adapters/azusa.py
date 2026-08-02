"""定义 Azusa 语义接口、离线 Mock 和当前 Rust 服务的兼容适配器。"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field


class SearchHit(BaseModel):
    """表示一条由 Azusa 返回并可作为证据使用的检索命中。"""

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    score: float | None = None
    source: str | None = None
    page: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """封装 Azusa 检索命中及实际使用的检索模式。"""

    hits: list[SearchHit]
    mode: str = Field(min_length=1)


class ChunkResponse(BaseModel):
    """表示按稳定 chunk_id 取回的完整证据正文与元数据。"""

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str | None = None
    page: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AzusaAdapterError(Exception):
    """表示适配器边界内的失败，并保留是否适合重试。"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class AzusaAdapter(Protocol):
    """规定 Kaiji 依赖的两个稳定 Azusa 语义调用。"""

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse: ...

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse: ...


class _LegacySearchItem(BaseModel):
    """校验旧版 ``/search`` 中一条检索结果的必要字段。"""

    id: int | str
    score: float | None = None


class _LegacySearchResponse(BaseModel):
    """校验旧版 ``/search`` 的响应外壳。"""

    results: list[_LegacySearchItem]


class _LegacyRecordResponse(BaseModel):
    """校验旧版 ``/record/{id}`` 中 Kaiji 依赖的元数据。"""

    id: int | str
    metadata: dict[str, Any]


class AzusaHttpAdapter:
    """调用 Kaiji 期望的稳定 Azusa 语义 HTTP 契约。

    对应的薄网关刻意位于 Kaiji 仓库之外，并暴露
    POST /search_azusa 与 GET /chunks/{chunk_id}。
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        """通过语义网关搜索证据，并把边界失败映射为重试策略。

        TODO 7a 的输入必须原样组成 ``query/k/mode`` JSON；成功返回值必须由
        ``SearchResponse`` 校验。Adapter 只分类并抛出 ``AzusaAdapterError``，
        不在这里循环重试：timeout、network、HTTP 5xx、非法 JSON 和响应 Schema
        不匹配标记为可重试，HTTP 4xx 标记为不可重试。
        """

        # TODO 7a：实现 POST /search_azusa、响应校验和上述完整错误分类。
        raise NotImplementedError("TODO 7a：实现语义搜索 Adapter 契约")

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """按稳定 ``chunk_id`` 读取完整证据，并防止证据错配。

        TODO 7b 必须把 ``chunk_id`` 当作单个 URL 路径段编码，再校验
        ``ChunkResponse``；响应中的 ``chunk_id`` 还必须与请求值完全一致。
        错误分类和“不在 Adapter 内重试”的边界与 TODO 7a 相同。
        """

        # TODO 7b：实现 GET /chunks/{encoded_chunk_id}、响应校验和错误分类。
        raise NotImplementedError("TODO 7b：实现证据读取 Adapter 契约")

    def close(self) -> None:
        """关闭底层 HTTP 客户端连接池。"""

        self._client.close()


class LegacyAzusaVectorHttpAdapter:
    """兼容 Azusa 当前 Rust 演示 HTTP API 的适配器。

    当前 Azusa 的 POST /search 接收向量，因此必须注入 query encoder，而且只表达
    HNSW 模式。记录元数据必须包含 chunk_id；Rust 服务不保存 chunk 正文，所以还要
    注入本地 text resolver。
    """

    def __init__(
        self,
        base_url: str,
        *,
        query_encoder: Callable[[str], list[float]],
        text_resolver: Callable[[str], str],
        timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._encoder = query_encoder
        self._text_resolver = text_resolver
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    def _resolve_text(self, chunk_id: str) -> str:
        """恢复旧服务未保存的正文，并统一本地 resolver 的失败边界。

        TODO 7c 要求 resolver 返回非空、非纯空白字符串。resolver 抛出的异常或
        非法返回值属于本地契约错误，统一包装成不可重试的
        ``AzusaAdapterError``；不能把它误判为远端瞬时故障。
        """

        raise NotImplementedError("TODO 7c：校验并包装正文 resolver")

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        """通过旧版 Rust 向量接口搜索，并恢复 Kaiji 的语义证据模型。

        TODO 7c 的数据流是 ``query -> encoder -> POST /search -> 每条命中的
        GET /record/{id} -> metadata.chunk_id -> resolver -> SearchHit``。
        只接受 ``hnsw``；请求体必须包含 ``vector/k`` 和固定
        ``ef_search=16``。encoder 必须返回非空有限数值列表，布尔值不算数值；
        record ID 必须与命中 ID 一致；``metadata.chunk_id`` 必须是非空、非纯
        空白字符串。命中的完整 metadata 要保留并追加原始 ``record_id``，同时
        映射 ``score/source/page``。

        encoder/resolver 的异常或非法返回不可重试；远端 HTTP 两阶段沿用 TODO 7a
        的分类。路径中的 record ID 必须编码。并发、缓存和连接池调优不在练习范围。
        """

        # TODO 7c：实现上述兼容转换与本地/远端两类失败边界。
        raise NotImplementedError("TODO 7c：实现旧版向量 Adapter 契约")

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """用同一套 resolver 契约按 ``chunk_id`` 构造兼容响应。"""

        # TODO 7c：复用 _resolve_text，不能让直接读取绕过 resolver 校验和错误包装。
        raise NotImplementedError("TODO 7c：实现旧版证据读取契约")

    def close(self) -> None:
        """关闭底层 HTTP 客户端连接池。"""

        self._client.close()


class MockAzusaAdapter:
    """提供无需网络、GPU 或真实 Azusa 服务的确定性检索结果。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, chunks: list[ChunkResponse] | None = None) -> None:
        self._chunks = {
            chunk.chunk_id: chunk
            for chunk in (
                chunks
                or [
                    ChunkResponse(
                        chunk_id="kaiji-001",
                        text="Kaiji owns tool, state, workflow, trace, and eval.",
                        source="mock://kaiji",
                    ),
                    ChunkResponse(
                        chunk_id="azusa-001",
                        text="Azusa provides HNSW and RAG retrieval through adapters.",
                        source="mock://azusa",
                    ),
                ]
            )
        }

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        """用简单词项匹配搜索内存中的教学证据。"""

        terms = set(query.lower().split())
        ranked = sorted(
            self._chunks.values(),
            key=lambda chunk: -sum(term in chunk.text.lower() for term in terms),
        )
        hits = [
            SearchHit(**chunk.model_dump(), score=float(len(ranked) - index))
            for index, chunk in enumerate(ranked[:k])
            if not terms or any(term in chunk.text.lower() for term in terms)
        ]
        return SearchResponse(hits=hits, mode=mode)

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """按 chunk_id 返回内存证据；未知 ID 为不可重试失败。"""

        try:
            return self._chunks[chunk_id]
        except KeyError as exc:
            raise AzusaAdapterError(
                f"unknown chunk_id: {chunk_id}", retryable=False
            ) from exc
