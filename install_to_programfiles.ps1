<#
.SYNOPSIS
    Installiert das KiCad AI Review Plugin nach C:\Program Files\KiCad\PlugIn_SpaceBunny.

.DESCRIPTION
    Kopiert den Quellbaum in das Zielverzeichnis und registriert das Plugin
    zusätzlich in KiCads Benutzer-Pluginverzeichnis, damit die Toolbar-Actions
    sofort sichtbar werden.

    Das Zielverzeichnis liegt unter "Program Files" und erfordert daher
    Administratorrechte. PowerShell-Skript per Rechtsklick >
    "Mit PowerShell ausführen" starten, oder in einer elevated Konsole:

        powershell -ExecutionPolicy Bypass -File install_to_programfiles.ps1

.PARAMETER Target
    Zielverzeichnis. Vorgabe: C:\Program Files\KiCad\PlugIn_SpaceBunny

.PARAMETER SkipRegister
    Nur kopieren, keine Registrierung im KiCad-Benutzerordner.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_to_programfiles.ps1
#>

[CmdletBinding()]
param(
    [string]$Target = "C:\Program Files\KiCad\PlugIn_SpaceBunny",
    [switch]$SkipRegister
)

$ErrorActionPreference = "Stop"

$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginId = "org.kicad.ai_review"

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step($message) {
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Write-Ok($message) {
    Write-Host "    OK: $message" -ForegroundColor Green
}

function Write-Warn($message) {
    Write-Host "    !: $message" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "KiCad AI Review - Installation" -ForegroundColor White
Write-Host "--------------------------------" -ForegroundColor White
Write-Host "Quelle : $Source"
Write-Host "Ziel   : $Target"
Write-Host ""

if (-not (Test-Admin)) {
    Write-Warn "Keine Administratorrechte. Das Ziel liegt unter Program Files."
    Write-Warn "Bitte PowerShell als Administrator neu starten und das Skript erneut ausfuehren."
    Write-Host ""
    $answer = Read-Host "Trotzdem versuchen? (j/N)"
    if ($answer -ne "j") {
        exit 1
    }
}

# --- 1. Zielverzeichnis anlegen -------------------------------------------
Write-Step "Zielverzeichnis wird erstellt"
if (-not (Test-Path $Target)) {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
}
Write-Ok $Target

# --- 2. Dateien kopieren --------------------------------------------------
Write-Step "Dateien werden kopiert"
Get-ChildItem $Target -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch "__pycache__" -and $_.Extension -ne ".pyc" } |
    Remove-Item -Force -ErrorAction SilentlyContinue

$copied = 0
Get-ChildItem $Source -Recurse -File | Where-Object {
    $_.FullName -notmatch "__pycache__" -and $_.Extension -ne ".pyc"
} | ForEach-Object {
    $relative = $_.FullName.Substring($Source.Length).TrimStart("\")
    $destination = Join-Path $Target $relative
    $parent = Split-Path $destination -Parent
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Copy-Item $_.FullName $destination -Force
    $copied++
}
Write-Ok "$copied Dateien kopiert"

# --- 3. In KiCads Benutzer-Pluginordner registrieren ---------------------
if ($SkipRegister) {
    Write-Warn "Registrierung im KiCad-Benutzerordner uebersprungen"
} else {
    Write-Step "Plugin wird im KiCad-Benutzerordner registriert"
    $kicadVersion = "10.0"
    $docs = [Environment]::GetFolderPath("MyDocuments")
    $userPluginRoot = Join-Path $docs "KiCad\$kicadVersion\plugins\$PluginId"

    if (-not (Test-Path $docs)) {
        $userPluginRoot = Join-Path $env:APPDATA "..\..\Documents\KiCad\$kicadVersion\plugins\$PluginId"
    }

    if (Test-Path $userPluginRoot) {
        Get-ChildItem $userPluginRoot -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch "__pycache__" -and $_.Extension -ne ".pyc" } |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
    else {
        New-Item -ItemType Directory -Force -Path $userPluginRoot | Out-Null
    }

    $registered = 0
    Get-ChildItem $Source -Recurse -File | Where-Object {
        $_.FullName -notmatch "__pycache__" -and $_.Extension -ne ".pyc"
    } | ForEach-Object {
        $relative = $_.FullName.Substring($Source.Length).TrimStart("\")
        $destination = Join-Path $userPluginRoot $relative
        $parent = Split-Path $destination -Parent
        if (-not (Test-Path $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        Copy-Item $_.FullName $destination -Force
        $registered++
    }
    Write-Ok "$registered Dateien nach $userPluginRoot"
}

# --- 4. Python-Abhaengigkeiten pruefen ----------------------------------
Write-Step "Python-Umgebung wird geprueft"
$kicadRoot = "C:\Program Files\KiCad\$kicadVersion"
$python = Join-Path $kicadRoot "bin\python.exe"
if (Test-Path $python) {
    Write-Ok "KiCad-Python: $python"
    $verdict = & $python -c "import sys; print(sys.version.split()[0])" 2>&1
    Write-Ok "Version $verdict"
}
else {
    Write-Warn "KiCad-Python nicht gefunden unter $python"
}

# --- Fertig ---------------------------------------------------------------
Write-Host ""
Write-Host "Installation abgeschlossen." -ForegroundColor Green
Write-Host ""
Write-Host "Naechste Schritte:" -ForegroundColor White
Write-Host "  1. KiCad neu starten"
Write-Host "  2. KiCad-Einstellungen > Plugin- und Content-Verwaltung > Reiter 'Plugins'"
Write-Host "     (Python-Umgebung wird beim ersten Start im Hintergrund erzeugt)"
Write-Host "  3. In Eeschema/Pcbnew erscheint die Toolbar-Aktion 'Schaltplan reviewen'"
Write-Host ""
Write-Host "Ohne KI-Backend funktioniert die Regelpruefung sofort. Fuer die KI-Analyse:"
Write-Host "  ollama serve"
Write-Host "  ollama pull qwen2.5-coder:14b"
Write-Host ""