"""定义 Azusa 语义接口、离线 Mock 和当前 Rust 服务的兼容适配器。"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator


def _non_blank(value: object, field_name: str) -> str:
    """拒绝空值和纯空白字符串，同时不改写合法字符串。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空且非纯空白字符串")
    return value


class SearchHit(BaseModel):
    """表示一条由 Azusa 返回并可作为证据使用的检索命中。"""

    chunk_id: StrictStr
    text: StrictStr
    score: int | float | None = None
    source: str | None = None
    page: StrictInt | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _valid_strings = field_validator("chunk_id", "text")(
        lambda value, info: _non_blank(value, info.field_name)
    )

    @field_validator("score", mode="before")
    @classmethod
    def score_is_finite_number(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("score 必须是有限数值")
        return value


class SearchResponse(BaseModel):
    """封装 Azusa 检索命中及实际使用的检索模式。"""

    hits: list[SearchHit]
    mode: StrictStr

    _valid_mode = field_validator("mode")(
        lambda value, info: _non_blank(value, info.field_name)
    )


class ChunkResponse(BaseModel):
    """表示按稳定 chunk_id 取回的完整证据正文与元数据。"""

    chunk_id: StrictStr
    text: StrictStr
    source: str | None = None
    page: StrictInt | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    _valid_strings = field_validator("chunk_id", "text")(
        lambda value, info: _non_blank(value, info.field_name)
    )


class AzusaAdapterError(Exception):
    """表示适配器边界内的失败，并保留是否适合重试。"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        if type(retryable) is not bool:
            raise TypeError("retryable 必须是真正的 bool")
        super().__init__(message)
        self.retryable = retryable


class AzusaAdapter(Protocol):
    """规定 Kaiji 依赖的两个稳定 Azusa 语义调用。"""

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse: ...

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse: ...


class _LegacySearchItem(BaseModel):
    """校验旧版 ``/search`` 中一条检索结果的必要字段。"""

    id: StrictInt | StrictStr
    score: int | float | None = None

    @field_validator("id")
    @classmethod
    def id_is_valid(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("id 不得为空或纯空白")
        return value

    @field_validator("score", mode="before")
    @classmethod
    def score_is_finite_number(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("score 必须是有限数值")
        return value


class _LegacySearchResponse(BaseModel):
    """校验旧版 ``/search`` 的响应外壳。"""

    results: list[_LegacySearchItem]


class _LegacyRecordResponse(BaseModel):
    """校验旧版 ``/record/{id}`` 中 Kaiji 依赖的元数据。"""

    id: StrictInt | StrictStr
    metadata: dict[str, Any]

    @field_validator("id")
    @classmethod
    def id_is_valid(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("id 不得为空或纯空白")
        return value


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

        Adapter 只负责标记失败是否可重试，真正的有限重试由
        ``ToolExecutor`` 统一执行。超时、网络错误、5xx 和无效响应可能来自
        网关的瞬时故障，因此标记为可重试；4xx 表示请求本身有误，不可重试。
        """

        try:
            response = self._client.post(
                "/search_azusa",
                json={"query": query, "k": k, "mode": mode},
            )
            response.raise_for_status()
            return SearchResponse.model_validate(response.json())
        except httpx.TimeoutException as exc:
            raise AzusaAdapterError("Azusa search timed out", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code >= 500
            raise AzusaAdapterError(
                f"Azusa search returned HTTP {exc.response.status_code}",
                retryable=retryable,
            ) from exc
        except httpx.RequestError as exc:
            raise AzusaAdapterError(
                f"Azusa search request failed: {exc}", retryable=True
            ) from exc
        except (ValueError, TypeError) as exc:
            raise AzusaAdapterError(
                f"Azusa search returned an invalid response: {exc}",
                retryable=True,
            ) from exc

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """按 chunk_id 读取完整证据，并校验响应结构。"""

        try:
            encoded_chunk_id = quote(chunk_id, safe="")
            response = self._client.get(f"/chunks/{encoded_chunk_id}")
            response.raise_for_status()
            chunk = ChunkResponse.model_validate(response.json())
            if chunk.chunk_id != chunk_id:
                raise ValueError(
                    "Azusa chunk response ID does not match the requested chunk_id"
                )
            return chunk
        except httpx.TimeoutException as exc:
            raise AzusaAdapterError("Azusa chunk lookup timed out", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise AzusaAdapterError(
                f"Azusa chunk lookup returned HTTP {exc.response.status_code}",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except httpx.RequestError as exc:
            raise AzusaAdapterError(
                f"Azusa chunk lookup request failed: {exc}", retryable=True
            ) from exc
        except (ValueError, TypeError) as exc:
            raise AzusaAdapterError(
                f"Azusa chunk lookup returned an invalid response: {exc}",
                retryable=True,
            ) from exc

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
        """调用本地正文解析器，并把异常或非法返回统一标为不可重试。"""

        try:
            text = self._text_resolver(chunk_id)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("text resolver 必须返回非空且非纯空白字符串")
            return text
        except Exception as exc:
            raise AzusaAdapterError(
                f"legacy Azusa text resolution failed for {chunk_id}: {exc}",
                retryable=False,
            ) from exc

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        """通过当前 Rust 向量接口搜索，并恢复 Kaiji 需要的证据字段。"""

        if mode != "hnsw":
            raise AzusaAdapterError(
                "current Rust Azusa HTTP API only supports hnsw mode",
                retryable=False,
            )
        try:
            raw_vector = self._encoder(query)
            if (
                not isinstance(raw_vector, list)
                or not raw_vector
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in raw_vector
                )
            ):
                raise ValueError("query encoder 必须返回非空的有限数值列表")
            vector = [float(value) for value in raw_vector]
        except Exception as exc:
            raise AzusaAdapterError(
                f"legacy Azusa query encoding failed: {exc}", retryable=False
            ) from exc

        try:
            response = self._client.post(
                "/search",
                json={"vector": vector, "k": k, "ef_search": 16},
            )
            response.raise_for_status()
            hits = []
            payload = _LegacySearchResponse.model_validate(response.json())
            for item in payload.results:
                record_id = str(item.id)
                record = self._client.get(f"/record/{quote(record_id, safe='')}")
                record.raise_for_status()
                body = _LegacyRecordResponse.model_validate(record.json())
                if type(body.id) is not type(item.id) or body.id != item.id:
                    raise ValueError("legacy Azusa record ID does not match the search result")
                metadata = dict(body.metadata)
                if "record_id" in metadata:
                    raise ValueError("legacy metadata 保留字段 record_id 不得由远端提供")
                raw_chunk_id = metadata.get("chunk_id")
                if not isinstance(raw_chunk_id, str) or not raw_chunk_id.strip():
                    raise AzusaAdapterError(
                        "legacy Azusa record metadata needs a non-empty string chunk_id",
                        retryable=True,
                    )
                chunk_id = raw_chunk_id
                text = self._resolve_text(chunk_id)
                hit_metadata = {**metadata, "record_id": item.id}
                hits.append(
                    SearchHit(
                        chunk_id=chunk_id,
                        text=text,
                        score=item.score,
                        source=metadata.get("source"),
                        page=metadata.get("page"),
                        metadata=hit_metadata,
                    )
                )
            return SearchResponse(hits=hits, mode=mode)
        except httpx.TimeoutException as exc:
            raise AzusaAdapterError("legacy Azusa timed out", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise AzusaAdapterError(
                f"legacy Azusa returned HTTP {exc.response.status_code}",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except httpx.RequestError as exc:
            raise AzusaAdapterError(
                f"legacy Azusa request failed: {exc}", retryable=True
            ) from exc
        except (ValueError, TypeError) as exc:
            raise AzusaAdapterError(
                f"legacy Azusa returned an invalid response: {exc}", retryable=True
            ) from exc

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        """用注入的正文解析器按 chunk_id 构造兼容响应。"""

        try:
            chunk_id = _non_blank(chunk_id, "chunk_id")
        except ValueError as exc:
            raise AzusaAdapterError(str(exc), retryable=False) from exc
        return ChunkResponse(chunk_id=chunk_id, text=self._resolve_text(chunk_id))

    def close(self) -> None:
        """关闭底层 HTTP 客户端连接池。"""

        self._client.close()


class MockAzusaAdapter:
    """提供无需网络、GPU 或真实 Azusa 服务的确定性检索结果。"""

    def __init__(self, chunks: list[ChunkResponse] | None = None) -> None:
        self._chunks = {
            chunk.chunk_id: chunk
            for chunk in (
                chunks if chunks is not None else [
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
