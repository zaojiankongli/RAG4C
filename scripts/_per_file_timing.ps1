# One-off diagnostic: time and grade every backend test module separately.
# Writes TSV rows to .tmp/per-file-timing.txt so the slow modules are obvious.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root '.venv\Scripts\python.exe'
$Out = Join-Path $Root '.tmp\per-file-timing.txt'
if (Test-Path $Out) { Remove-Item $Out }
foreach ($f in (Get-ChildItem tests -Filter 'test_*.py' | Sort-Object Name)) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $res = & $Py -m pytest $f.FullName -q -p no:cacheprovider 2>&1 | Select-Object -Last 3
    $sw.Stop()
    $line = ($res -join ' | ')
    $verdict = if ($line -match '(\d+) passed') { 'PASS' } elseif ($line -match 'error|failed') { 'FAIL' } else { 'UNKNOWN' }
    Add-Content $Out ("{0}`t{1}`t{2:F2}`t{3}" -f $f.Name, $verdict, $sw.Elapsed.TotalSeconds, $line)
    Write-Host ("{0,-60} {1,6:F2}s {2}" -f $f.Name, $sw.Elapsed.TotalSeconds, $verdict)
}
Write-Host 'DONE'
