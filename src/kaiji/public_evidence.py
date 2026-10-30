"""生成并复核自有合成证据包；与历史真实模型产物分开，不需要凭据或网络。"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from kaiji.demo import ScriptedDecisionProvider, build_registry
from kaiji.eval_runner import AgentEvalCase, AgentEvalRunner
from kaiji.eval_scorer import score_episode
from kaiji.experiment import load_agent_cases
from kaiji.faults import ScriptedToolHandler, ToolFaultStep
from kaiji.formal_runner import FormalRunConfig, FormalRunReport, ResumableFormalRunner, rescore_formal_run, _fingerprint
from kaiji.models import ModelAction, ToolCall
from kaiji.tools import ToolExecutor, ToolRegistry


def sample_cases() -> list[AgentEvalCase]:
    """声明并冻结三个自有折扣样例：成功、等价数值但严格参数失败、工具超时。"""
    return [AgentEvalCase(
        case_id=case_id, semantic_group="public-discount", suite="reliability",
        prompt="原价 120，减免 25%，请计算最终价格。", scenario=scenario,
        expected_stop_reason="completed", expected_tool="calculate_discount",
        expected_arguments={"price": "120", "percent": "25"}, required_answer_facts=["90"],
    ) for case_id, scenario in (("public-success", "success"), ("public-arguments", "arguments"), ("public-timeout", "timeout"))]


def provider(case: AgentEvalCase) -> ScriptedDecisionProvider:
    """按显式故障场景生成动作；参数失败故意使用字符串 120.0，不把它冒充金标。"""
    price = "120.0" if case.scenario == "arguments" else "120"
    return ScriptedDecisionProvider([
        ModelAction(kind="tool_call", tool_call=ToolCall(
            call_id=f"{case.case_id}-discount", name="calculate_discount", arguments={"price": price, "percent": "25"},
        )), ModelAction(kind="final_answer", final_answer="最终价格为 90。"),
    ])


def executor(case: AgentEvalCase) -> ToolExecutor:
    """成功使用演示工具，超时使用受控只读 delay；保留真实线程超时和一次重试。"""
    registry = build_registry()
    if case.scenario == "timeout":
        definition = registry.get("calculate_discount")
        registry = ToolRegistry()
        registry.register(replace(
            definition, handler=ScriptedToolHandler([ToolFaultStep(kind="delay", delay_seconds=0.03) for _ in range(2)]),
            timeout_seconds=0.001, max_retries=1,
        ))
    return ToolExecutor(registry)


class RecordingRunner:
    """复用现有评测 Runner，额外记录本进程真正执行的 case 坐标以验证恢复跳过。"""

    def __init__(self) -> None:
        """每次重启创建独立记录与 Provider/Executor 工厂。"""
        self.delegate = AgentEvalRunner(provider, executor)
        self.executed: list[tuple[str, int]] = []

    def run(self, case: AgentEvalCase, *, repeat_index: int = 0):
        """保留标准 Episode 原样返回，同时记录本次实际执行坐标。"""
        self.executed.append((case.case_id, repeat_index))
        return self.delegate.run(case, repeat_index=repeat_index)


def hashes(root: Path) -> dict[str, str]:
    """记录包内所有普通文件，不包含自引用的 kit.json；不扫描外部历史目录。"""
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file() and path != root/"kit.json"}


def validate_outcomes(report: FormalRunReport, scores: dict, episodes: dict) -> None:
    """校验固定三条样例的报告、逐条分数及原始 Episode；任一不符即抛 ValueError。

    参数样例必须只落到 arguments 失败，超时必须记录两次尝试且没有成功证据；
    完整矩阵还要求零真实请求。输入字典按冻结 case_id 索引，不接受任意题集替代
    这组契约，也不能从这三个受控样例外推模型质量。
    """
    if not report.complete or report.actual_episode_count != 3 or report.requests_started != 0:
        raise ValueError("公开样例矩阵不完整或产生模型请求")
    if not scores["public-success"].task_success:
        raise ValueError("成功样例未通过")
    arguments = scores["public-arguments"]
    if arguments.task_success or arguments.arguments_correct or arguments.failure_categories != ["arguments"]:
        raise ValueError("严格参数样例没有保留单独的参数失败")
    timeout = episodes["public-timeout"].result
    failed_tools = [event for event in timeout.trace if event.event == "tool_result"]
    if timeout.stop_reason.value != "tool_failed" or timeout.successful_tool_results or scores["public-timeout"].task_success:
        raise ValueError("超时未保持失败隔离")
    if len(failed_tools) != 1 or failed_tools[0].data.get("error", {}).get("code") != "timeout" or failed_tools[0].data.get("attempts") != 2:
        raise ValueError("超时样例未记录两次有界尝试")


def read_episodes(run_dir: Path) -> dict:
    """按 case_id 返回单次重复样例的 Episode，保留模型、消息、工具与 Trace。

    调用方须先生成或由 Formal 校验矩阵与 hash 链；本辅助函数只解析记录模型，
    不独立证明链完整性。此公开包固定 repeats=1，不能用于按 case_id 合并多轮实验。
    """
    from kaiji.formal_runner import FormalEpisodeRecord

    records = [FormalEpisodeRecord.model_validate_json(line) for line in (run_dir/"episodes.jsonl").read_text(encoding="utf-8").splitlines()]
    return {record.case_id: record.episode for record in records}


def generate_kit(output: Path) -> None:
    """向不存在的新目录生成三条自有样例，先保存单条 partial，再恢复完成矩阵。

    题集在执行前落盘；工具与模型均为受控替身，不读取历史真实实验。生成后校验
    逐条判定和恢复跳过行为，记录原始文件及生成源码的散列。输出已存在时拒绝；
    中途失败可留下排查文件，但不会写入作为成功包入口的 kit.json。
    """
    output.mkdir(parents=True, exist_ok=False)
    cases = sample_cases()
    (output/"cases.jsonl").write_text("\n".join(case.model_dump_json() for case in cases)+"\n", encoding="utf-8", newline="\n")
    config = FormalRunConfig(
        suite="reliability", provider="scripted", model="scripted", repeats=1,
        base_url="http://127.0.0.1/unused-no-network", request_limit=1, cost_limit_usd=1,
        input_cost_per_million=0, output_cost_per_million=0,
        system_prompt_sha256=_fingerprint("public-evidence-script-v1"), tool_schema_sha256=_fingerprint(build_registry().schemas()),
    )
    runner = RecordingRunner()
    def stop_after_one():
        """完成第一坐标后主动暂停，使 partial 不含正式聚合。"""
        return "intentional_pause" if runner.executed else None
    session = ResumableFormalRunner(config, cases, runner, output/"complete", stop_check=stop_after_one)
    partial = session.run()
    if partial.complete or partial.aggregate is not None or partial.actual_episode_count != 1:
        raise ValueError("没有得到单条 partial 报告")
    shutil.copytree(output/"complete", output/"partial")
    resumed = RecordingRunner()
    complete = ResumableFormalRunner(config, cases, resumed, output/"complete", resume=True).run()
    if resumed.executed != [(case.case_id, 0) for case in cases[1:]]:
        raise ValueError("恢复没有跳过已完成坐标")
    episodes = read_episodes(output/"complete")
    scores = {case.case_id: score_episode(case, episodes[case.case_id]) for case in cases}
    validate_outcomes(complete, scores, episodes)
    root = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    relevant = [Path(__file__), root/"src/kaiji/eval_scorer.py", root/"src/kaiji/formal_runner.py", root/"src/kaiji/tools.py"]
    metadata = {"kind": "self-authored-scripted-contract-samples", "license": "CC0-1.0",
                "real_model_requests": 0, "base_commit": commit,
                "generation_sources_sha256": {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in relevant},
                "files_sha256": hashes(output)}
    (output/"kit.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def replay_kit(bundle: Path, output: Path) -> dict:
    """校验给定包的字节摘要并用当前 Scorer 重算，在新 output 目录续跑副本。

    完整包重评分不持久化；只有 partial 副本会被追加，须保留已完成首条的 run_id，
    并只执行其余两条。返回逐条分数、来源散列和恢复观测，同时写 replay.json。
    原包损坏时在创建输出前失败；续跑中异常可保留输出用于排查，不覆盖原件。
    包摘要用于损坏检测，不是签名或来源认证；默认复核不重新生成冻结的原始样例。
    """
    metadata = json.loads((bundle/"kit.json").read_text(encoding="utf-8"))
    before = hashes(bundle)
    if before != metadata["files_sha256"]:
        raise ValueError("证据包散列不一致")
    cases = load_agent_cases(bundle/"cases.jsonl")
    # 包的清单只用于字节比较，不用其中的路径访问外部文件；评分固定在两个子目录。
    report = rescore_formal_run(bundle/"complete", cases, persist=False)
    episodes = read_episodes(bundle/"complete")
    scores = {case.case_id: score_episode(case, episodes[case.case_id]) for case in cases}
    validate_outcomes(report, scores, episodes)
    partial = FormalRunReport.model_validate_json((bundle/"partial/report.json").read_text(encoding="utf-8"))
    if partial.complete or partial.aggregate is not None or partial.actual_episode_count != 1:
        raise ValueError("partial 被错误发布为完整成绩")
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(bundle/"partial", output/"resumed")
    config = FormalRunConfig.model_validate_json(json.dumps(json.loads((bundle/"partial/manifest.json").read_text(encoding="utf-8"))["config"]))
    runner = RecordingRunner()
    resumed = ResumableFormalRunner(config, cases, runner, output/"resumed", resume=True).run()
    if runner.executed != [(case.case_id, 0) for case in cases[1:]] or not resumed.complete:
        raise ValueError("partial 恢复未跳过已有坐标")
    original_first = read_episodes(bundle/"partial")[cases[0].case_id].run_id
    if read_episodes(output/"resumed")[cases[0].case_id].run_id != original_first:
        raise ValueError("恢复替换了已完成记录")
    resumed_episodes = read_episodes(output/"resumed")
    validate_outcomes(resumed, {case.case_id: score_episode(case, resumed_episodes[case.case_id]) for case in cases}, resumed_episodes)
    if hashes(bundle) != before:
        raise ValueError("复核修改了原始证据包")
    result = {"kind": metadata["kind"], "real_model_requests": 0, "source_unchanged": True,
              "replay_sources_sha256": {
                  name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                  for name in ("public_evidence.py", "eval_scorer.py", "formal_runner.py", "tools.py")
              },
              "partial_count": 1, "resumed_executed": runner.executed,
              "report": report.model_dump(mode="json"),
              "scores": {key: value.model_dump(mode="json") for key,value in scores.items()}}
    (output/"replay.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    """默认离线复核仓库样例；生成操作必须显式指定新的输出目录。"""
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", type=Path)
    parser.add_argument("--bundle", type=Path, default=root/"examples/public_evidence_v1")
    args = parser.parse_args()
    if args.generate is not None:
        generate_kit(args.generate)
        print(json.dumps({"generated": str(args.generate)}))
    else:
        target = root/"artifacts/public-replay"/uuid4().hex
        replay_kit(args.bundle, target)
        print(json.dumps({"status": "passed", "report": str(target/"replay.json")}))


if __name__ == "__main__":
    main()
