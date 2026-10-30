"""验证公开合成包的只读重评分、恢复和损坏拒绝，不依赖模型或网关。"""

from pathlib import Path
import shutil

import pytest

from kaiji.public_evidence import hashes, replay_kit


def test_public_kit_replays_without_replacing_completed_record(tmp_path):
    """重算保留严格参数失败与超时，续跑只执行后两个坐标，原包逐字节不变。"""
    bundle = Path(__file__).resolve().parents[1]/"examples/public_evidence_v1"
    before = hashes(bundle)
    result = replay_kit(bundle, tmp_path/"replay")
    assert result["source_unchanged"] is True
    assert result["real_model_requests"] == 0
    assert result["resumed_executed"] == [("public-arguments", 0), ("public-timeout", 0)]
    assert result["scores"]["public-success"]["task_success"] is True
    assert result["scores"]["public-arguments"]["failure_categories"] == ["arguments"]
    assert result["scores"]["public-timeout"]["task_success"] is False
    assert hashes(bundle) == before


def test_modified_kit_fails_before_creating_resume_output(tmp_path):
    """题集被改写不能继续绑定原始成绩或产生恢复输出。"""
    original = Path(__file__).resolve().parents[1]/"examples/public_evidence_v1"
    bundle = tmp_path/"modified"
    shutil.copytree(original, bundle)
    with (bundle/"cases.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{}\n")
    with pytest.raises(ValueError, match="散列"):
        replay_kit(bundle, tmp_path/"replay")
    assert not (tmp_path/"replay").exists()
