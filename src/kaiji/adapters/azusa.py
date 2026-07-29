from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field


class SearchHit(BaseModel):
    chunk_id: str
    text: str
    score: float | None = None
    source: str | None = None
    page: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    hits: list[SearchHit]
    mode: str


class ChunkResponse(BaseModel):
    chunk_id: str
    text: str
    source: str | None = None
    page: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AzusaAdapterError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class AzusaAdapter(Protocol):
    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse: ...

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse: ...


class AzusaHttpAdapter:
    """Adapter for the stable semantic HTTP contract expected by Kaiji.

    The corresponding thin gateway is intentionally outside Kaiji. It should
    expose POST /search_azusa and GET /chunks/{chunk_id}.
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
        except (httpx.RequestError, ValueError) as exc:
            raise AzusaAdapterError(str(exc), retryable=True) from exc

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        try:
            response = self._client.get(f"/chunks/{chunk_id}")
            response.raise_for_status()
            return ChunkResponse.model_validate(response.json())
        except httpx.TimeoutException as exc:
            raise AzusaAdapterError("Azusa chunk lookup timed out", retryable=True) from exc
        except httpx.HTTPStatusError as exc:
            raise AzusaAdapterError(
                f"Azusa chunk lookup returned HTTP {exc.response.status_code}",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise AzusaAdapterError(str(exc), retryable=True) from exc

    def close(self) -> None:
        self._client.close()


class LegacyAzusaVectorHttpAdapter:
    """Compatibility adapter for Azusa's current Rust demo HTTP API.

    Current Azusa accepts vectors at POST /search, so a query encoder must be
    injected. It only represents the HNSW mode. Record metadata must contain
    chunk_id; the Rust service does not store chunk text, so a local text
    resolver is also injected.
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
        if mode != "hnsw":
            raise AzusaAdapterError(
                "current Rust Azusa HTTP API only supports hnsw mode",
                retryable=False,
            )
        try:
            response = self._client.post(
                "/search",
                json={"vector": self._encoder(query), "k": k, "ef_search": 16},
            )
            response.raise_for_status()
            hits = []
            for item in response.json()["results"]:
                record = self._client.get(f"/record/{item['id']}")
                record.raise_for_status()
                body = record.json()
                metadata = body.get("metadata") or {}
                chunk_id = str(metadata["chunk_id"])
                hits.append(
                    SearchHit(
                        chunk_id=chunk_id,
                        text=self._text_resolver(chunk_id),
                        score=item.get("score"),
                        source=metadata.get("source"),
                        page=metadata.get("page"),
                        metadata={"record_id": item["id"]},
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
        except (httpx.RequestError, KeyError, ValueError) as exc:
            raise AzusaAdapterError(str(exc), retryable=True) from exc

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        return ChunkResponse(chunk_id=chunk_id, text=self._text_resolver(chunk_id))

    def close(self) -> None:
        self._client.close()


class MockAzusaAdapter:
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
        try:
            return self._chunks[chunk_id]
        except KeyError as exc:
            raise AzusaAdapterError(
                f"unknown chunk_id: {chunk_id}", retryable=False
            ) from exc
