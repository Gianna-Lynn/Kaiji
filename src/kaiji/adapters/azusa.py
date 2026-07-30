"""定义 Azusa 语义接口、离线 Mock 和当前 Rust 服务的兼容适配器。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field


class SearchHit(BaseModel):
    """表示一条由 Azusa 返回并可作为证据使用的检索命中。"""

    chunk_id: str
    text: str
    score: float | None = None
    source: str | None = None
    page: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """封装 Azusa 检索命中及实际使用的检索模式。"""

    hits: list[SearchHit]
    mode: str


class ChunkResponse(BaseModel):
    """表示按稳定 chunk_id 取回的完整证据正文与元数据。"""

    chunk_id: str
    text: str
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
        """通过语义网关搜索证据；练习版需要补全 HTTP 与错误分类。"""
        # TODO 7a：向 /search_azusa POST query/k/mode，检查状态码后校验
        # SearchResponse。超时、网络错误与 5xx 可重试，4xx 不可重试。
        del query, k, mode
        raise AzusaAdapterError("TODO 7a: semantic search adapter", retryable=False)

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """按 chunk_id 读取完整证据；练习版需要补全 HTTP 与错误分类。"""
        # TODO 7b：GET /chunks/{chunk_id}，校验 ChunkResponse，并沿用与
        # search 相同的可重试边界。
        del chunk_id
        raise AzusaAdapterError("TODO 7b: semantic chunk adapter", retryable=False)

    def close(self) -> None:
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

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        """通过当前 Rust 向量接口搜索，并恢复 Kaiji 需要的证据字段。"""
        # TODO 7c：
        # - 拒绝 hnsw 以外的 mode；
        # - 编码 query，并向当前 Rust /search 发送 POST；
        # - 对每个命中调用 GET /record/{id}；
        # - 从 metadata 恢复 chunk_id，从 text_resolver 恢复正文；
        # - 保留 score/source/page，并正确分类 HTTP 失败。
        del query, k, mode
        raise AzusaAdapterError("TODO 7c: legacy vector adapter", retryable=False)

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """用注入的正文解析器按 chunk_id 构造兼容响应。"""

        return ChunkResponse(chunk_id=chunk_id, text=self._text_resolver(chunk_id))

    def close(self) -> None:
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
