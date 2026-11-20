"""运行 Kaiji 的离线或 DeepSeek 四组故障消融，并从原始记录生成报告。"""

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import kaiji
from kaiji.eval_runner import AgentEpisode, AgentEvalCase, AgentEvalRunner
from kaiji.eval_scorer import score_episode
from kaiji.experiment import _scripted_executor, build_scripted_runner, load_agent_cases
from kaiji.harnesses import HarnessVariant
from kaiji.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from kaiji.real_smoke import BASE_URL, BudgetedProvider, RequestBudget

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval" / "fault_cases.jsonl"
MODEL = "deepseek-flash"
MAX_TOKENS = 128


def system_prompt(case: AgentEvalCase) -> str:
    """明确故障触发条件，工具结果和最终答案由模型处理。"""
    args = json.dumps(case.expected_arguments, ensure_ascii=False)
    first = f"这是工具协议测试。第一轮只调用 {case.expected_tool}，参数严格用 {args}。"
    if case.scenario == "duplicate":
        return first + "之后每轮用相同参数再调用一次，每轮恰好一个工具，不输出最终回答。"
    if case.scenario == "max_steps":
        return first + "之后每轮仍只调用一次，price 不变，percent 比上一轮加 1，不输出最终回答。"
    if case.scenario == "invalid_arguments":
        return first + "刻意提交非法参数，不更正，不用文字替代调用。"
    if case.expected_tool == "search_azusa":
        return first + (
            "收到结果后停止调用并简短回答。只依据结果内容；如果有证据，"
            "保留关键原文并另起一行用 Citations: 列出返回的 chunk_id。没有证据就说明未找到。"
        )
    return first + "收到工具结果后停止调用，简短报告工具结果。"


def live_runner(variant: HarnessVariant, budget: RequestBudget) -> AgentEvalRunner:
    """组装真实 Provider，工具和故障序列复用离线实验。"""
    def executor_for(case):
        return _scripted_executor(case, variant)

    def provider_for(case):
        # Provider 只读取工具 schema，故障状态留在独立的执行器中。
        registry = executor_for(case)._registry
        config = OpenAICompatibleConfig(
            base_url=BASE_URL,
            model=MODEL,
            api_key_env="DEEPSEEK_API_KEY",
            thinking="disabled",
            max_tokens=MAX_TOKENS,
            timeout_seconds=15,
            temperature=0,
            system_prompt=system_prompt(case),
        )
        return BudgetedProvider(OpenAICompatibleProvider(config, registry), budget)

    return AgentEvalRunner(
        provider_for, executor_for, duplicate_guard=variant.duplicate_guard
    )


def validate_row(row, case, provider):
    """验证用例、模型身份和评分，返回可重新聚合的 Episode。"""
    episode = AgentEpisode.model_validate(row["episode"])
    if row["case"] != case.model_dump(mode="json") or (
        episode.case_id, episode.suite, episode.repeat_index
    ) != (case.case_id, case.suite, 0):
        raise ValueError(f"用例记录不匹配 {case.case_id}")
    if row["score"] != score_episode(case, episode).model_dump(mode="json"):
        raise ValueError(f"评分无法复算 {case.case_id}")
    if provider == "deepseek":
        if row["system_prompt"] != system_prompt(case) or (
            row["requests"] != len(episode.result.model_calls)
        ) or not all(call.metadata.model == MODEL for call in episode.result.model_calls):
            raise ValueError(f"DeepSeek 运行记录不匹配 {case.case_id}")
    elif row["requests"] != 0 or row["system_prompt"] is not None:
        raise ValueError(f"离线运行记录不匹配 {case.case_id}")
    return episode


def load_rows(path):
    """读取每行一条 Episode 的原始记录。"""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run_matrix(args, cases, raw_path):
    """运行所有缺失组合，逐条保存原始记录以便续跑。"""
    live = args.provider == "deepseek"
    recorded = load_rows(raw_path) if args.resume else []
    if args.reuse_full and (not live or recorded):
        raise ValueError("完整组导入仅用于新建 DeepSeek 实验")
    if raw_path.exists() and not args.resume:
        raise FileExistsError(raw_path)
    case_by_id = {case.case_id: case for case in cases}
    imported = load_rows(args.reuse_full) if args.reuse_full else []
    if imported:
        if sorted(row["case"]["case_id"] for row in imported) != sorted(case_by_id):
            raise ValueError("导入文件须覆盖全部用例各一次")
        for row in imported:
            episode = validate_row(row, case_by_id[row["case"]["case_id"]], args.provider)
            if any(call.failure is not None for call in episode.result.model_calls):
                raise ValueError("导入记录含有 Provider 失败")
            row.update(variant=HarnessVariant.FULL_KAIJI.value, origin="prior_full_run")
    if live and not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("未设置 DEEPSEEK_API_KEY")
    done = set()
    for row in [*recorded, *imported]:
        case_id = row["case"]["case_id"]
        key = HarnessVariant(row["variant"]), case_id
        if key in done:
            raise ValueError(f"重复记录 {key}")
        validate_row(row, case_by_id[case_id], args.provider)
        done.add(key)

    budget = RequestBudget(limit=args.request_limit)
    with raw_path.open("a" if args.resume else "x", encoding="utf-8") as stream:
        for row in imported:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.flush()
        for variant in HarnessVariant:
            runner = live_runner(variant, budget) if live else build_scripted_runner(variant)
            for case in cases:
                if (variant, case.case_id) in done:
                    continue
                before = budget.used
                episode = runner.run(case)
                if live and any(
                    call.failure is not None
                    and call.failure.code.value not in {"invalid_response", "invalid_json"}
                    for call in episode.result.model_calls
                ):
                    raise RuntimeError(f"{variant.value}/{case.case_id} 模型请求失败，已用 {budget.used} 次")
                score = score_episode(case, episode)
                row = {
                    "variant": variant.value,
                    "origin": "this_run",
                    "case": case.model_dump(mode="json"),
                    "system_prompt": system_prompt(case) if live else None,
                    "episode": episode.model_dump(mode="json"),
                    "score": score.model_dump(mode="json"),
                    "requests": budget.used - before,
                }
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(variant.value, case.case_id, episode.result.stop_reason.value,
                      score.failure_categories, budget.used, flush=True)


def build_report(provider, cases, raw_path):
    """核验 80 条记录并生成按配置和按用例的 Markdown 对照。"""
    case_by_id = {case.case_id: case for case in cases}
    matrix = {}
    for row in load_rows(raw_path):
        case_id = row["case"]["case_id"]
        key = HarnessVariant(row["variant"]), case_id
        if key in matrix:
            raise ValueError(f"重复记录 {key}")
        validate_row(row, case_by_id[case_id], provider)
        matrix[key] = row
    if len(matrix) != len(HarnessVariant) * len(cases):
        raise ValueError("消融矩阵未完成")
    live = provider == "deepseek"
    title = "DeepSeek API" if live else "离线 Scripted"
    dataset_link = Path(os.path.relpath(DATASET, raw_path.parent)).as_posix()
    lines = [
        f"# {title} 故障消融",
        "",
        "四组各运行同一批 20 条用例一次，保持工具、故障序列、用例步数上限及评分规则一致。"
        "每组只切换工具重试和重复调用保护。临时故障及模拟 Azusa 响应均在本地注入。",
        "",
        f"用例来源为 [fault_cases.jsonl]({dataset_link})，SHA-256 为 "
        f"`{hashlib.sha256(DATASET.read_bytes()).hexdigest()}`。",
        "",
    ]
    if live:
        lines += [
            f"模型为 `{MODEL}`，关闭思考模式，每次最多输出 {MAX_TOKENS} tokens。"
            "四组均使用相同的用例级系统提示，引导模型触发故障并自主生成工具动作和回答。",
            "",
        ]
    else:
        lines += ["Provider 按脚本返回工具动作和回答，API 请求数为 0。", ""]
    lines += [
        "## 四组结果",
        "",
        "| 配置 | 工具重试 | 重复调用保护 | 严格通过 | 重试恢复 | 平均步数 | API 请求 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for variant in HarnessVariant:
        group = [matrix[variant, case.case_id] for case in cases]
        scores = [row["score"] for row in group]
        passed = sum(score["task_success"] for score in scores)
        retry_scores = [row["score"] for row in group if row["case"]["expects_retry_recovery"]]
        recovered = sum(score["retry_recovered"] for score in retry_scores)
        steps = sum(score["steps"] for score in scores) / len(scores)
        requests = sum(row["requests"] for row in group)
        lines.append(
            f"| `{variant.value}` | {'开' if variant.retry_enabled else '关'} | "
            f"{'开' if variant.duplicate_guard else '关'} | {passed}/{len(scores)} "
            f"({passed / len(scores):.0%}) | {recovered}/{len(retry_scores)} | "
            f"{steps:.2f} | {requests} |"
        )
    if live:
        totals = Counter()
        for row in matrix.values():
            totals["requests"] += row["requests"]
            totals["input"] += row["score"]["input_tokens"]
            totals["output"] += row["score"]["output_tokens"]
            if row["origin"] == "this_run":
                totals["new_requests"] += row["requests"]
                totals["new_input"] += row["score"]["input_tokens"]
                totals["new_output"] += row["score"]["output_tokens"]
        total_usd = (totals["input"] * 0.3 + totals["output"] * 1.2) / 1_000_000
        new_usd = (totals["new_input"] * 0.3 + totals["new_output"] * 1.2) / 1_000_000
        lines += [
            "",
            f"整张矩阵使用 {totals['requests']} 次 API 请求，"
            f"{totals['input']:,} 输入和 {totals['output']:,} 输出 tokens，"
            f"按峰时无缓存价估算 ${total_usd:.6f}。"
            f"本次新增 {totals['new_requests']} 次请求，估算 ${new_usd:.6f}。"
            " [DeepSeek 官方定价](https://api-docs.deepseek.com/quick_start/pricing/)",
        ]
        if totals["requests"] != totals["new_requests"]:
            lines += ["", "完整组复用同日已运行的记录，逐条核对了原用例、系统提示、模型和评分。"]
    lines += ["", "## 机制影响", ""]
    for label, variant in (
        ("只开工具重试", HarnessVariant.NO_DUPLICATE_GUARD),
        ("只开重复调用保护", HarnessVariant.NO_RETRY),
    ):
        baseline = HarnessVariant.BOUNDED_BASELINE
        recovered = []
        regressed = []
        for case in cases:
            before = matrix[baseline, case.case_id]["score"]["task_success"]
            after = matrix[variant, case.case_id]["score"]["task_success"]
            if before != after:
                (recovered if after else regressed).append(f"`{case.case_id}`")
        lines.append(f"- {label}，较基线新增通过 {'、'.join(recovered) or '无'}。")
        if regressed:
            lines.append(f"  由通过变为失败的用例为 {'、'.join(regressed)}。")
    full_failures = [
        (case.case_id, matrix[HarnessVariant.FULL_KAIJI, case.case_id])
        for case in cases
        if not matrix[HarnessVariant.FULL_KAIJI, case.case_id]["score"]["task_success"]
    ]
    for case_id, row in full_failures:
        answer = row["episode"]["result"]["answer"]
        if answer:
            excerpt = " ".join(answer.split())
            lines.append(f"- 完整组的 `{case_id}` 未通过。实际回答为「{excerpt}」。")
    if any(row["case"]["scenario"] == "empty_evidence" for _, row in full_failures):
        lines += [
            "",
            "空证据用例的原评分要求答案包含「使用工具」，且至少给出一条有成功工具证据"
            "支持的引用。搜索返回空结果时，这项引用要求无法达成。表格保留原严格评分。",
        ]
    lines += [
        "",
        "## 逐条结果",
        "",
        "每格列出实际停止原因及严格评分。未通过项括号内列出扣分字段。",
        "",
        "| 用例 | 预期停止 | 基线 | 只开重复保护 | 只开重试 | 完整配置 |",
        "|---|---|---|---|---|---|",
    ]
    for case in cases:
        cells = []
        for variant in HarnessVariant:
            row = matrix[variant, case.case_id]
            stop = row["episode"]["result"]["stop_reason"]
            score = row["score"]
            verdict = "通过" if score["task_success"] else "（" + "、".join(score["failure_categories"]) + "）"
            cells.append(f"`{stop}` {verdict}")
        lines.append(f"| `{case.case_id}` | `{case.expected_stop_reason.value}` | "
                     + " | ".join(cells) + " |")
    lines += [
        "",
        "## 复现",
        "",
        f"在仓库根目录运行 `python eval/fault_ablation/run.py {provider} "
        f"--output-dir .cache/fault-ablation-repro-{provider}`。"
        + ("运行前在进程环境中配置 `DEEPSEEK_API_KEY`。" if live else ""),
        "",
        f"逐条原始记录见 [{raw_path.name}]({raw_path.name})，包含用例、模型动作、工具结果、"
        "停止原因、评分和 Token 计数。",
        "",
        f"在仓库根目录运行 `python eval/fault_ablation/run.py {provider} "
        "--output-dir eval/fault_ablation --report-only` 可从本目录原始记录重建报告。",
        "",
    ]
    raw_path.with_name(f"{provider}.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    """解析模式和预算，确认导入来源后执行或重建报告。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("scripted", "deepseek"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--request-limit", type=int, default=144)
    parser.add_argument("--reuse-full", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    if not Path(kaiji.__file__).resolve().is_relative_to(ROOT / "src" / "kaiji"):
        parser.error("kaiji 导入未指向当前仓库的 src/kaiji")
    cases = load_agent_cases(DATASET)
    if len(cases) != len({case.case_id for case in cases}) or len(cases) != 20:
        parser.error("故障题集应包含 20 条唯一用例")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_dir / f"{args.provider}.jsonl"
    if not args.report_only:
        run_matrix(args, cases, raw_path)
    build_report(args.provider, cases, raw_path)


if __name__ == "__main__":
    main()
