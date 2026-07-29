from kaiji.adapters.azusa import MockAzusaAdapter, SearchResponse
from kaiji.langchain_compare import (
    RecordingCallback,
    build_search_runnable,
    build_search_tool,
)


def test_langchain_structured_tool_reuses_same_adapter_contract() -> None:
    tool = build_search_tool(MockAzusaAdapter())

    result = tool.invoke({"query": "Kaiji", "k": 1, "mode": "hybrid"})

    assert result["hits"][0]["chunk_id"] == "kaiji-001"
    assert tool.args_schema.model_json_schema()["required"] == ["query"]


def test_runnable_validates_structured_output_and_emits_callbacks() -> None:
    callback = RecordingCallback()
    runnable = build_search_runnable(MockAzusaAdapter())

    result = runnable.invoke(
        {"query": "Azusa", "k": 1, "mode": "hybrid"},
        config={"callbacks": [callback]},
    )

    assert isinstance(result, SearchResponse)
    assert result.hits[0].chunk_id == "azusa-001"
    assert callback.starts >= 1
    assert callback.ends >= 1
    assert callback.errors == 0
