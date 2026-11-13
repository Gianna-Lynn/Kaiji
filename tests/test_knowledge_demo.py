"""验证规则问答使用实际返回 ID，并在回读正文不一致时拒绝生成回答。"""

from kaiji.adapters.azusa import ChunkResponse, MockAzusaAdapter
from kaiji.knowledge_demo import run_knowledge_demo


def test_knowledge_demo_uses_returned_evidence_and_isolates_unknown_chunk():
    """不预设金标 ID，工具返回的自定义 ID 应进入后续 get 和最终引用。"""
    adapter = MockAzusaAdapter([ChunkResponse(chunk_id="custom-evidence", text="SQLite resumes a workflow.")])
    success, failure = run_knowledge_demo(adapter, "SQLite workflow")
    assert success.stop_reason.value == "completed"
    assert success.answer == "SQLite resumes a workflow.\nCitations: custom-evidence"
    assert len(success.successful_tool_results) == 2
    assert failure.stop_reason.value == "tool_failed"
    assert failure.successful_tool_results == []


def test_knowledge_demo_rejects_search_get_text_disagreement():
    """结构合法但与检索命中不一致的回读不能被摘录为已完成回答。"""
    class DisagreeingAdapter(MockAzusaAdapter):
        """只改变合法回读正文，模拟跨代证据不一致。"""

        def get_chunk_by_id(self, chunk_id):
            """保留未知 chunk 的失败行为，只改写真实存在的证据。"""
            chunk = super().get_chunk_by_id(chunk_id)
            return chunk.model_copy(update={"text": "Different generation"})

    adapter = DisagreeingAdapter([ChunkResponse(chunk_id="custom-evidence", text="SQLite resumes a workflow.")])
    success, _ = run_knowledge_demo(adapter, "SQLite workflow")
    assert success.stop_reason.value == "model_failed"
    assert success.answer is None
