<#
.SYNOPSIS
    Prueft, ob das Plugin in KiCad bereit ist.

.DESCRIPTION
    Testet Python-Version, Abhaengigkeiten, kicad-cli, Plugin-Manifest, Icons
    und fuehrt die Testsuite aus. Nuetzlich nach einem Update oder einer
    Neuinstallation.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File doctor.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

$failed = 0

function Write-Head($text) {
    Write-Host ""
    Write-Host "== $text" -ForegroundColor Cyan
}

function Check-Result($name, $ok, $detail) {
    if ($ok) {
        Write-Host "  [OK]   $name" -ForegroundColor Green
        if ($detail) { Write-Host "         $detail" -ForegroundColor DarkGray }
    }
    else {
        Write-Host "  [FEHL] $name" -ForegroundColor Red
        if ($detail) { Write-Host "         $detail" -ForegroundColor DarkGray }
        $script:failed++
    }
}

Write-Host ""
Write-Host "KiCad AI Review - Doctor" -ForegroundColor White
Write-Host "==========================" -ForegroundColor White
Write-Host "Wurzel: $Root"

# --- Python ---------------------------------------------------------------
Write-Head "Python"
$python = $null
foreach ($candidate in @(
    "C:\Program Files\KiCad\10.0\bin\python.exe",
    "$env:KICAD_ROOT\bin\python.exe",
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
)) {
    if ($candidate -and (Test-Path $candidate)) { $python = $candidate; break }
}
Check-Result "Python-Interpreter" ([bool]$python) $python

if ($python) {
    $ver = & $python -c "import sys;print('%d.%d.%d' % sys.version_info[:3])" 2>&1
    Check-Result "Python-Version" ($LASTEXITCODE -eq 0) $ver

    & $python -c "import requests; print(requests.__version__)" 2>&1 | Out-Null
    Check-Result "requests" ($LASTEXITCODE -eq 0) "HTTP-Client fuer KI-Backends"

    & $python -c "import wx; print(wx.version())" 2>&1 | Out-Null
    Check-Result "wxPython" ($LASTEXITCODE -eq 0) "GUI-Toolkit fuer das Review-Fenster"
}

# --- kicad-cli ------------------------------------------------------------
Write-Head "kicad-cli"
$cli = "C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
if (Test-Path $cli) {
    $cliVer = & $cli version 2>&1
    Check-Result "kicad-cli" $true "$cli ($cliVer)"
}
else {
    Check-Result "kicad-cli" $false "nicht unter $cli gefunden - ERC und Netzliste fehlen"
}

# --- Plugin-Manifest ------------------------------------------------------
Write-Head "Plugin-Manifest"
$pluginJson = Join-Path $Root "plugin.json"
if (Test-Path $pluginJson) {
    try {
        $manifest = Get-Content $pluginJson -Raw | ConvertFrom-Json
        Check-Result "plugin.json lesbar" $true "identifier=$($manifest.identifier)"
        Check-Result "runtime" ($manifest.runtime.type -eq "python") "typ=$($manifest.runtime.type)"
        Check-Result "actions" ($manifest.actions.Count -gt 0) "$($manifest.actions.Count) Action(s)"
    }
    catch {
        Check-Result "plugin.json lesbar" $false $_.Exception.Message
    }
}
else {
    Check-Result "plugin.json" $false "fehlt"
}

foreach ($icon in @(
    "icons\review_light_24.png", "icons\review_light_48.png",
    "icons\review_dark_24.png", "icons\review_dark_48.png"
)) {
    $path = Join-Path $Root $icon
    Check-Result "Icon $icon" (Test-Path $path) $(if (Test-Path $path) { "$((Get-Item $path).Length) Bytes" } else { "fehlt" })
}

# --- KI-Backend -----------------------------------------------------------
Write-Head "KI-Backend"
if ($python) {
    Push-Location $Root
    $llmOut = & $python -m kicad_ai check-llm 2>&1
    $llmCode = $LASTEXITCODE
    Pop-Location
    Write-Host "         $($llmOut -join "`n         ")" -ForegroundColor DarkGray
    # Kein Backend ist kein harter Fehler - die Regelpruefung laeuft ohne KI.
    Write-Host "  [INFO] Backend optional - Regelpruefung laeuft ohne KI." -ForegroundColor DarkGray
}

# --- Tests ----------------------------------------------------------------
if (-not $SkipTests -and $python) {
    Write-Head "Testsuite"
    Push-Location $Root
    $testOut = & $python "tests\test_analyzer.py" 2>&1
    $testCode = $LASTEXITCODE
    $tail = $testOut | Select-Object -Last 4
    Write-Host "         $($tail -join "`n         ")" -ForegroundColor DarkGray
    Check-Result "test_analyzer.py" ($testCode -eq 0) "Analyzer, Regeln, Reports"
    Pop-Location
}

# --- Ergebnis -------------------------------------------------------------
Write-Host ""
if ($failed -eq 0) {
    Write-Host "Ergebnis: alle Pruefungen bestanden." -ForegroundColor Green
}
else {
    Write-Host "Ergebnis: $failed Problem(e) gefunden." -ForegroundColor Red
}
Write-Host ""
exit $failed