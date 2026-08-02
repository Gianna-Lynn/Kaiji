"""在显式启用时验证 Kaiji 与真实 Azusa BM25 网关的跨仓库闭环。"""

import os

import pytest

from kaiji.adapters.azusa import AzusaHttpAdapter


GATEWAY_URL = os.getenv("AZUSA_GATEWAY_URL")

pytestmark = pytest.mark.skipif(
    not GATEWAY_URL,
    reason=(
        "TODO 12 未实际执行：请设置 AZUSA_GATEWAY_URL；"
        "验收时必须看到本测试 passed，skipped 不算完成"
    ),
)


def test_todo12_live_azusa_search_then_get_same_chunk() -> None:
    """实际运行时验证检索命中与按 ID 回读的证据正文完全一致。"""

    assert GATEWAY_URL

    # 第一次真实请求可能导入本地嵌入和重排依赖；即使 BM25 本身只使用
    # CPU，真实联调测试仍需要为冷启动保留足够时间。
    adapter = AzusaHttpAdapter(
        GATEWAY_URL,
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
