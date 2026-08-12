"""离线验证 Azusa Live Pilot 的固定 case 和系统指令。"""

from pathlib import Path

from kaiji.azusa_live_smoke import _system_prompt
from kaiji.experiment import load_agent_cases


def test_live_pilot_has_five_explicit_bm25_citation_cases() -> None:
    root = Path(__file__).resolve().parents[1]
    cases = load_agent_cases(root / "eval" / "azusa_live_cases.jsonl")

    assert len(cases) == 5
    assert all(case.expected_arguments["mode"] == "bm25" for case in cases)
    assert all(len(case.allowed_citation_ids) == 1 for case in cases)
    for case in cases:
        prompt = _system_prompt(case)
        assert case.allowed_citation_ids[0] in prompt
        assert case.expected_arguments["query"] in prompt
