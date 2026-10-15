<#
.SYNOPSIS
    Richtet eine Entwicklerverknüpfung ein: KiCad-Pluginordner -> Plugin-Quellbaum.

.DESCRIPTION
    Erzeugt ein Junctionsymbol (Verzeichnisverknuepfung) vom KiCad-Benutzer-
    Pluginordner auf diesen Quellbaum. Aenderungen an den .py-Dateien sind dann
    sofort in KiCad sichtbar, ohne Kopieren. KiCad selbst wird dafuer nicht neu
    gestartet - die Toolbar-Action laedt den Code bei jedem Klick neu.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File link_dev.ps1
#>

[CmdletBinding()]
param(
    [switch]$Unlink
)

$ErrorActionPreference = "Stop"
$PluginId = "org.kicad.ai-review"
$Source = Split-Path -Parent $MyInvocation.MyCommand.Path

$docs = [Environment]::GetFolderPath("MyDocuments")
$link = Join-Path $docs "KiCad\10.0\plugins\$PluginId"

if ($Unlink) {
    if (Test-Path $link) {
        $item = Get-Item $link -Force
        if ($item.LinkType -eq "Junction") {
            [System.IO.Directory]::Delete($link, $false)
            Write-Host "Junction entfernt: $link" -ForegroundColor Green
        }
        else {
            Write-Host "Kein Junction, sondern ein echtes Verzeichnis - nicht entfernt." -ForegroundColor Yellow
            Write-Host "Bitte manuell loeschen: $link" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "Nichts zu entfernen."
    }
    exit 0
}

$parent = Split-Path $link -Parent
if (-not (Test-Path $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Write-Host "Angelegt: $parent"
}

if (Test-Path $link) {
    $item = Get-Item $link -Force
    if ($item.LinkType -eq "Junction") {
        Write-Host "Junction existiert bereits: $link" -ForegroundColor Yellow
        exit 0
    }
    Write-Host "FEHLER: $link existiert als echtes Verzeichnis." -ForegroundColor Red
    Write-Host "Bitne zuerst loeschen oder umbenennen." -ForegroundColor Red
    exit 1
}

cmd /c mklink /J "`"$link`"" "`"$Source`"" | Out-Null
if (Test-Path $link) {
    Write-Host "Junction erstellt: $link -> $Source" -ForegroundColor Green
}
else {
    Write-Host "FEHLER: Junction konnte nicht erstellt werden." -ForegroundColor Red
    exit 1
}