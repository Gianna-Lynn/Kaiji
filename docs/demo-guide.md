# Kaiji 展示运行指南

本指南适用于 main 的交付范围；develop 同源能力也可按相同方式验证。solution 还有额外服务与聊天能力，不能据此扩大 main 范围。

## 固定源码并运行

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
    & $py -B -m kaiji.course_demo
    & $py -B -m kaiji.eval_demo
    & $py -B scripts/verify_delivery.py
    & $py -B -m kaiji.public_evidence
} finally {
    if ($hadPythonPath) { $env:PYTHONPATH = $previousPythonPath }
    else { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
}
```

独立新环境可按 README 安装本分支。普通 `python -m` 必须核对来源，不能由 pytest 的 pythonpath 配置推断其来源。

## 本机 Azusa HTTP

```powershell
python -B scripts/verify_azusa.py --azusa-root F:/Repositories/Azusa
```

脚本只启动本次独立网关，以系统分配端口监听回环地址，结束时清理本次子进程；不切换 Azusa 分支。报告必须记录实际 Azusa HEAD，使用 develop 验证不能写成 main 已联调。

若本机代理导致回环请求返回 502，可仅在运行脚本的独立进程内，将 `127.0.0.1,localhost,::1` 追加到 `NO_PROXY` 与 `no_proxy`；不得改写用户或系统代理设置。PowerShell 若临时设置变量，必须记录原先是否存在及原值，并在 finally 恢复。

此路径使用项目笔记与规则决策。TODO12 的原始 fixture 必须另行启动对应网关，显式运行 `tests/test_azusa_live_integration.py` 且显示 passed；skipped 不算验收。完整步骤见课程文档。

## 预期结果

- 折扣：90、completed、2 步、1 次工具执行；最终答案位于 RunResult.answer，不追加最终 assistant 消息。
- SQLite：新 Saver/Graph 和相同 thread_id 恢复待审批状态，批准后 completed。
- Formal Preflight：120 与 60 条独立矩阵完成，真实请求数为 0；故障组中的伪引用应被评分器识别。
- 公开包：完整矩阵复核成功；从 1/3 恢复只执行剩余两条；原包字节不变。
- HTTP：成功路径恰有 search/get 两条成功结果；未知 chunk 失败结果不得进入成功证据。
