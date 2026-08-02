"""加载固定评测集并输出聚合器合成金标报告，不执行真实 Agent。"""

from pathlib import Path

from kaiji.evaluation import (
    load_cases,
    reference_observation,
    report_to_json,
    run_eval,
)


def main() -> None:
    """用确定性合成观测演示指标管线，不把结果冒充 Agent 成绩。"""

    root = Path(__file__).resolve().parents[2]
    cases = load_cases(root / "eval" / "cases.jsonl")
    print(report_to_json(run_eval(cases, reference_observation)))


if __name__ == "__main__":
    main()
