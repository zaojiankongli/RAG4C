# RAG4C 工作区清理 —— 只删【可再生的测试/构建垃圾】，默认不删任何东西。
#
# 用法（在任意目录下）：
#   scripts\clean_workspace.ps1              # 只统计并列出将被删除的内容（dry-run）
#   scripts\clean_workspace.ps1 -Apply       # 真正删除
#   scripts\clean_workspace.ps1 -Apply -KeepTestdata   # 保留 .testdata 下的夹具
#
# 为什么要有这个脚本：
#   历史 QA 轮次习惯把 pytest 的 basetemp 显式指到仓库内（`--basetemp .pytest-<轮次>`，
#   见 docs/RAG4C-Waku…最终实现说明 §17.1 的复现命令），一次全量回归就留下几十个
#   80MB 量级的目录。累计 100 多次之后，仓库膨胀到 5.2GB，其中 2.8GB 是**完全可再生**
#   的运行残留。手工删容易误伤 backups/ 里的 catalog 真实备份，所以把"删什么、不删什么"
#   固化成代码。
#
# 明确不碰：backups/（catalog 备份）、data/、源码、.env*、.venv、node_modules、
#           .testdata 顶层的夹具文件（smoke_parsers / smoke_tsr 依赖）。

[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$KeepTestdata
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

function Get-DirBytes {
    param([string]$Path)
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return 0 }
    return [int64]$sum
}

$targets = @()

# 1) 仓库根的 pytest basetemp 残留目录
foreach ($d in (Get-ChildItem -LiteralPath $Root -Directory -Force -Filter '.pytest-*' -ErrorAction SilentlyContinue)) {
    $targets += [PSCustomObject]@{ Path = $d.FullName; Bytes = (Get-DirBytes $d.FullName); Kind = 'pytest basetemp' }
}

# 2) .testdata 下的运行残留目录（顶层文件是夹具，保留）
$testdata = Join-Path $Root '.testdata'
if ((-not $KeepTestdata) -and (Test-Path $testdata)) {
    foreach ($d in (Get-ChildItem -LiteralPath $testdata -Directory -Force -ErrorAction SilentlyContinue)) {
        $targets += [PSCustomObject]@{ Path = $d.FullName; Bytes = (Get-DirBytes $d.FullName); Kind = 'testdata run dir' }
    }
}

# 3) 仓库根的历史日志（.gitignore 已忽略 *.log，全部是服务/构建输出）
foreach ($f in (Get-ChildItem -LiteralPath $Root -File -Force -Filter '*.log' -ErrorAction SilentlyContinue)) {
    $targets += [PSCustomObject]@{ Path = $f.FullName; Bytes = [int64]$f.Length; Kind = 'root log' }
}

# 4) 根目录的一次性调试文本
foreach ($name in @('.tmp-stage20-failure.txt', 'debug-readonly3.txt')) {
    $p = Join-Path $Root $name
    if (Test-Path $p) {
        $targets += [PSCustomObject]@{ Path = $p; Bytes = [int64](Get-Item -LiteralPath $p).Length; Kind = 'scratch file' }
    }
}

$totalBytes = ($targets | Measure-Object -Property Bytes -Sum).Sum
if ($null -eq $totalBytes) { $totalBytes = 0 }

Write-Host "仓库根: $Root"
Write-Host ("待清理: {0} 项, 合计 {1:N1} MB" -f $targets.Count, ($totalBytes / 1MB))
Write-Host ''
foreach ($group in ($targets | Group-Object Kind)) {
    $gb = ($group.Group | Measure-Object -Property Bytes -Sum).Sum / 1MB
    Write-Host ("  {0,-20} {1,4} 项  {2,9:N1} MB" -f $group.Name, $group.Count, $gb)
}

if (-not $Apply) {
    Write-Host ''
    Write-Host '这是 dry-run。确认无误后加 -Apply 真正删除。' -ForegroundColor Yellow
    exit 0
}

$freed = 0
$failed = 0
foreach ($t in $targets) {
    try {
        Remove-Item -LiteralPath $t.Path -Recurse -Force -ErrorAction Stop
        $freed += $t.Bytes
    } catch {
        $failed++
        Write-Host ("  [skip] {0}: {1}" -f $t.Path, $_.Exception.Message) -ForegroundColor DarkYellow
    }
}

Write-Host ''
Write-Host ("已释放 {0:N1} MB，失败 {1} 项" -f ($freed / 1MB), $failed) -ForegroundColor Green
if ($failed -gt 0) { exit 1 }
exit 0
