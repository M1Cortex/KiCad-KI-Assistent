<#
.SYNOPSIS
    Fuehrt die komplette Testsuite aus: Analyzer, KI-Pipeline, UI, MCP, Doctor.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File run_tests.ps1
#>

[CmdletBinding()]
param([switch]$Quick)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "C:\Program Files\KiCad\10.0\bin\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python).Source }

$suites = @("tests\test_analyzer.py", "tests\test_ai_pipeline.py", "tests\test_ui.py")
if (-not $Quick) { $suites += "tests\smoke_mcp.py" }

$failed = @()

foreach ($suite in $suites) {
    Write-Host ""
    Write-Host "=== $suite ===" -ForegroundColor Cyan
    Push-Location $Root
    & $python $suite 2>&1 | Select-Object -Last 3
    $code = $LASTEXITCODE
    Pop-Location
    # PowerShell reports stderr from python as errors, so rely on the unittest summary.
    if ($code -ne 0) { $failed += $suite }
}

Write-Host ""
Write-Host "=== package validation ===" -ForegroundColor Cyan
Push-Location $Root
& $python tools\build_package.py --validate 2>&1 | Select-Object -Last 2
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { $failed += "package" }

Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "ALLE SUITES BESTANDEN" -ForegroundColor Green
}
else {
    Write-Host "FEHLGESCHLAGEN: $($failed -join ', ')" -ForegroundColor Red
}
exit $failed.Count