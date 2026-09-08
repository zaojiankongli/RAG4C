# RAG4C 检查脚本 —— 免去手打路径前缀。
#
# 注意：本文件必须以 **UTF-8 with BOM** 保存。
# Windows PowerShell 5.1 读 .ps1 时，没有 BOM 就按 ANSI(GBK) 解码，中文注释
# 会被拆成乱码字节、引号配对错乱，然后报一堆"缺少右大括号 / 字符串缺少终止符"
# —— 报错位置全指向不相干的行，极难往编码上想。改这个文件时别把 BOM 弄丢。
#
# 用法（在任意目录下）：
#   D:\program_project\python_project\RAG4C\scripts\check.ps1                 # 跑全部离线 smoke 套件
#   D:\program_project\python_project\RAG4C\scripts\check.ps1 -Only wiring    # 只跑名字含 wiring 的
#   D:\program_project\python_project\RAG4C\scripts\check.ps1 -Milvus         # 额外跑真实 Milvus 联调
#   D:\program_project\python_project\RAG4C\scripts\check.ps1 -Install        # 先把依赖装进项目 .venv
#   D:\program_project\python_project\RAG4C\scripts\check.ps1 -All            # 全部 + 前端构建
#
# 为什么要有这个脚本，几个坑各踩过一次：
#   1. PowerShell 不把 `.venv\Scripts\python.exe` 当相对路径（开头没有 `.\`
#      时按命令名解析，报"无法加载模块 .venv"）。
#   2. 在用户主目录下敲 `scripts\xxx.py` 会找不到文件。
#   3. `uv pip install` 会按 cwd / VIRTUAL_ENV 猜环境，在项目外执行会把包
#      装进完全不相干的 venv，而且**不报错** —— 只在第一行输出里提一句
#      "Using Python ... environment at: ..."，很容易看漏。
# 这里统一把工作目录切到仓库根、把解释器路径写死、给 uv 显式指定 --python。

[CmdletBinding()]
param(
    [string]$Only = '',   # 只跑名字包含该子串的 smoke 套件，例如 -Only wiring
    [switch]$Install,     # 先执行 uv pip install ".[milvus]" 到项目 .venv
    [switch]$Milvus,      # 跑 scripts\check_milvus.py（需要真实 Milvus 可达）
    [switch]$Frontend,    # 跑 npm run build
    [switch]$All          # 等价于 -Milvus -Frontend
)

$ErrorActionPreference = 'Stop'

if ($All) { $Milvus = $true; $Frontend = $true }

# ---- 定位仓库根 -------------------------------------------------------- #
# 不依赖单一变量：$PSScriptRoot 在某些 PowerShell 宿主/调用方式下会是空的，
# 空值一路传到 Set-Location 才炸，报错还指向不相干的行。这里逐个回退，
# 都拿不到就明确报错，而不是带着空字符串继续跑。
$ScriptFile = $PSCommandPath
if (-not $ScriptFile) { $ScriptFile = $MyInvocation.MyCommand.Path }
if (-not $ScriptFile) { $ScriptFile = $MyInvocation.MyCommand.Definition }
if ((-not $ScriptFile) -or (-not (Test-Path $ScriptFile))) {
    Write-Host '[FAIL] 无法定位脚本自身路径，请改用显式路径运行：' -ForegroundColor Red
    Write-Host '       powershell -File D:\program_project\python_project\RAG4C\scripts\check.ps1'
    exit 1
}

$ScriptDir = Split-Path -Parent $ScriptFile
$Root      = Split-Path -Parent $ScriptDir
if ((-not $Root) -or (-not (Test-Path (Join-Path $Root 'pyproject.toml')))) {
    Write-Host "[FAIL] 仓库根定位失败: '$Root'（该目录下没有 pyproject.toml）" -ForegroundColor Red
    exit 1
}
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    Write-Host "[FAIL] 找不到虚拟环境解释器: $Python" -ForegroundColor Red
    Write-Host '       先建虚拟环境:  uv venv   或   python -m venv .venv'
    exit 1
}

Write-Host "仓库根   : $Root"
Write-Host "解释器   : $Python"

# Milvus 地址：**只记下来，不在这里设进环境**。
# 离线 smoke 套件里有断言在测配置默认值（smoke_foundation 断言
# milvus.uri == "./rag4c.db"），全局设了 RAG4C_MILVUS_URI 会把它们打挂——
# 检查脚本不该改变被测对象的运行环境。这个变量只在 check_milvus 那一步
# 临时设进去，跑完就还原。
$MilvusUri = $env:RAG4C_MILVUS_URI
if (-not $MilvusUri) { $MilvusUri = 'http://192.168.100.128:19530' }
Write-Host "Milvus   : $MilvusUri （仅 check_milvus 步骤使用）"

$failed = @()

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)

    Write-Host ''
    Write-Host ('-' * 68) -ForegroundColor DarkGray
    Write-Host "> $Name" -ForegroundColor Cyan
    Write-Host ('-' * 68) -ForegroundColor DarkGray

    # 子步骤失败不中断整轮：一次跑完拿到全部失败项，比修一个跑一遍快
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    & $Body
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev

    if ($code -ne 0) {
        $script:failed += $Name
        Write-Host "[FAIL] $Name (exit $code)" -ForegroundColor Red
    }
}

# ---- 装依赖（可选）----------------------------------------------------- #
if ($Install) {
    # --python 是关键：不加的话 uv 会按 cwd / VIRTUAL_ENV 自己挑环境，
    # 很可能装到别的 venv 里去，然后这里的检查照样报"未安装"。
    Invoke-Step 'uv pip install .[milvus]' {
        uv pip install --python $Python ".[milvus]"
    }.GetNewClosure()

    # 装完立刻回读实际生效的版本，确认装进了正确的环境。
    # 注意：这段 Python 代码里**一个引号都不能有**。PowerShell 调用原生
    # exe 时不保留参数内部的双引号，`print("x", v)` 会被剥成 `print(x, v)`，
    # 于是报一个莫名其妙的 NameError。所以只 print 值，标签写在外面。
    $probe = 'import pymilvus,sys;print(pymilvus.__version__);print(sys.executable)'
    Invoke-Step 'pymilvus 版本确认' {
        & $Python -c $probe
    }.GetNewClosure()
}

# ---- 离线 smoke 套件（不需要任何外部服务）------------------------------ #
# 自动发现，不维护硬编码清单：新增一个 smoke_xxx.py 就自动纳入，
# 否则清单迟早和目录对不上，而且漏跑是静默的。
$suites = Get-ChildItem -Path $ScriptDir -Filter 'smoke_*.py' | Sort-Object Name
if ($Only) {
    $suites = @($suites | Where-Object { $_.BaseName -like "*$Only*" })
    if ($suites.Count -eq 0) {
        Write-Host "[FAIL] 没有匹配 '$Only' 的 smoke 套件" -ForegroundColor Red
        exit 1
    }
}
# 离线阶段主动摘掉 RAG4C_MILVUS_URI：这些套件里有断言在测配置**默认值**
# （smoke_foundation 断言 milvus.uri == "./rag4c.db"），只要环境里有这个
# 覆盖就会挂——不管是本脚本设的还是你自己在 shell 里设的。跑完还原。
# 同理，其它 RAG4C_* 覆盖也可能打挂对应套件，遇到再按这个模式加。
$savedUriForSuites = $env:RAG4C_MILVUS_URI
Remove-Item Env:\RAG4C_MILVUS_URI -ErrorAction SilentlyContinue
try {
    foreach ($suite in $suites) {
        $suitePath = $suite.FullName
        Invoke-Step $suite.BaseName { & $Python $suitePath }.GetNewClosure()
    }
} finally {
    if ($null -ne $savedUriForSuites) {
        $env:RAG4C_MILVUS_URI = $savedUriForSuites
    }
}

# ---- 真实 Milvus 联调 -------------------------------------------------- #
if ($Milvus) {
    $milvusScript = Join-Path $ScriptDir 'check_milvus.py'
    # 只在这一步把地址设进环境，跑完还原，避免污染上面的离线套件
    $savedUri = $env:RAG4C_MILVUS_URI
    $env:RAG4C_MILVUS_URI = $MilvusUri
    try {
        Invoke-Step 'check_milvus' { & $Python $milvusScript }.GetNewClosure()
    } finally {
        if ($null -eq $savedUri) {
            Remove-Item Env:\RAG4C_MILVUS_URI -ErrorAction SilentlyContinue
        } else {
            $env:RAG4C_MILVUS_URI = $savedUri
        }
    }
}

# ---- 前端构建 ---------------------------------------------------------- #
if ($Frontend) {
    $frontendDir = Join-Path $Root 'frontend'
    Invoke-Step 'frontend build' {
        Push-Location $frontendDir
        try { npm run build } finally { Pop-Location }
    }.GetNewClosure()
}

Write-Host ''
Write-Host ('=' * 68)
if ($failed.Count -eq 0) {
    Write-Host '全部通过' -ForegroundColor Green
    Write-Host ('=' * 68)
    exit 0
}
Write-Host ("失败 {0} 项: {1}" -f $failed.Count, ($failed -join ', ')) -ForegroundColor Red
Write-Host ('=' * 68)
exit 1
