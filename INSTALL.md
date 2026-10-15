# Installation — KiCad KI-Assistent

KI-gestütztes Schaltplan-Review für KiCad 10.

**Voraussetzungen:** KiCad 10.0 oder neuer. Plugin-Pakete mit `runtime: ipc`
werden von KiCad 6–9 nicht angezeigt.

---

## Variante 1 — Installation über den Plugin- und Content Manager (empfohlen)

### 1. ZIP herunterladen

Repository: <https://github.com/M1Cortex/KiCad-KI-Assistent>

Oder direkt:

```
https://github.com/M1Cortex/KiCad-KI-Assistent/releases/latest/download/kicad-ai-review-0.1.0.zip
```

> **Achtung:** Der Windows-„Speichern unter"-Dialog hängt bei GitHub-Release-Assets
> gerne noch `.zip.zip` an. Prüfe den Dateinamen — er muss mit `.zip` enden.

### 2. In KiCad importieren

1. KiCad öffnen (Projektmanager genügt)
2. **Werkzeuge → Plugin- und Content-Verwaltung** öffnen
   *(in englischen Installationen: Preferences → Plugin and Content Manager)*
3. Auf **Aus Datei installieren...** klicken
4. Die heruntergeladene ZIP-Datei auswählen
5. Das Paket erscheint im Tab **„Ausstehend"** (Pending)
6. Auf **Änderungen anwenden** *(Apply Pending Changes)* klicken

### 3. IPC-Server aktivieren

Damit die Toolbar-Actions geladen werden, muss KiCads IPC-Server laufen.
Das ist einmalig nötig:

1. **Werkzeuge → Allgemeine Einstellungen**
2. Reiter **Programmgr.** *(Program Manager)*
3. Unter **API-Server** den Haken bei **„Server aktivieren"** setzen
4. KiCad neu starten

Alternativ per Kommandozeile:

```powershell
powershell -ExecutionPolicy Bypass -File enable_ipc.ps1
```

### 4. Plugin-Umgebung erzeugen

Beim ersten Start legt KiCad automatisch eine Python-Umgebung für das Plugin an.

1. Erneut **Werkzeuge → Plugin- und Content-Verwaltung → Reiter „Plugins"**
2. Warte, bis der Eintrag grün bzw. ohne Fehlermeldung dasteht (1–3 Minuten)
3. Fehlt die Action: Rechtsklick auf die Action → **„Plugin-Umgebung neu erstellen"**

### 5. Plugin verwenden

1. **PCB-Editor** oder **Projektmanager** öffnen
2. In der Toolbar erscheint **„Schaltplan reviewen"** (Lupe über Schaltplansymbol)
3. Klicken → das Review-Fenster öffnet sich mit dem aktuell geöffneten Projekt

> **Wichtig:** IPC-Actions erscheinen in KiCad 10 nur im PCB-Editor und im
> Projektmanager, **nicht im Schaltplan-Editor**. Das Review liest die
> `.kicad_sch`-Dateien direkt und funktioniert unabhängig davon vollständig.
> Ab KiCad 11 erscheint die Action automatisch auch in Eeschema.

### 6. Installation prüfen

```powershell
cd "$env:USERPROFILE\Documents\KiCad\10.0\plugins\org.kicad.ai_review"
powershell -ExecutionPolicy Bypass -File doctor.ps1
```

Erwartet: `Ergebnis: alle Pruefungen bestanden.`

---

## Variante 2 — Kommandozeile (ohne GUI)

Funktioniert unabhängig von KiCad. Ideal für CI und zum Ausprobieren.

```powershell
cd "$env:USERPROFILE\Documents\KiCad\10.0\plugins\org.kicad.ai_review"

# Vollständiges Review
& "C:\Program Files\KiCad\10.0\bin\python.exe" -m kicad_ai review C:\Pfad\zu\board.kicad_sch

# Nur die deterministischen Regeln, ohne KI
& "C:\Program Files\KiCad\10.0\bin\python.exe" -m kicad_ai review C:\Pfad\zu\board.kicad_sch --no-ai

# Report als Markdown
& "C:\Program Files\KiCad\10.0\bin\python.exe" -m kicad_ai review C:\Pfad\zu\board.kicad_sch `
    --format markdown -o review.md

# Backend prüfen
& "C:\Program Files\KiCad\10.0\bin\python.exe" -m kicad_ai check-llm --list-models
```

---

## Variante 3 — Manuell kopieren

Falls der PCM-Import nicht funktioniert:

1. ZIP entpacken — es enthält `metadata.json` und `plugins/`
2. Den Ordner `plugins\org.kicad.ai_review` kopieren nach:
   ```
   %USERPROFILE%\Documents\KiCad\10.0\plugins\org.kicad.ai_review
   ```
3. IPC-Server aktivieren (Schritt 3 oben)
4. KiCad neu starten

---

## KI-Analyse einrichten (optional)

Ohne Backend arbeitet der Plugin vollständig — nur die KI-Analyse fehlt.

### Ollama (lokal, Daten bleiben auf dem Rechner)

```powershell
ollama serve
ollama pull qwen2.5-coder:14b
```

Für reine Layout- und Signalintegritätsfragen eignen sich auch
`llama3.1:8b`, `qwen3:8b` oder `deepseek-r1:8b`.

### OpenAI-kompatible API

Funktioniert mit OpenAI, Azure OpenAI, LM Studio, vLLM, llama.cpp-Server,
Groq, Together und OpenRouter.

```powershell
$env:KICAD_AI_BACKEND   = "openai"
$env:KICAD_AI_BASE_URL  = "https://api.openai.com/v1"
$env:KICAD_AI_MODEL     = "gpt-4o-mini"
$env:OPENAI_API_KEY     = "sk-..."
```

Diese Variablen werden zusätzlich im Review-Fenster eingetragen, damit sie auch
für den KiCad-Prozess gelten.

---

## MCP-Server einrichten (optional)

Ermöglicht externe KI-Agenten wie Claude Desktop, Cursor oder VS Code, direkt
auf deine Schaltpläne zuzugreifen.

Im Review-Fenster auf **„MCP-Konfiguration anzeigen"** klicken — die fertige
Konfiguration wird für das aktuelle Projekt erzeugt und in die Zwischenablage
kopiert. Dann in die MCP-Client-Konfiguration einfügen.

---

## Deinstallation

**Werkzeuge → Plugin- und Content-Verwaltung → Reiter „Installiert" →
Paket markieren → Deinstallieren → Änderungen anwenden**

Manuell: Ordner `%USERPROFILE%\Documents\KiCad\10.0\plugins\org.kicad.ai_review`
und das Python-Environment unter `%LOCALAPPDATA%\KiCad\10.0\python-environments\org.kicad.ai_review`
löschen.

---

## Fehlerbehebung

| Symptom | Ursache und Lösung |
|---|---|
| Kein Button in der Toolbar | IPC-Server nicht aktiv (Schritt 3). Danach KiCad neu starten. |
| Button fehlt, Actions sind da | Python-Umgebung fehlerhaft: Rechtsklick → „Plugin-Umgebung neu erstellen". |
| „Keine Verbindung zu …" | Ollama läuft nicht. `ollama serve` starten. |
| Meldung „kein Projekt gefunden" | Kein Projekt geöffnet. In KiCad ein `.kicad_pro` öffnen. |
| Hinweise in der Statusleiste | KiCad leitet stdout/stderr der Action dorthin. Dort stehen Fehlermeldungen. |
| Sehr lange Wartezeit | Erstmaliger Lauf: `kicad-cli` analysiert das Design, danach fragt das Modell ab. |

Weitere Diagnose:

```powershell
$env:KICAD_ALLOC_CONSOLE = "1"
$env:KICAD_ENABLE_WXTRACE = "1"
$env:WXTRACE = "KICAD_API"
& "C:\Program Files\KiCad\10.0\bin\kicad.exe"
```

---

## Systemanforderungen

| | |
|---|---|
| KiCad | 10.0 oder neuer |
| Python | 3.9+ (liegt KiCad bei) |
| Abhängigkeiten | `requests`, `wxPython` — beide in KiCads Python enthalten |
| Plattformen | Windows, macOS, Linux |
| Speicher | ~20 MB installiert, zusätzlich Python-Environment |