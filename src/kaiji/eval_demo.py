"""加载固定评测集并输出参考评测指标报告。"""

from pathlib import Path

from kaiji.evaluation import (
    load_cases,
    reference_observation,
    report_to_json,
    run_eval,
)


def main() -> None:
    """加载仓库中的固定用例并打印参考指标。"""

    root = Path(__file__).resolve().parents[2]
    cases = load_cases(root / "eval" / "cases.jsonl")
    print(report_to_json(run_eval(cases, reference_observation)))


if __name__ == "__main__":
    main()
