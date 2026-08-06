"""严格验证 Azusa Adapter、HTTP 边界和工具注册契约。"""

from __future__ import annotations

import json

import httpx
import pytest

from kaiji.adapters.azusa import (
    AzusaAdapterError,
    AzusaHttpAdapter,
    ChunkResponse,
    LegacyAzusaVectorHttpAdapter,
    MockAzusaAdapter,
    SearchResponse,
)
from kaiji.azusa_tools import (
    GetChunkArgs,
    SearchAzusaArgs,
    build_azusa_registry,
)
from kaiji.models import ToolCall, ToolErrorCode
from kaiji.tools import ToolExecutor


def test_mock_adapter_and_both_registered_tools_form_offline_loop() -> None:
    adapter = MockAzusaAdapter()
    executor = ToolExecutor(build_azusa_registry(adapter))

    search = executor.execute(
        ToolCall(
            call_id="azusa-search-1",
            name="search_azusa",
            arguments={"query": "Kaiji", "k": 2, "mode": "hybrid"},
        )
    )
    chunk = executor.execute(
        ToolCall(
            call_id="azusa-chunk-1",
            name="get_chunk_by_id",
            arguments={"chunk_id": "kaiji-001"},
        )
    )

    assert search.ok
    assert search.output is not None
    assert search.output["hits"][0]["chunk_id"] == "kaiji-001"
    assert chunk.ok
    assert chunk.output is not None
    assert chunk.output["chunk_id"] == "kaiji-001"


def test_registry_exposes_two_precise_argument_schemas_and_retry_policy() -> None:
    registry = build_azusa_registry(MockAzusaAdapter())
    schemas = {schema["name"]: schema for schema in registry.schemas()}

    assert set(schemas) == {"search_azusa", "get_chunk_by_id"}
    assert "证据" in schemas["search_azusa"]["description"]
    assert "证据" in schemas["get_chunk_by_id"]["description"]

    search_parameters = schemas["search_azusa"]["parameters"]
    assert search_parameters["required"] == ["query"]
    assert search_parameters["properties"]["query"]["minLength"] == 2
    k_schema = search_parameters["properties"]["k"]
    assert k_schema["default"] == 3
    assert k_schema["minimum"] == 1
    assert k_schema["maximum"] == 20
    assert k_schema["type"] == "integer"
    assert search_parameters["properties"]["mode"]["default"] == "hybrid"
    assert search_parameters["properties"]["mode"]["minLength"] == 1

    chunk_parameters = schemas["get_chunk_by_id"]["parameters"]
    assert chunk_parameters["required"] == ["chunk_id"]
    assert chunk_parameters["properties"]["chunk_id"]["minLength"] == 1

    search_definition = registry.get("search_azusa")
    chunk_definition = registry.get("get_chunk_by_id")
    assert search_definition is not None
    assert chunk_definition is not None
    assert search_definition.arguments_model is SearchAzusaArgs
    assert search_definition.result_model is SearchResponse
    assert search_definition.max_retries == 1
    assert chunk_definition.arguments_model is GetChunkArgs
    assert chunk_definition.result_model is ChunkResponse
    assert chunk_definition.max_retries == 1

    search_output = search_definition.handler(SearchAzusaArgs(query="Kaiji"))
    chunk_output = chunk_definition.handler(GetChunkArgs(chunk_id="kaiji-001"))
    assert type(search_output) is dict
    assert type(chunk_output) is dict
    assert search_output["mode"] == "hybrid"
    assert chunk_output["chunk_id"] == "kaiji-001"


def test_semantic_http_adapter_uses_exact_search_contract_and_maps_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/search_azusa"
        assert json.loads(request.content) == {
            "query": "question",
            "k": 3,
            "mode": "hybrid",
        }
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "chunk_id": "c1",
                        "text": "evidence",
                        "score": 0.75,
                        "source": "fixture",
                        "page": 4,
                        "metadata": {"section": "intro"},
                    }
                ],
                "mode": "hybrid",
            },
        )

    adapter = AzusaHttpAdapter(
        "http://azusa.test", transport=httpx.MockTransport(handler)
    )
    try:
        result = adapter.search_azusa("question", 3, "hybrid")
    finally:
        adapter.close()

    assert result == SearchResponse(
        hits=[
            {
                "chunk_id": "c1",
                "text": "evidence",
                "score": 0.75,
                "source": "fixture",
                "page": 4,
                "metadata": {"section": "intro"},
            }
        ],
        mode="hybrid",
    )


def test_todo_7a_semantic_search_success_narrow_path() -> None:
    adapter = AzusaHttpAdapter("http://azusa.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"hits": [], "mode": "hybrid"})))
    try:
        assert adapter.search_azusa("question", 2, "hybrid") == SearchResponse(hits=[], mode="hybrid")
    finally:
        adapter.close()


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None])
def test_todo_7d_retryable_requires_real_bool_and_never_retries(bad: object) -> None:
    with pytest.raises(TypeError):
        AzusaAdapterError("bad retry flag", retryable=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [True, False, 1.0, "3", None])
def test_todo_7d_invalid_k_is_rejected_before_adapter_call(bad: object) -> None:
    adapter = _SpyAdapter()
    result = ToolExecutor(build_azusa_registry(adapter)).execute(
        ToolCall(call_id="bad-k", name="search_azusa", arguments={"query": "ok", "k": bad})
    )
    assert not result.ok and result.attempts == 0
    assert result.error is not None and result.error.code == ToolErrorCode.INVALID_ARGUMENTS
    assert adapter.search_calls == []


def test_todo_7d_argument_schema_is_strict_and_forbids_extra_fields() -> None:
    search_schema = SearchAzusaArgs.model_json_schema()
    chunk_schema = GetChunkArgs.model_json_schema()
    assert search_schema["additionalProperties"] is False
    assert chunk_schema["additionalProperties"] is False
    with pytest.raises(Exception):
        SearchAzusaArgs(query="ok", unexpected=True)
    with pytest.raises(Exception):
        GetChunkArgs(chunk_id="c1", unexpected=True)


def test_todo_7d_registry_timeout_contract_is_explicit() -> None:
    registry = build_azusa_registry(MockAzusaAdapter())
    assert registry.get("search_azusa").timeout_seconds == 4.0  # type: ignore[union-attr]
    assert registry.get("get_chunk_by_id").timeout_seconds == 4.0  # type: ignore[union-attr]
    for bad in [True, False, 0, -1, float("nan"), float("inf"), "4"]:
        with pytest.raises((TypeError, ValueError)):
            build_azusa_registry(MockAzusaAdapter(), tool_timeout_seconds=bad)  # type: ignore[arg-type]


def test_todo_7c_mock_empty_collection_semantics() -> None:
    empty = MockAzusaAdapter(chunks=[])
    assert empty.search_azusa("anything", 3, "hybrid").hits == []
    with pytest.raises(AzusaAdapterError) as caught:
        empty.get_chunk_by_id("missing")
    assert caught.value.retryable is False


def test_todo_7c_record_id_type_mismatch_does_not_call_resolver() -> None:
    resolver_calls = 0
    def resolver(_: str) -> str:
        nonlocal resolver_calls
        resolver_calls += 1
        return "text"
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"id": 7}]})
        return httpx.Response(200, json={"id": "7", "metadata": {"chunk_id": "c7"}})
    adapter = LegacyAzusaVectorHttpAdapter("http://azusa.test", query_encoder=lambda _: [1], text_resolver=resolver, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AzusaAdapterError):
            adapter.search_azusa("q", 1, "hnsw")
    finally:
        adapter.close()
    assert resolver_calls == 0


def test_todo_7c_remote_record_id_is_not_silently_overwritten() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"id": 7}]})
        return httpx.Response(200, json={"id": 7, "metadata": {"chunk_id": "c7", "record_id": "remote"}})
    adapter = LegacyAzusaVectorHttpAdapter("http://azusa.test", query_encoder=lambda _: [1], text_resolver=lambda _: "text", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AzusaAdapterError):
            adapter.search_azusa("q", 1, "hnsw")
    finally:
        adapter.close()


@pytest.mark.parametrize("bad", ["", "   ", True, float("nan"), float("inf")])
def test_todo_7a_external_response_evidence_boundaries(bad: object) -> None:
    payload = {"hits": [{"chunk_id": "c", "text": "text", "score": bad}], "mode": "hybrid"}
    adapter = AzusaHttpAdapter("http://azusa.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    try:
        with pytest.raises(AzusaAdapterError):
            adapter.search_azusa("q", 1, "hybrid")
    finally:
        adapter.close()


@pytest.mark.parametrize("status, retryable", [(301, False), (429, False), (499, False), (500, True), (599, True)])
def test_todo_7a_http_status_policy_includes_redirect_rate_limit_and_edges(status: int, retryable: bool) -> None:
    adapter = AzusaHttpAdapter("http://azusa.test", transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            adapter.search_azusa("q", 1, "hybrid")
    finally:
        adapter.close()
    assert caught.value.retryable is retryable


def test_todo_7b_semantic_get_chunk_narrow_path() -> None:
    adapter = AzusaHttpAdapter("http://azusa.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"chunk_id": "c", "text": "e"})))
    try:
        assert adapter.get_chunk_by_id("c").text == "e"
    finally:
        adapter.close()


def test_todo_7c_resolver_narrow_path() -> None:
    adapter = LegacyAzusaVectorHttpAdapter("http://azusa.test", query_encoder=lambda _: [1], text_resolver=lambda _: "正文")
    try:
        assert adapter.get_chunk_by_id("c").text == "正文"
    finally:
        adapter.close()


def test_todo_7c_encoder_narrow_path() -> None:
    requests = 0
    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"results": []})
    adapter = LegacyAzusaVectorHttpAdapter("http://azusa.test", query_encoder=lambda _: [1, 2.0], text_resolver=lambda _: "正文", transport=httpx.MockTransport(handler))
    try:
        assert adapter.search_azusa("q", 1, "hnsw").hits == []
    finally:
        adapter.close()
    assert requests == 1


def test_todo_7c_search_narrow_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [{"id": 7, "score": 0.5}]})
        return httpx.Response(200, json={"id": 7, "metadata": {"chunk_id": "c"}})
    adapter = LegacyAzusaVectorHttpAdapter("http://azusa.test", query_encoder=lambda _: [1], text_resolver=lambda _: "正文", transport=httpx.MockTransport(handler))
    try:
        assert adapter.search_azusa("q", 1, "hnsw").hits[0].chunk_id == "c"
    finally:
        adapter.close()


def test_todo_7c_get_chunk_narrow_path_rejects_bad_id_without_resolver() -> None:
    calls = 0
    def resolver(_: str) -> str:
        nonlocal calls
        calls += 1
        return "正文"
    adapter = LegacyAzusaVectorHttpAdapter("http://azusa.test", query_encoder=lambda _: [1], text_resolver=resolver)
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            adapter.get_chunk_by_id("   ")
    finally:
        adapter.close()
    assert caught.value.retryable is False and calls == 0


def test_semantic_http_adapter_forwards_non_default_search_arguments() -> None:
    """第二组非默认参数防止 Adapter 把示例值硬编码进请求体。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/search_azusa"
        assert json.loads(request.content) == {
            "query": "另一条检索问题",
            "k": 7,
            "mode": "bm25",
        }
        return httpx.Response(200, json={"hits": [], "mode": "bm25"})

    adapter = AzusaHttpAdapter(
        "http://azusa.test", transport=httpx.MockTransport(handler)
    )
    try:
        result = adapter.search_azusa("另一条检索问题", 7, "bm25")
    finally:
        adapter.close()

    assert result == SearchResponse(hits=[], mode="bm25")


def test_semantic_http_adapter_encodes_chunk_id_and_validates_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path == b"/chunks/folder%2Fchunk%201"
        return httpx.Response(
            200,
            json={
                "chunk_id": "folder/chunk 1",
                "text": "full evidence",
                "source": "fixture",
                "page": 2,
                "metadata": {"kind": "paragraph"},
            },
        )

    adapter = AzusaHttpAdapter(
        "http://azusa.test", transport=httpx.MockTransport(handler)
    )
    try:
        result = adapter.get_chunk_by_id("folder/chunk 1")
    finally:
        adapter.close()

    assert result.chunk_id == "folder/chunk 1"
    assert result.text == "full evidence"
    assert result.metadata == {"kind": "paragraph"}


@pytest.mark.parametrize(
    "operation",
    ["search", "chunk"],
)
@pytest.mark.parametrize(
    ("failure", "retryable"),
    [
        ("timeout", True),
        ("network", True),
        ("status-400", False),
        ("status-503", True),
        ("malformed-json", True),
        ("invalid-schema", True),
    ],
)
def test_semantic_http_adapter_classifies_boundary_failures(
    operation: str, failure: str, retryable: bool
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("slow gateway", request=request)
        if failure == "network":
            raise httpx.ConnectError("unreachable gateway", request=request)
        if failure.startswith("status-"):
            return httpx.Response(int(failure.removeprefix("status-")))
        if failure == "malformed-json":
            return httpx.Response(
                200,
                content=b"{",
                headers={"content-type": "application/json"},
            )
        if failure == "invalid-schema":
            if operation == "search":
                return httpx.Response(200, json={"unexpected": []})
            return httpx.Response(200, json={"chunk_id": "c1"})
        raise AssertionError(failure)

    adapter = AzusaHttpAdapter(
        "http://azusa.test", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            if operation == "search":
                adapter.search_azusa("question", 3, "hybrid")
            else:
                adapter.get_chunk_by_id("c1")
    finally:
        adapter.close()

    assert caught.value.retryable is retryable
    # Adapter 只分类，不在 HTTP 边界内部偷偷重试。
    assert len(requests) == 1


def test_semantic_http_adapter_rejects_mismatched_chunk_id() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            json={"chunk_id": "different", "text": "evidence"},
        )

    adapter = AzusaHttpAdapter(
        "http://azusa.test", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            adapter.get_chunk_by_id("c1")
    finally:
        adapter.close()

    assert caught.value.retryable
    assert requests == 1


def test_legacy_adapter_uses_exact_payload_and_preserves_evidence_fields() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/search":
            assert request.method == "POST"
            assert json.loads(request.content) == {
                "vector": [1.0, 0.0],
                "k": 1,
                "ef_search": 16,
            }
            return httpx.Response(
                200, json={"results": [{"id": 7, "score": 0.9}]}
            )
        if request.url.path == "/record/7":
            assert request.method == "GET"
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "vector": [1.0, 0.0],
                    "metadata": {
                        "chunk_id": "c7",
                        "source": "fixture",
                        "page": 6,
                        "section": "results",
                    },
                },
            )
        raise AssertionError(request.url)

    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: [1.0, 0.0],
        text_resolver=lambda chunk_id: f"text for {chunk_id}",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = adapter.search_azusa("question", 1, "hnsw")
        chunk = adapter.get_chunk_by_id("c7")
    finally:
        adapter.close()

    assert requests == [("POST", "/search"), ("GET", "/record/7")]
    assert result.mode == "hnsw"
    assert len(result.hits) == 1
    assert result.hits[0].model_dump() == {
        "chunk_id": "c7",
        "text": "text for c7",
        "score": 0.9,
        "source": "fixture",
        "page": 6,
        "metadata": {
            "chunk_id": "c7",
            "source": "fixture",
            "page": 6,
            "section": "results",
            "record_id": 7,
        },
    }
    assert chunk == ChunkResponse(chunk_id="c7", text="text for c7")


def test_legacy_adapter_forwards_non_default_input_encodes_ids_and_maps_all_hits() -> None:
    """非默认查询、多命中和特殊 record ID 必须完整穿过兼容数据流。"""

    encoded_queries: list[str] = []
    resolved_chunks: list[str] = []
    raw_paths: list[bytes] = []

    def encode(query: str) -> list[float]:
        encoded_queries.append(query)
        return [0.25, -0.5]

    def resolve(chunk_id: str) -> str:
        resolved_chunks.append(chunk_id)
        return f"正文 {chunk_id}"

    records = {
        b"/record/folder%2Fid%201": {
            "id": "folder/id 1",
            "metadata": {"chunk_id": "chunk-one", "source": "first"},
        },
        b"/record/second%3Fid": {
            "id": "second?id",
            "metadata": {"chunk_id": "chunk-two", "page": 9},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raw_paths.append(request.url.raw_path)
        if request.url.path == "/search":
            assert json.loads(request.content) == {
                "vector": [0.25, -0.5],
                "k": 4,
                "ef_search": 16,
            }
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": "folder/id 1", "score": 0.8},
                        {"id": "second?id", "score": 0.7},
                    ]
                },
            )
        return httpx.Response(200, json=records[request.url.raw_path])

    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=encode,
        text_resolver=resolve,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = adapter.search_azusa("非默认问题", 4, "hnsw")
    finally:
        adapter.close()

    assert encoded_queries == ["非默认问题"]
    assert raw_paths == [
        b"/search",
        b"/record/folder%2Fid%201",
        b"/record/second%3Fid",
    ]
    assert resolved_chunks == ["chunk-one", "chunk-two"]
    assert [hit.chunk_id for hit in result.hits] == ["chunk-one", "chunk-two"]
    assert [hit.metadata["record_id"] for hit in result.hits] == [
        "folder/id 1",
        "second?id",
    ]


def test_legacy_adapter_rejects_unsupported_mode_before_network_call() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError(request.url)

    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: [1.0],
        text_resolver=lambda chunk_id: "text",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            adapter.search_azusa("question", 1, "hybrid")
    finally:
        adapter.close()

    assert not caught.value.retryable
    assert requests == 0


@pytest.mark.parametrize(
    "stage",
    ["search", "record"],
)
@pytest.mark.parametrize(
    ("failure", "retryable"),
    [
        ("timeout", True),
        ("network", True),
        ("status-400", False),
        ("status-503", True),
        ("malformed-json", True),
        ("invalid-schema", True),
    ],
)
def test_legacy_adapter_classifies_both_http_stages(
    stage: str,
    failure: str,
    retryable: bool,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if stage == "record" and request.url.path == "/search":
            return httpx.Response(
                200,
                json={"results": [{"id": 7, "score": 0.9}]},
            )
        expected_path = "/search" if stage == "search" else "/record/7"
        assert request.url.path == expected_path
        if failure == "timeout":
            raise httpx.ReadTimeout("slow legacy service", request=request)
        if failure == "network":
            raise httpx.ConnectError("legacy service offline", request=request)
        if failure.startswith("status-"):
            return httpx.Response(int(failure.removeprefix("status-")))
        if failure == "malformed-json":
            return httpx.Response(200, content=b"{")
        if failure == "invalid-schema":
            return httpx.Response(200, json={"unexpected": []})
        raise AssertionError(failure)

    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: [1.0],
        text_resolver=lambda chunk_id: "text",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            adapter.search_azusa("question", 1, "hnsw")
    finally:
        adapter.close()

    assert caught.value.retryable is retryable
    assert requests == (["/search"] if stage == "search" else ["/search", "/record/7"])


@pytest.mark.parametrize(
    "metadata_or_id",
    [
        {"id": 7, "metadata": {}},
        {"id": 8, "metadata": {"chunk_id": "c7"}},
    ],
)
def test_legacy_adapter_rejects_invalid_record_contract(
    metadata_or_id: dict,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200, json={"results": [{"id": 7, "score": 0.9}]}
            )
        if request.url.path == "/record/7":
            return httpx.Response(200, json=metadata_or_id)
        raise AssertionError(request.url)

    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: [1.0],
        text_resolver=lambda chunk_id: "text",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            adapter.search_azusa("question", 1, "hnsw")
    finally:
        adapter.close()

    assert caught.value.retryable


def test_legacy_local_encoder_exception_is_non_retryable() -> None:
    def broken_encoder(query: str) -> list[float]:
        raise ValueError("encoder unavailable")

    encoder_adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=broken_encoder,
        text_resolver=lambda chunk_id: "text",
    )
    with pytest.raises(AzusaAdapterError) as encoder_error:
        encoder_adapter.search_azusa("question", 1, "hnsw")
    encoder_adapter.close()

    assert not encoder_error.value.retryable


@pytest.mark.parametrize("operation", ["search", "get_chunk"])
def test_legacy_local_resolver_exception_is_non_retryable(operation: str) -> None:
    def broken_resolver(chunk_id: str) -> str:
        raise KeyError(chunk_id)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={"results": [{"id": 7, "score": 0.9}]},
            )
        if request.url.path == "/record/7":
            return httpx.Response(
                200,
                json={"id": 7, "metadata": {"chunk_id": "c7"}},
            )
        raise AssertionError(request.url)

    resolver_adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: [1.0],
        text_resolver=broken_resolver,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AzusaAdapterError) as resolver_error:
            if operation == "search":
                resolver_adapter.search_azusa("question", 1, "hnsw")
            else:
                resolver_adapter.get_chunk_by_id("missing")
    finally:
        resolver_adapter.close()

    assert not resolver_error.value.retryable


@pytest.mark.parametrize(
    "invalid_vector",
    [
        [],
        [float("nan")],
        [float("inf")],
        [float("-inf")],
        [True],
        ["not-a-number"],
        [object()],
        (),
        None,
        "not-a-list",
    ],
)
def test_legacy_rejects_invalid_encoder_output_before_network(
    invalid_vector: object,
) -> None:
    """本地 encoder 返回值不合法时不得发请求，也不得伪装成瞬时故障。"""

    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError(request.url)

    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: invalid_vector,  # type: ignore[arg-type,return-value]
        text_resolver=lambda chunk_id: "text",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            adapter.search_azusa("question", 1, "hnsw")
    finally:
        adapter.close()

    assert not caught.value.retryable
    assert requests == 0


@pytest.mark.parametrize("invalid_text", ["", "   ", None, 7])
@pytest.mark.parametrize("operation", ["search", "get_chunk"])
def test_legacy_rejects_invalid_resolver_output_as_non_retryable(
    invalid_text: object,
    operation: str,
) -> None:
    """resolver 的非法返回值属于本地契约错误，两条入口都要统一包装。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200, json={"results": [{"id": 7, "score": 0.9}]}
            )
        if request.url.path == "/record/7":
            return httpx.Response(
                200,
                json={"id": 7, "metadata": {"chunk_id": "c7"}},
            )
        raise AssertionError(request.url)

    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: [1.0],
        text_resolver=lambda chunk_id: invalid_text,  # type: ignore[return-value]
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            if operation == "search":
                adapter.search_azusa("question", 1, "hnsw")
            else:
                adapter.get_chunk_by_id("c7")
    finally:
        adapter.close()

    assert not caught.value.retryable


@pytest.mark.parametrize("invalid_chunk_id", [None, "", "   ", 0, False, [], {}])
def test_legacy_requires_string_chunk_id_in_record_metadata(
    invalid_chunk_id: object,
) -> None:
    resolver_calls = 0

    def resolver(chunk_id: str) -> str:
        nonlocal resolver_calls
        resolver_calls += 1
        return "text"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={"results": [{"id": 7, "score": 0.9}]},
            )
        if request.url.path == "/record/7":
            return httpx.Response(
                200,
                json={"id": 7, "metadata": {"chunk_id": invalid_chunk_id}},
            )
        raise AssertionError(request.url)

    adapter = LegacyAzusaVectorHttpAdapter(
        "http://azusa.test",
        query_encoder=lambda query: [1.0],
        text_resolver=resolver,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AzusaAdapterError) as caught:
            adapter.search_azusa("question", 1, "hnsw")
    finally:
        adapter.close()

    assert caught.value.retryable
    assert resolver_calls == 0


class _SpyAdapter:
    """记录 Registry 交给 Adapter 的精确参数。"""

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, int, str]] = []
        self.chunk_calls: list[str] = []

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        self.search_calls.append((query, k, mode))
        return SearchResponse(hits=[], mode=mode)

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        self.chunk_calls.append(chunk_id)
        return ChunkResponse(chunk_id=chunk_id, text="evidence")


def test_registry_forwards_explicit_defaults_and_chunk_id_exactly_once() -> None:
    adapter = _SpyAdapter()
    executor = ToolExecutor(build_azusa_registry(adapter))

    explicit = executor.execute(
        ToolCall(
            call_id="explicit-search",
            name="search_azusa",
            arguments={"query": "explicit", "k": 7, "mode": "bm25"},
        )
    )
    defaults = executor.execute(
        ToolCall(
            call_id="default-search",
            name="search_azusa",
            arguments={"query": "defaults"},
        )
    )
    chunk = executor.execute(
        ToolCall(
            call_id="chunk",
            name="get_chunk_by_id",
            arguments={"chunk_id": "folder/chunk 1"},
        )
    )

    assert explicit.ok
    assert defaults.ok
    assert chunk.ok
    assert adapter.search_calls == [
        ("explicit", 7, "bm25"),
        ("defaults", 3, "hybrid"),
    ]
    assert adapter.chunk_calls == ["folder/chunk 1"]


class _RetryingAdapter:
    """用计数器证明重试只由 ToolExecutor 执行且最多一次。"""

    def __init__(self, *, retryable: bool, failures_before_success: int) -> None:
        self.retryable = retryable
        self.failures_before_success = failures_before_success
        self.search_calls = 0
        self.chunk_calls = 0

    def search_azusa(self, query: str, k: int, mode: str) -> SearchResponse:
        self.search_calls += 1
        if self.search_calls <= self.failures_before_success:
            raise AzusaAdapterError("search failed", retryable=self.retryable)
        return SearchResponse(hits=[], mode=mode)

    def get_chunk_by_id(self, chunk_id: str) -> ChunkResponse:
        self.chunk_calls += 1
        raise AzusaAdapterError("chunk failed", retryable=self.retryable)


def test_registry_propagates_retryable_error_and_executor_retries_once() -> None:
    adapter = _RetryingAdapter(retryable=True, failures_before_success=1)
    executor = ToolExecutor(build_azusa_registry(adapter))

    result = executor.execute(
        ToolCall(
            call_id="retry-search",
            name="search_azusa",
            arguments={"query": "question"},
        )
    )

    assert result.ok
    assert result.attempts == 2
    assert adapter.search_calls == 2


def test_registry_stops_after_one_retry_when_adapter_keeps_failing() -> None:
    adapter = _RetryingAdapter(retryable=True, failures_before_success=99)
    executor = ToolExecutor(build_azusa_registry(adapter))

    result = executor.execute(
        ToolCall(
            call_id="retry-exhausted",
            name="search_azusa",
            arguments={"query": "question"},
        )
    )

    assert not result.ok
    assert result.attempts == 2
    assert adapter.search_calls == 2
    assert result.error is not None
    assert result.error.code == ToolErrorCode.EXECUTION_FAILED
    assert result.error.retryable


def test_registry_does_not_retry_non_retryable_chunk_failure() -> None:
    adapter = _RetryingAdapter(retryable=False, failures_before_success=99)
    executor = ToolExecutor(build_azusa_registry(adapter))

    result = executor.execute(
        ToolCall(
            call_id="no-retry-chunk",
            name="get_chunk_by_id",
            arguments={"chunk_id": "missing"},
        )
    )

    assert not result.ok
    assert result.attempts == 1
    assert adapter.chunk_calls == 1
    assert result.error is not None
    assert result.error.code == ToolErrorCode.EXECUTION_FAILED
    assert not result.error.retryable


def test_registry_retries_retryable_chunk_failure_once() -> None:
    """get_chunk 工具也必须原样传播可重试属性，不能只修搜索工具。"""

    adapter = _RetryingAdapter(retryable=True, failures_before_success=99)
    executor = ToolExecutor(build_azusa_registry(adapter))

    result = executor.execute(
        ToolCall(
            call_id="retry-chunk",
            name="get_chunk_by_id",
            arguments={"chunk_id": "missing"},
        )
    )

    assert not result.ok
    assert result.attempts == 2
    assert adapter.chunk_calls == 2
    assert result.error is not None
    assert result.error.retryable


def test_registry_rejects_invalid_arguments_before_calling_adapter() -> None:
    adapter = _RetryingAdapter(retryable=True, failures_before_success=0)
    executor = ToolExecutor(build_azusa_registry(adapter))

    result = executor.execute(
        ToolCall(
            call_id="invalid-search",
            name="search_azusa",
            arguments={"query": "x", "k": 0, "mode": ""},
        )
    )

    assert not result.ok
    assert result.attempts == 0
    assert adapter.search_calls == 0
    assert result.error is not None
    assert result.error.code == ToolErrorCode.INVALID_ARGUMENTS
