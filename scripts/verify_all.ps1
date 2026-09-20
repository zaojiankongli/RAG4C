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
    [switch]$WithVisual,
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
# 视觉质量门禁：默认不跑（要起 vite + 加载 171 个页面，约 4 分钟），显式 -WithVisual 才进。
#
# 为什么要接成门禁而不是"脚本躺在 frontend/scripts 里"：R5/R6 造了仪器却没进门禁，
# 于是 docs/41 里"窄屏触控目标已修"这句话在无人复核的情况下烂了一个循环 ——
# 那条规则当时挂在 DOM 里根本不存在的 .app-topbar 上（见 docs/42）。
$gates['frontend-visual'] = @{
    Desc = 'Playwright 视觉量化（19 路由 × 3 视口 × 3 主题，阈值 + 页面数下界）'
    Run  = {
        $Node = (Get-Command node -ErrorAction SilentlyContinue).Source
        if (-not $Node) { throw '找不到 node.exe，无法跑视觉门禁' }
        $ViteBin = Join-Path $Frontend 'node_modules\vite\bin\vite.js'
        if (-not (Test-Path $ViteBin)) { throw "找不到 vite 入口: $ViteBin" }
        $Report = Join-Path $Frontend 'output\visual-quality\verify-gate.json'
        if (Test-Path $Report) { Remove-Item $Report -Force }

        $ViteLog = Join-Path $Root '.tmp\verify-vite.log'
        New-Item -ItemType Directory -Force -Path (Join-Path $Root '.tmp') | Out-Null
        # 已有 dev server 在 :1420 就直接复用（--strictPort 会因端口占用而起不来，
        # 那是环境的既有状态，不该判成门禁失败）。
        $reused = $false
        try {
            $pre = Invoke-WebRequest -Uri 'http://localhost:1420/' -UseBasicParsing -TimeoutSec 5
            if ($pre.StatusCode -eq 200) { $reused = $true }
        } catch { }
        $vite = $null
        if (-not $reused) {
            $vite = Start-Process -FilePath $Node `
                -ArgumentList $ViteBin, '--port', '1420', '--strictPort' `
                -WorkingDirectory $Frontend -RedirectStandardOutput $ViteLog `
                -RedirectStandardError "$ViteLog.err" -PassThru -WindowStyle Hidden
        }
        try {
            $ready = $reused
            $deadline = (Get-Date).AddSeconds(90)
            while ((Get-Date) -lt $deadline) {
                if ($ready) { break }
                Start-Sleep -Seconds 2
                if ($vite -and $vite.HasExited) { break }
                try {
                    $probe = Invoke-WebRequest -Uri 'http://localhost:1420/' -UseBasicParsing -TimeoutSec 10
                    if ($probe.StatusCode -eq 200) { $ready = $true; break }
                } catch { }
            }
            if (-not $ready) {
                Get-Content "$ViteLog.err" -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
                throw 'vite dev server 90s 内未就绪（视觉门禁依赖 :1420）'
            }
            if ($reused) { Write-Host '  复用已在跑的 vite dev server（:1420）' }
            # 视觉门禁对后端是否在线敏感：空库/断连时表格与徽标根本不渲染，数字天然更好看。
            # 不声明模式，就等于允许"空态的 0 违规"去冒充"数据态的 0 违规"。
            $apiMode = '断开（测量的是空态/错误态，不代表数据密集态）'
            try {
                $api = Invoke-WebRequest -Uri 'http://127.0.0.1:8010/api/health' -UseBasicParsing -TimeoutSec 5
                if ($api.StatusCode -eq 200) { $apiMode = '后端在线（数据态可渲染）' }
            } catch { }
            Write-Host "  后端状态 : $apiMode"
            if ($apiMode -like '断开*') { Write-Host '  提示：先跑 scripts/seed_visual_qa.py 并起 :8010，再复测数据密集态' }

            Push-Location $Frontend
            try { & $Node 'scripts\visual-quality.mjs' 'verify-gate' } finally { Pop-Location }
            if (-not (Test-Path $Report)) { throw "视觉门禁没有产出报告: $Report" }

            $t = (Get-Content $Report -Raw -Encoding UTF8 | ConvertFrom-Json).totals
            # 页面数下界：指标"变好"绝不允许来自"量得更少"。
            if ($t.pages -lt 170) { throw "覆盖页数异常: $($t.pages) < 170（19 路由 × 3 视口 × 3 主题）" }
            Write-Host ("  实测: 对比度 {0} / 部件对比度 {1}（候选 {3}）/ 触控 {2} / 溢出 {4} / 裁切 {5}" -f `
                $t.contrastViolations, $t.componentContrastViolations, $t.tinyTargets, `
                $t.componentContrastCandidates, $t.overflowingElements, $t.clippedElements)
            Write-Host ("  如实上报跳过: 渐变背景 {0} / 部件无有效边框 {1} / 混排白名单(专名) {2}" -f `
                $t.contrastSkippedGradient, $t.componentContrastSkipped, $t.mixedLanguageProperNouns)
            Write-Host ("  控制台错误 {0}（已知盲区 {1} = /enterprise/* 401，本地无 dev 身份通道；非盲区 {2}）/ HTTP>=400 {3}" -f `
                $t.consoleErrors, $t.consoleErrorsBlindSpot, $t.consoleErrorsUnexpected, $t.httpFailures)
            $fails = @()
            if ($t.overflowingElements -ne 0) { $fails += "横向溢出 $($t.overflowingElements) 处（基线 0）" }
            if ($t.clippedElements -ne 0) { $fails += "内容被裁切 $($t.clippedElements) 处（基线 0）" }
            if ($t.contrastViolations -gt 12) { $fails += "文字对比度违规 $($t.contrastViolations) > 12" }
            if ($t.tinyTargets -gt 70) { $fails += "触控目标不足 $($t.tinyTargets) > 70" }
            # 控制台错误按"是否属于已登记的 /enterprise/* 401 盲区"拆开。
            # R10 补上 route 归因后复跑，实测 18 条 401 全部来自 /consistency（不在裁定范围内），
            # /enterprise/* 反而 0 条——因为它在无 actorToken 时直接不发请求。
            # 所以这里保持"非盲区必须为 0"，既不放宽白名单也不建身份通道，让门禁如实红。
            if ($t.consoleErrorsUnexpected -ne 0) { $fails += "非盲区控制台错误 $($t.consoleErrorsUnexpected) 条（要求 0）" }
            # 1.4.11：396 是 R9 基线（HEAD 744）。R10 把「控件描边」与「结构性引导线」拆成
            # 两个 token 并让 TDesign level-2 桥到控件描边后，实测 0 条，故按实测收阈。
            # 收阈的前提是分母没塌：本轮判定 594 个部件（> 旧违规数 396），
            # 因此"0 违规"不能用"量得更少"解释；下方 candidates 下界把这件事钉成契约。
            if ($t.componentContrastViolations -gt 0) { $fails += "部件对比度违规 $($t.componentContrastViolations) > 基线 0" }
            # 分母下界：与 pages>=170 同构——指标"变好"绝不允许来自"量得更少"。
            # 3243 是 R10 实测候选数。R12 按新基线重导出为 2700：/config 的 9 个 TDesign 复合
            # InputNumber 换成 facade 原生控件后候选 3234 -> 2865，而逐路由核对**只有 /config 变了**
            # （每次加载 138 -> 97），减掉的是复合组件自带的带边框子元素，不是"少扫了"——
            # 同一路由 formControls 反而 55 -> 57、无可访问名 9 -> 0（findings §31）。
            if ($t.componentContrastCandidates -lt 2700) { $fails += "部件对比度候选数 $($t.componentContrastCandidates) < 2700（分母疑似塌陷，违规数不可信）" }
            # 真正让"0 违规"有意义的是**判定数** = 候选 - 无边框跳过 - 填充可辨识豁免。
            # R11 594 -> R12 405，仍高于当初收阈依据的旧违规基线 396，所以守住 380。
            $ccJudged = $t.componentContrastCandidates - $t.componentContrastSkipped - $t.componentContrastIdentifiedByFill
            if ($ccJudged -lt 380) { $fails += "部件对比度实际判定数 $ccJudged < 380（判定面塌陷，0 违规不可信）" }
            # 4.1.2/3.3.2 可访问名：R12 实测 0（分母 formControls 963，取 900 留 ~7% 余量）。
            # 违规数与分母必须一起判，否则"0"只是"没量到"。
            if ($t.missingAccessibleNames -gt 0) { $fails += "无可见名称的表单控件 $($t.missingAccessibleNames) > 基线 0" }
            if ($t.formControls -lt 900) { $fails += "表单控件总数 $($t.formControls) < 900（分母疑似塌陷，可访问名指标不可信）" }
            # ARIA tabs 结构**故意不设阈**：实测 327/327 全坏，且因 /enterprise/* 身份盲区，
            # 门禁只覆盖到 12 个缺陷文件里的 3 个（findings §30）。锁 327 = 冻结已知全坏，锁 0 = 假绿。
            if ($fails.Count) { throw ($fails -join '; ') }
        } finally {
            if ($vite -and -not $vite.HasExited) { Stop-Process -Id $vite.Id -Force -ErrorAction SilentlyContinue }
        }
    }
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
    # 视觉门禁要起 vite + 加载 171 页，默认不进"一条命令全部门禁"；-WithVisual 显式开启。
    if (-not $WithVisual) { $selected = $selected | Where-Object { $_ -ne 'frontend-visual' } }
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
