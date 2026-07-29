import os

import pytest

from kaiji.adapters.azusa import AzusaHttpAdapter


GATEWAY_URL = os.getenv("AZUSA_GATEWAY_URL")

pytestmark = pytest.mark.skipif(
    not GATEWAY_URL,
    reason="set AZUSA_GATEWAY_URL to run the live Azusa integration test",
)


def test_live_azusa_search_then_get_same_chunk() -> None:
    # The first real request may import local embedding/rerank dependencies even
    # though bm25 itself is CPU-only, so the live test allows cold-start time.
    adapter = AzusaHttpAdapter(
        GATEWAY_URL or "http://127.0.0.1:3010",
        timeout_seconds=120,
    )
    try:
        search = adapter.search_azusa(
            "When were stromules discovered?",
            3,
            "bm25",
        )

        assert search.mode == "bm25"
        assert search.hits
        assert "1962" in search.hits[0].text

        chunk = adapter.get_chunk_by_id(search.hits[0].chunk_id)
        assert chunk.chunk_id == search.hits[0].chunk_id
        assert chunk.text == search.hits[0].text
    finally:
        adapter.close()
