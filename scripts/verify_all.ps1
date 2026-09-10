# RAG4C 一键验收门禁 —— 把"改完怎么证明没坏"固化成一条命令。
#
# 用法（在任意目录下）：
#   scripts\verify_all.ps1                     # 全部门禁
#   scripts\verify_all.ps1 -SkipBackendSuite   # 跳过最慢的后端全量测试
#   scripts\verify_all.ps1 -Only backend-test  # 只跑某一项
#   scripts\verify_all.ps1 -List               # 列出所有门禁项
#
# 为什么要有这个脚本：
#   项目的验收命令散落在 README / 各轮优化文档里（ruff、pytest、eslint、tsc、vite build、
#   自己写的 vitest 批处理 runner），每次验收都要人肉拼命令、还要分别看退出码。
#   这里统一成一次性运行 + 一张表 + 一个退出码，让"验收"这一步有确定性的入口。
#
# 本文件必须以 **UTF-8 with BOM** 保存（Windows PowerShell 5.1 会按 ANSI 解码无 BOM 的
# UTF-8 中文注释，导致引号配对错乱并报一堆无关行号的语法错误）。详见 scripts/check.ps1 文件头。

[CmdletBinding()]
param(
    [string]$Only = '',
    [switch]$SkipBackendSuite,
    [switch]$SkipFrontend,
    [switch]$Parallel,
    [switch]$List
)

$ErrorActionPreference = 'Stop'

$ScriptFile = $PSCommandPath
if (-not $ScriptFile) { $ScriptFile = $MyInvocation.MyCommand.Path }
if ((-not $ScriptFile) -or (-not (Test-Path $ScriptFile))) {
    Write-Host '[FAIL] 无法定位脚本自身路径' -ForegroundColor Red
    exit 1
}
$Root = Split-Path -Parent (Split-Path -Parent $ScriptFile)
if (-not (Test-Path (Join-Path $Root 'pyproject.toml'))) {
    Write-Host "[FAIL] 仓库根定位失败: $Root" -ForegroundColor Red
    exit 1
}
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    Write-Host "[FAIL] 找不到虚拟环境解释器: $Python" -ForegroundColor Red
    exit 1
}
$Frontend = Join-Path $Root 'frontend'
$Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $Npm) { $Npm = (Get-Command npm -ErrorAction SilentlyContinue).Source }

# 后端全量测试默认【串行】。
#
# 为什么不用 -n auto 当默认：本仓库有一批**性能预算断言**（run_history_store /
# run_registry / run_ops_api / run_observability_parity / source_dispatch_runtime），
# 断言的是 p95 延迟、offer 热路径预算、1000 事件 / 100000 列表的墙钟上限。
# 并行跑时这些阈值会被同机其它 worker 抢 CPU 直接打穿 —— 实测 `-n auto` 会让
# 12 个用例转红、`-n 4` 会让 10 个转红，而**串行时同样这些用例是绿的**。
# 也就是说：并行 + 性能门禁 = 假红灯，会让"验收"这一步失去可信度。
# 需要缩短墙钟时显式加 -Parallel（并接受性能门禁可能抖动的代价）。
$hasXdist = $false
& $Python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('xdist') else 1)" 2>$null
if ($LASTEXITCODE -eq 0) { $hasXdist = $true }

$gates = [ordered]@{}
$gates['backend-lint'] = @{
    Desc = 'ruff check .（后端静态检查）'
    Run  = { & $Python -m ruff check . }
}
# 后端全量测试分两段跑：**功能断言并行 + 性能断言独占串行**。
#
# 为什么必须分两段：本仓库有一批断言 p95 延迟 / offer 热路径预算 / 1000 事件与
# 100000 列表墙钟上限的用例。它们对同机负载敏感，实测：
#   -n auto → 12 个转红；-n 4 → 10 个转红；串行且在空闲机器上 → 全绿。
# 也就是"并行跑全量"会让性能门禁变成**假红灯**。分两段之后：段 A 拿并行收益，
# 段 B 是唯一需要机器安静的部分（只要 ~2 分钟）。
$PerfBudgetFiles = @(
    'tests/test_run_history_store.py',
    'tests/test_run_observability_parity.py',
    'tests/test_run_ops_api.py',
    'tests/test_run_registry.py',
    'tests/test_source_dispatch_runtime.py'
)
$gates['backend-test'] = @{
    Desc = 'pytest 段A 功能断言(-n auto) + 段B 性能预算(串行独占)'
    Run  = {
        $ignore = @()
        foreach ($f in $PerfBudgetFiles) { $ignore += "--ignore=$f" }
        if ($Parallel -and $hasXdist) {
            Write-Host '  [段A] pytest tests -q -n auto（排除 5 个性能预算文件）'
            & $Python -m pytest tests -q @ignore -n auto
        } else {
            Write-Host '  [段A] pytest tests -q（串行；未加 -Parallel 或未装 xdist）'
            & $Python -m pytest tests -q @ignore
        }
        if ($LASTEXITCODE -ne 0) { throw '段A（功能断言）未通过' }
        Write-Host '  [段B] pytest 性能预算用例 -q（串行，须独占机器）'
        & $Python -m pytest @PerfBudgetFiles -q
        if ($LASTEXITCODE -ne 0) { throw '段B（性能预算）未通过——先确认机器上没有其它重负载任务' }
    }
}
$gates['frontend-lint'] = @{
    Desc = 'eslint（前端静态检查）'
    Run  = { Push-Location $Frontend; try { & $Npm run lint } finally { Pop-Location } }
}
$gates['frontend-build'] = @{
    Desc = 'tsc --noEmit + vite build（类型 + 生产构建）'
    Run  = { Push-Location $Frontend; try { & $Npm run build } finally { Pop-Location } }
}
$gates['frontend-test'] = @{
    Desc = 'vitest 全量（批处理 runner）'
    Run  = { Push-Location $Frontend; try { & $Npm test } finally { Pop-Location } }
}
$gates['server-health'] = @{
    Desc = '启动即健康（本地 SQLite catalog 起 uvicorn 并探 /api/health）'
    Run  = {
        # 为什么需要这一项：本仓库的 .env 把 catalog 指向远端 MySQL。远端不可达时
        # server/app.py 在【模块导入期】就要一个只读 catalog 引擎（:691），拿不到就
        # 整个进程起不来 —— 连 /api/health 都没有，等于"服务挂了但你查不到为什么"。
        # Milvus 走的是降级路线（health 里报 error / status=degraded），catalog 却硬失败。
        #
        # 这里不做"改产品语义"的事（catalog 不可达时禁止启动是 fail-closed 的正确行为，
        # 放开它等于把授权引擎变成可选 = 安全回退），而是把【本地可复现的启动路径】
        # 变成可验收的事实：显式指向本地 SQLite catalog，起服务，探活，收工。
        $healthDb = Join-Path $Root 'data\rag4c.db'
        $healthUrl = 'sqlite:///data/rag4c.db'
        $env:RAG4C_CATALOG_DB_URL = $healthUrl
        if (-not (Test-Path $healthDb)) {
            Write-Host "  本地 catalog 不存在，先迁移到 head：$healthDb"
            & $Python -m alembic upgrade head
            if ($LASTEXITCODE -ne 0) { throw 'alembic upgrade head 失败' }
        }
        $port = 8017
        $outLog = Join-Path $Root '.tmp\verify-server.out.log'
        $errLog = Join-Path $Root '.tmp\verify-server.err.log'
        New-Item -ItemType Directory -Force -Path (Join-Path $Root '.tmp') | Out-Null
        $proc = Start-Process -FilePath $Python `
            -ArgumentList '-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', "$port" `
            -WorkingDirectory $Root -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
            -PassThru -WindowStyle Hidden
        try {
            $deadline = (Get-Date).AddSeconds(90)
            $body = $null
            while ((Get-Date) -lt $deadline) {
                Start-Sleep -Seconds 3
                if ($proc.HasExited) { break }
                try {
                    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/health" -UseBasicParsing -TimeoutSec 20
                    if ($resp.StatusCode -eq 200) { $body = $resp.Content; break }
                } catch { }
            }
            if (-not $body) {
                Write-Host '  [FAIL] 90s 内未拿到 /api/health 200' -ForegroundColor Red
                if ($proc.HasExited) {
                    Write-Host "  进程已退出（exit=$($proc.ExitCode)）；stderr 末尾：" -ForegroundColor Red
                    Get-Content $errLog -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
                }
                throw "server-health 门禁失败"
            }
            $health = $body | ConvertFrom-Json
            Write-Host "  /api/health -> status=$($health.status) probed_at=$($health.probed_at)"
            if ($health.status -notin @('ok', 'degraded')) {
                Write-Host "  [FAIL] status 既不是 ok 也不是 degraded：$($health.status)" -ForegroundColor Red
                throw "server-health 门禁失败"
            }
            # degraded 是允许的（Milvus / 嵌入服务在别的机器上），但必须能说出是谁不健康。
            $bad = @($health.components.PSObject.Properties | Where-Object { $_.Value.status -notin @('ok', 'empty') })
            foreach ($c in $bad) { Write-Host "  降级组件: $($c.Name) -> $($c.Value.status)" }
            Write-Host '  启动即健康：通过'
        } finally {
            if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
        }
    }
}

if ($List) {
    foreach ($k in $gates.Keys) { Write-Host ("  {0,-16} {1}" -f $k, $gates[$k].Desc) }
    exit 0
}

$selected = @($gates.Keys)
if ($Only) {
    $selected = @($gates.Keys | Where-Object { $_ -eq $Only })
    if ($selected.Count -eq 0) {
        Write-Host "[FAIL] 没有名为 '$Only' 的门禁项；用 -List 查看" -ForegroundColor Red
        exit 1
    }
} else {
    if ($SkipBackendSuite) { $selected = $selected | Where-Object { $_ -ne 'backend-test' } }
    if ($SkipFrontend) { $selected = $selected | Where-Object { $_ -notlike 'frontend-*' } }
}

Write-Host "仓库根 : $Root"
Write-Host "解释器 : $Python"
Write-Host ("并行   : {0}" -f $(if ($Parallel -and $hasXdist) { '后端测试用 -n auto（性能门禁可能抖动）' } elseif ($hasXdist) { '默认串行（xdist 已装，需要时加 -Parallel）' } else { '串行（未安装 pytest-xdist）' }))
Write-Host ''

$results = @()
foreach ($name in $selected) {
    $gate = $gates[$name]
    Write-Host ('=' * 72) -ForegroundColor DarkGray
    Write-Host "> $name — $($gate.Desc)" -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor DarkGray

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        & $gate.Run
    } catch {
        Write-Host "  [exception] $($_.Exception.Message)" -ForegroundColor Red
        $global:LASTEXITCODE = 1
    }
    $sw.Stop()
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev

    $results += [PSCustomObject]@{
        Gate    = $name
        Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        Exit    = $code
        Verdict = $(if ($code -eq 0) { 'PASS' } else { 'FAIL' })
    }
    Write-Host ''
}

Write-Host ('=' * 72)
Write-Host '验收汇总' -ForegroundColor Cyan
Write-Host ('=' * 72)
foreach ($r in $results) {
    $color = if ($r.Exit -eq 0) { 'Green' } else { 'Red' }
    Write-Host ("  {0,-16} {1,-6} {2,8:N1}s" -f $r.Gate, $r.Verdict, $r.Seconds) -ForegroundColor $color
}

$failed = @($results | Where-Object { $_.Exit -ne 0 })
if ($failed.Count -eq 0) {
    Write-Host ''
    Write-Host '全部通过' -ForegroundColor Green
    exit 0
}
Write-Host ''
Write-Host ("失败 {0} 项: {1}" -f $failed.Count, (($failed | ForEach-Object { $_.Gate }) -join ', ')) -ForegroundColor Red
exit 1
