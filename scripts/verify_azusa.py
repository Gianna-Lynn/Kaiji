"""启动独立 Azusa 网关进程，经 HTTP 核验问答和失败路径，记录双方版本与源码。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

from verify_delivery import file_hashes


def provenance(root: Path, paths: list[Path]) -> dict:
    """返回仓库路径、HEAD 与显式文件列表的 SHA256；文件或 Git 读取失败直接传播。

    base_commit 仅标识基底，工作区可有补丁，实际受测文件以散列为准。此处不声明
    工作区干净，也不改写 safe.directory 等用户级配置。
    """
    commit = subprocess.check_output(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), "rev-parse", "HEAD"], text=True,
    ).strip()
    return {"root": str(root), "base_commit": commit, "files_sha256": file_hashes(root, paths)}


def wait_gateway(process: subprocess.Popen, log: Path) -> str:
    """在有界等待内读取端口 0 分配的实际地址；进程退出或 120 秒未就绪即失败。"""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        match = re.search(r"http://127\.0\.0\.1:(\d+)", log.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(0)
        if process.poll() is not None:
            raise RuntimeError(f"网关启动失败，见 {log}")
        time.sleep(0.1)
    raise TimeoutError(f"网关启动超时，见 {log}")


def main() -> None:
    """从显式 Azusa 根目录启动本机网关，校验规则问答及未知 chunk 的失败隔离。

    端口由系统分配，启动等待有界；退出时只清理本次创建的子进程。输出写入唯一
    新目录，保留完整 Trace 和双方源码身份；断言或启动失败直接非零退出，不产生
    成功报告。当前入口只验证自有 fixture 的真实 HTTP 路径，不评估模型自主行为。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--azusa-root", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    azusa = args.azusa_root.resolve()
    gateway = azusa/"rag/run_kaiji_gateway.py"
    if not gateway.is_file():
        parser.error("Azusa 目录缺少网关入口")
    sys.path.insert(0, str(root/"src"))
    import kaiji
    from kaiji.adapters.azusa import AzusaHttpAdapter
    from kaiji.knowledge_demo import run_knowledge_demo

    if not Path(kaiji.__file__).resolve().is_relative_to(root/"src"):
        raise RuntimeError("Kaiji 导入来源错误")
    target = root/"artifacts/integration"/uuid4().hex
    target.mkdir(parents=True, exist_ok=False)
    fixture = root/"examples/knowledge_notes.jsonl"
    sources = {
        "kaiji": provenance(root, list((root/"src").rglob("*.py")) + [Path(__file__), root/"scripts/verify_delivery.py"]),
        "azusa": provenance(azusa, list((azusa/"rag/src").rglob("*.py")) + [gateway]),
    }
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with (target/"gateway.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-B", "-s", str(gateway), "--host", "127.0.0.1", "--port", "0", "--embedding-path", str(fixture)],
            cwd=azusa/"rag", stdout=log, stderr=subprocess.STDOUT, env=env,
        )
        try:
            url = wait_gateway(process, target/"gateway.log")
            adapter = AzusaHttpAdapter(url, timeout_seconds=120)
            try:
                success, failure = run_knowledge_demo(adapter, "How does Kaiji resume a workflow with SQLite?")
            finally:
                adapter.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    if success.stop_reason.value != "completed" or len(success.successful_tool_results) != 2:
        raise RuntimeError("成功路径没有完成 search/get 闭环")
    if "fresh Saver" not in success.answer or "Citations: kaiji-sqlite-recovery" not in success.answer:
        raise RuntimeError("公开项目笔记的回答或引用不符合预期")
    if failure.stop_reason.value != "tool_failed" or failure.successful_tool_results:
        raise RuntimeError("未知 chunk 没有保持失败证据隔离")
    for source in sources.values():
        source_root = Path(source["root"])
        if file_hashes(source_root, [source_root/name for name in source["files_sha256"]]) != source["files_sha256"]:
            raise RuntimeError("运行期间源码变化")
    result = {"kind": "rule-driven-real-http-integration", "real_model_requests": 0,
              "sources": sources, "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
              "success": success.model_dump(mode="json"), "failure": failure.model_dump(mode="json")}
    output = target/"integration.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "report": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
