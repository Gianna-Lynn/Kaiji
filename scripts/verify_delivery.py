"""在当前工作树离线核验交付小闭环，记录来源；不调用模型或改写历史实验。"""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import io
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def file_hashes(root: Path, paths: list[Path]) -> dict[str, str]:
    """对显式选择的文件计算 SHA256；文件缺失直接失败，不读取凭据或缓存。"""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


def verify_sqlite(target: Path) -> dict[str, object]:
    """用两个独立连接验证审批暂停和恢复；只模拟批准，不执行外部副作用。"""
    from kaiji.adapters.azusa import MockAzusaAdapter
    from kaiji.checkpointing import sqlite_checkpointer
    from kaiji.langgraph_workflow import build_agent_graph, resume_graph, run_graph

    database = target / "checkpoint.sqlite"
    thread_id = "delivery-approval"
    with sqlite_checkpointer(database) as saver:
        graph = build_agent_graph(MockAzusaAdapter(), checkpointer=saver)
        paused = run_graph(graph, "publish the report", thread_id=thread_id)
        if paused["approved"] is not None or not paused["pending_action"]:
            raise RuntimeError("审批路径没有暂停")
    with sqlite_checkpointer(database) as saver:
        graph = build_agent_graph(MockAzusaAdapter(), checkpointer=saver)
        restored = graph.get_state({"configurable": {"thread_id": thread_id}}).values
        if restored["state_schema_version"] != 1 or restored["approved"] is not None:
            raise RuntimeError("新连接没有恢复预期版本的待审批状态")
        result = resume_graph(graph, thread_id=thread_id, approved=True)
    if result["stop_reason"] != "completed" or result["approved"] is not True:
        raise RuntimeError("审批恢复未完成")
    return {"fresh_connection": True, "stop_reason": result["stop_reason"]}


def verify_history(root: Path, run_dir: Path) -> dict[str, object]:
    """只读重算完整历史矩阵并比较目录全部文件散列；不发送请求或迁移报告。"""
    from kaiji.formal_runner import load_formal_behavior_cases, rescore_formal_run

    paths = [path for path in run_dir.rglob("*") if path.is_file()]
    before = file_hashes(run_dir, paths)
    report = rescore_formal_run(run_dir, load_formal_behavior_cases(root), persist=False)
    after = file_hashes(run_dir, [path for path in run_dir.rglob("*") if path.is_file()])
    if before != after:
        raise RuntimeError("历史目录出现预期外变化，停止验证")
    return {"path": str(run_dir), "files_sha256": before,
            "unchanged": True, "report": report.model_dump(mode="json")}


def main() -> None:
    """固定导入当前源码，验证 Demo、SQLite 和离线矩阵，向唯一新目录写入报告。

    可选历史路径只用于只读重评分。任一异常直接返回非零退出码，不生成成功报告；
    已产生的新目录保留供排查。该入口不代替完整 pytest 或真实 HTTP/模型验证。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-run", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    import kaiji
    from kaiji.demo import main as demo
    from kaiji.formal_runner import run_formal_preflight

    imported = Path(kaiji.__file__).resolve()
    if not imported.is_relative_to(root / "src"):
        raise RuntimeError("导入来源与当前工作树不一致")
    target = root / "artifacts" / "delivery" / uuid4().hex
    target.mkdir(parents=True, exist_ok=False)
    paths = list((root / "src").rglob("*.py")) + list((root / "eval").glob("*.jsonl"))
    paths += [root / "pyproject.toml", Path(__file__).resolve()]
    hashes = file_hashes(root, paths)
    # safe.directory 只对本次只读 Git 命令生效，不写入用户级配置。
    commit = subprocess.check_output(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root),
         "rev-parse", "HEAD"], text=True,
    ).strip()
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        demo()
    native = json.loads(captured.getvalue())
    if native["stop_reason"] != "completed" or native["answer"] != "The final price is 90.":
        raise RuntimeError("Native Demo 未得到预期结果")
    sqlite = verify_sqlite(target)
    preflight = run_formal_preflight(root, target / "formal-preflight")
    for report, expected in ((preflight.real_behavior, 120), (preflight.reliability, 60)):
        if not report.complete or report.actual_episode_count != expected or report.requests_started != 0:
            raise RuntimeError("离线 Preflight 矩阵或零请求边界不符合预期")
    result = {"status": "passed", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "source_root": str(root), "imported": str(imported),
              "base_commit": commit, "python": sys.version, "files_sha256": hashes,
              "dependencies": {name: version(name) for name in (
                  "pydantic", "httpx", "langgraph", "langgraph-checkpoint-sqlite", "pytest",
              )},
              "native_demo": native, "sqlite": sqlite,
              "preflight": preflight.model_dump(mode="json")}
    if args.historical_run is not None:
        result["historical_rescore"] = verify_history(root, args.historical_run.resolve())
    if hashes != file_hashes(root, paths):
        raise RuntimeError("核验期间源码发生变化，结果不可归属于单一版本")
    output = target / "verification.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"status": "passed", "report": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
