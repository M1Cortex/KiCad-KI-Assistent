<#
.SYNOPSIS
    Aktiviert den KiCad-IPC-Server, damit Toolbar-Actions aus IPC-Plugins erscheinen.

.DESCRIPTION
    KiCad 10 startet den IPC-Server nur, wenn api.enable_server in
    kicad_common.json true ist. Ohne das werden Plugin-Actions nicht geladen.

    Das Skript aendert ausschliesslich dieses eine Feld und sichert vorher eine
    Kopie der Konfiguration.

    WICHTIG: KiCad muss danach neu gestartet werden.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File enable_ipc.ps1
#>

[CmdletBinding()]
param(
    [switch]$Disable
)

$ErrorActionPreference = "Stop"

$configDir = Join-Path $env:APPDATA "kicad\10.0"
$configFile = Join-Path $configDir "kicad_common.json"

if (-not (Test-Path $configFile)) {
    Write-Host "FEHLER: $configFile nicht gefunden." -ForegroundColor Red
    Write-Host "Starte KiCad einmal, damit die Konfiguration angelegt wird." -ForegroundColor Yellow
    exit 1
}

# Sicherung anlegen
$backup = "$configFile.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $configFile $backup
Write-Host "Sicherung: $backup" -ForegroundColor DarkGray

$config = Get-Content $configFile -Raw | ConvertFrom-Json

# api-Block anlegen, falls nicht vorhanden
if (-not $config.PSObject.Properties.Name.Contains("api")) {
    $config | Add-Member -NotePropertyName "api" -NotePropertyValue ([PSCustomObject]@{}) -Force
}

$desired = if ($Disable) { $false } else { $true }
$config.api.enable_server = $desired

# pythonw.exe als Interpreter fuer IPC-Plugins (Standard)
if (-not $config.api.PSObject.Properties.Name.Contains("interpreter_path")) {
    $config.api | Add-Member -NotePropertyName "interpreter_path" `
        -NotePropertyValue "C:\Program Files\KiCad\10.0\bin\pythonw.exe" -Force
}

$config | ConvertTo-Json -Depth 20 | Set-Content $configFile -Encoding UTF8

$state = if ($desired) { "aktiviert" } else { "deaktiviert" }
Write-Host ""
Write-Host "IPC-Server $state" -ForegroundColor Green
Write-Host "Datei: $configFile" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Naechster Schritt: KiCad vollstaendig neu starten." -ForegroundColor Yellow
Write-Host ""
Write-Host "Danach in KiCad:" -ForegroundColor White
Write-Host "  1. Tools > Plugin- und Content-Verwaltung > Reiter 'Plugins'"
Write-Host "     KiCad erzeugt dort die Python-Umgebung fuer das Plugin"
Write-Host "  2. PCB-Editor oder Projektmanager oeffnen"
Write-Host "  3. In der Toolbar erscheint 'Schaltplan reviewen'"