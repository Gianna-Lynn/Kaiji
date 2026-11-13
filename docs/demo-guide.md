# Kaiji 展示运行指南

本指南适用于 main 的交付范围；develop 同源能力也可按相同方式验证。solution 还有额外服务与聊天能力，不能据此扩大 main 范围。

## 新读者：独立环境与源码安装

从 README 克隆 main 后，在仓库根目录创建一个新的虚拟环境。运行入口需要访问仓库里的题集、脚本和公开样例，因此使用该独立环境的 editable 安装。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m kaiji.demo
.\.venv\Scripts\python.exe -m kaiji.course_demo
.\.venv\Scripts\python.exe -m kaiji.eval_demo
.\.venv\Scripts\python.exe -B scripts/verify_delivery.py
.\.venv\Scripts\python.exe -B -m kaiji.public_evidence
```

Linux/macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m kaiji.demo
.venv/bin/python -m kaiji.course_demo
.venv/bin/python -m kaiji.eval_demo
.venv/bin/python -B scripts/verify_delivery.py
.venv/bin/python -B -m kaiji.public_evidence
```

这些离线入口不需要 API Key、GPU 或正在运行的 Azusa。当前 wheel 不包含仓库外层的 `eval/` 与 `examples/`；把 wheel 复制到任意目录后运行全部实验入口不在本次支持范围内。核心模块可被安装，完整展示按源码仓库复现。

Pydantic 最低版本为 2.12：Executor 使用该版本新增的 `model_validate(..., extra=...)` 参数。依赖允许范围不是每个版本组合都已测试的承诺。

## 维护者：共享 ChuoEnv 的来源隔离

多工作区共用 ChuoEnv 时，禁止用重新 editable install 切换源码。下面只设置当前进程环境，结束时恢复原值；每条 Python 命令使用同一解释器和搜索路径。

```powershell
$py = 'D:\Chuo_Data\conda_envs\ChuoEnv\python.exe'
$hadPythonPath = Test-Path Env:PYTHONPATH
$previousPythonPath = $env:PYTHONPATH
$testRoot = '.cache/codex-pytest-' + [guid]::NewGuid().ToString('N')
try {
    $sourceRoot = (Resolve-Path src).Path
    $env:PYTHONPATH = $sourceRoot
    if ($hadPythonPath -and $previousPythonPath) {
        $env:PYTHONPATH += [IO.Path]::PathSeparator + $previousPythonPath
    }
    & $py -B -c 'import pathlib,kaiji; p=pathlib.Path(kaiji.__file__).resolve(); print(p); assert p.is_relative_to((pathlib.Path.cwd()/"src").resolve())'
    if ($LASTEXITCODE) { throw '源码来源检查失败' }
    & $py -B -m pytest tests --ignore=tests/test_azusa_live_integration.py -o addopts= -q --basetemp=$testRoot -o "cache_dir=$testRoot/cache"
    if ($LASTEXITCODE) { throw '离线回归失败' }
    & $py -B -m kaiji.demo
    if ($LASTEXITCODE) { throw '原生 Demo 失败' }
    & $py -B -m kaiji.course_demo
    if ($LASTEXITCODE) { throw '框架对照 Demo 失败' }
    & $py -B -m kaiji.eval_demo
    if ($LASTEXITCODE) { throw 'Eval Demo 失败' }
    & $py -B scripts/verify_delivery.py
    if ($LASTEXITCODE) { throw '交付核验失败' }
    & $py -B -m kaiji.public_evidence
    if ($LASTEXITCODE) { throw '公开包复核失败' }
} finally {
    if ($hadPythonPath) { $env:PYTHONPATH = $previousPythonPath }
    else { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
}
```

普通 `python -m` 必须核对来源，不能由 pytest 的 pythonpath 配置推断其来源。上面的维护者路径仅为本机示例，新读者使用前面的独立虚拟环境。

## 本机 Azusa HTTP

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_azusa.py --azusa-root ../Azusa
```

此例假设 Azusa 克隆在同级目录；其他位置请替换 --azusa-root。Linux/macOS 使用 .venv/bin/python，共享 ChuoEnv 使用来源已核对的解释器。

脚本只启动本次独立网关，以系统分配端口监听回环地址，结束时清理本次子进程；不切换 Azusa 分支。报告必须记录实际 Azusa HEAD，使用 develop 验证不能写成 main 已联调。

若本机代理导致回环请求返回 502，可仅在运行脚本的独立进程内，将 `127.0.0.1,localhost,::1` 追加到 `NO_PROXY` 与 `no_proxy`；不得改写用户或系统代理设置。PowerShell 若临时设置变量，必须记录原先是否存在及原值，并在 finally 恢复。

此路径使用项目笔记与规则决策。TODO12 的原始 fixture 必须另行启动对应网关，显式运行 `tests/test_azusa_live_integration.py` 且显示 passed；skipped 不算验收。完整步骤见课程文档。

## 预期结果

- 折扣：90、completed、2 步、1 次工具执行；最终答案位于 RunResult.answer，不追加最终 assistant 消息。
- SQLite：新 Saver/Graph 和相同 thread_id 恢复待审批状态，批准后 completed。
- Formal Preflight：120 与 60 条独立矩阵完成，真实请求数为 0；故障组中的伪引用应被评分器识别。
- 公开包：完整矩阵复核成功；从 1/3 恢复只执行剩余两条；原包字节不变。
- HTTP：成功路径恰有 search/get 两条成功结果；未知 chunk 失败结果不得进入成功证据。
