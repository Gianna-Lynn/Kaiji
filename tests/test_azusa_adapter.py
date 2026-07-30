"""验证 Mock、语义 HTTP 与当前 Rust Azusa 兼容适配器的契约。"""

import httpx
import pytest

from kaiji.adapters.azusa import (
    AzusaAdapterError,
    AzusaHttpAdapter,
    LegacyAzusaVectorHttpAdapter,
    MockAzusaAdapter,
)
from kaiji.azusa_tools import build_azusa_registry
from kaiji.models import ToolCall
from kaiji.tools import ToolExecutor


def test_mock_adapter_and_tool_registry_form_offline_loop() -> None:
    adapter = MockAzusaAdapter()
    executor = ToolExecutor(build_azusa_registry(adapter))

    result = executor.execute(
        ToolCall(
            call_id="azusa-1",
            name="search_azusa",
            arguments={"query": "Kaiji", "k": 2, "mode": "hybrid"},
        )
    )

    assert result.ok
    assert result.output is not None
    assert result.output["hits"][0]["chunk_id"] == "kaiji-001"


def test_semantic_http_adapter_uses_stable_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search_azusa"
        return httpx.Response(
            200,
            json={
                "hits": [{"chunk_id": "c1", "text": "evidence"}],
                "mode": "hybrid",
            },
        )

    adapter = AzusaHttpAdapter(
        "http://azusa.test", transport=httpx.MockTransport(handler)
    )

    result = adapter.search_azusa("question", 3, "hybrid")

    assert result.hits[0].text == "evidence"


def test_semantic_http_adapter_classifies_400_as_non_retryable() -> None:
    adapter = AzusaHttpAdapter(
        "http://azusa.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(400, request=request)
        ),
    )

    with pytest.raises(AzusaAdapterError) as caught:
        adapter.search_azusa("question", 3, "bad-mode")

    assert not caught.value.retryable


def test_legacy_adapter_maps_current_vector_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200, json={"results": [{"id": 7, "score": 0.9}]}
            )
        if request.url.path == "/record/7":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "vector": [1.0, 0.0],
                    "metadata": {"chunk_id": "c7", "source": "fixture"},
                },
            )
        raise AssertionError(request.url)

    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: [1.0, 0.0],
        text_resolver=lambda chunk_id: f"text for {chunk_id}",
        transport=httpx.MockTransport(handler),
    )

    result = adapter.search_azusa("question", 1, "hnsw")

    assert result.hits[0].chunk_id == "c7"
    assert result.hits[0].text == "text for c7"


def test_legacy_adapter_rejects_modes_current_server_cannot_support() -> None:
    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: [1.0],
        text_resolver=lambda chunk_id: "text",
    )

    with pytest.raises(AzusaAdapterError) as caught:
        adapter.search_azusa("question", 1, "hybrid")

    assert not caught.value.retryable
