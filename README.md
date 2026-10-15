# KiCad KI-Assistent

KI-gestütztes Schaltplan-Review für **KiCad 10** — mit deterministischen Prüfregeln,
Anforderungsableitung, LLM-Anbindung über Ollama oder API und einem **MCP-Server**
für externe KI-Agenten.

> **Installation:** → **[INSTALL.md](INSTALL.md)**
>
> ```
> https://github.com/M1Cortex/KiCad-KI-Assistent/releases/latest/download/kicad-ai-review-0.1.0.zip
> ```
>
> In KiCad: *Werkzeuge → Plugin- und Content-Verwaltung → Aus Datei installieren…*

**Projektstatus:** v0.1.0, getestet gegen KiCad 10.0.7 (Windows).
53 automatisierte Tests laufen durch. Getestet an KiCads eigenen Demo-Projekten.

---

## Was es tut

| Bereich | Funktion |
|---|---|
| **Review** | 23 deterministische Prüfregeln über Netz-, Versorgungs-, Pin-, BOM- und Hierarchiefehler |
| **Verbindungen testen** | Auflösung des gesamten Netzgraphen über Labels, Power-Symbole und Blattgrenzen; Connectivity-Tracing zwischen beliebigen Bauteilen |
| **ERC** | Übernimmt die offiziellen KiCad-ERC-Ergebnisse und führt sie als eigene Befundkategorie |
| **Anforderungen ableiten** | Leitet aus dem Schaltplan Anforderungen ab und bewertet deren Erfüllung (`erfüllt` / `teilweise` / `fehlt` / `unklar`) |
| **KI-Analyse** | Ollama (lokal, offline) oder jede OpenAI-kompatible API |
| **MCP** | 11 Werkzeuge für Claude Desktop, Cursor, VS Code und andere MCP-Clients |
| **CI** | Exit-Code-Gating, Reports als Text, Markdown, HTML, JSON |

Die KI ist **optional**. Ohne Backend laufen alle deterministischen Regeln und die
statische Anforderungsableitung vollständig.

## Vorschau

Ergebnis auf einem KiCad-Demo-Projekt (`complex_hierarchy`, 40 Bauteile, 52 Netze):

```
Umfang  : 40 Bauteile, 2 Blätter, 52 Netze, 164 Pin-Verbindungen
Befunde : 2 Fehler, 3 Warnungen, 2 Info, 44 Hinweise
Dauer   : 1.7s

[NETZ]  2 Befund(e)

  [FEHLER] NET-NO-SOURCE - Versorgungsnetz '+12V' ohne Quelle
  +12V: C104.1, D101.1, U101.3, U201.8, U301.8 enthält keinen POWER_OUT-Pin.
  Das Netz kann nicht versorgt werden.
  Ort      : C104, D101, U101, U201, U301 | net +12V
  Empfehlung : PWR_FLAG setzen oder eine Spannungsquelle / einen Regler ergänzen.
```

Geprüfte Prüfregeln: `NET-NO-SOURCE`, `NET-MULTI-SOURCE`, `NET-SINGLE-PIN`,
`PIN-FLOATING`, `POWER-NO-DECOUPLING`, `I2C-PULLUP`, `CRYSTAL-CAP`,
`GROUND-MISSING`, `BOM-NO-FOOTPRINT`, `BOM-GENERIC-VALUE`, `SYM-DUPLICATE-REF`,
`LABEL-SINGLE-USE` sowie alle offiziellen KiCad-ERC-Typen.

---

## Installation

Ausführliche Anleitung: **[INSTALL.md](INSTALL.md)**

Kurzfassung: Release-ZIP herunterladen, in KiCad über
*Werkzeuge → Plugin- und Content-Verwaltung → Aus Datei installieren…* importieren,
IPC-Server aktivieren, KiCad neu starten.

### Variante A — Über den Plugin- und Content Manager (empfohlen)

```powershell
python tools\build_package.py
```

Erzeugt `dist\kicad-ai-review-0.1.0.zip`. Dann in KiCad:

**Werkzeuge → Plugin- und Content-Verwaltung → Aus Datei installieren…**

KiCad legt das Plugin unter `%USERPROFILE%\Documents\KiCad\10.0\plugins\` ab und
erzeugt automatisch eine Python-Umgebung.

### Variante B — Entwicklungsmodus mit Junction

```powershell
powershell -ExecutionPolicy Bypass -File link_dev.ps1
```

Verbindet KiCads Pluginordner mit diesem Quellbaum. Python-Codeänderungen sind
dann ohne Neustart und ohne Kopieren sichtbar.

Aufheben mit `link_dev.ps1 -Unlink`.

### Variante C — Nach `C:\Program Files\KiCad\PlugIn_SpaceBunny`

```powershell
# PowerShell als Administrator starten, dann:
powershell -ExecutionPolicy Bypass -File install_to_programfiles.ps1
```

Kopiert dorthin **und** registriert das Plugin zusätzlich im KiCad-Benutzerordner,
damit die Toolbar-Actions sofort funktionieren.

### Nach der Installation

1. KiCad neu starten
2. KiCad-Einstellungen → Plugin- und Content-Verwaltung → Reiter **Plugins**
   (KiCad legt die Python-Umgebung im Hintergrund an)
3. In Eeschema/Pcbnew erscheint die Toolbar-Aktion **„Schaltplan reviewen"**

Falls keine Action erscheint: Python-Umgebung in den Plugin-Einstellungen über
Rechtsklick → „Recreate Plugin Environment" neu erzeugen.

---

## KI-Backend einrichten

### Ollama (lokal, empfohlen für Vertraulichkeit)

```powershell
ollama serve
ollama pull qwen2.5-coder:14b
python -m kicad_ai check-llm --list-models
```

Für reine Layout-/Signalintegritätsfragen eignen sich auch
`llama3.1:8b`, `qwen3:8b` oder `deepseek-r1:8b`.

### OpenAI-kompatible API

Funktioniert mit OpenAI, Azure OpenAI, LM Studio, vLLM, llama.cpp-Server, Groq,
Together und OpenRouter.

```powershell
$env:KICAD_AI_BACKEND = "openai"
$env:KICAD_AI_BASE_URL = "https://api.openai.com/v1"
$env:KICAD_AI_MODEL = "gpt-4o-mini"
$env:OPENAI_API_KEY = "sk-..."
```

### Umgebungsvariablen

| Variable | Standard | Bedeutung |
|---|---|---|
| `KICAD_AI_BACKEND` | `ollama` | `ollama`, `openai` oder `none` |
| `KICAD_AI_MODEL` | `qwen2.5-coder:14b` | Modellname |
| `KICAD_AI_BASE_URL` | `http://localhost:11434` | API-Endpunkt |
| `KICAD_AI_API_KEY` | — | API-Schlüssel (auch `OPENAI_API_KEY`) |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama-Endpunkt |
| `KICAD_CLI` | — | Pfad zu `kicad-cli` |
| `KICAD_AI_PROJECT` | — | Standardprojekt für die CLI/MCP |

---

## Kommandozeile

Funktioniert unabhängig von KiCad — ideal für CI und Vor-Commit-Hooks.

```powershell
# Vollständiges Review als Text
python -m kicad_ai review .\projekt.kicad_sch

# Nur deterministische Regeln, als Markdown, Warnungen und höher abbrechen
python -m kicad_ai review .\projekt --no-ai --format markdown --fail-on warning

# KI mit Zusatzfrage
python -m kicad_ai review .\projekt --instruction "Prüfe die I2C-Trennung und die ADC-Versorgung"

# Nur Anforderungen
python -m kicad_ai review .\projekt --requirements-only --format markdown

# Bericht in Datei, JSON-Ausgabe für Pipelines
python -m kicad_ai review .\projekt -o report.json --format json

# Backend prüfen
python -m kicad_ai check-llm --list-models

# Regeln auflisten
python -m kicad_ai list-rules
```

**CI-Integration:**

```yaml
- name: Schaltplan-Review
  run: |
    "C:\Program Files\KiCad\10.0\bin\python.exe" -m kicad_ai review \
      "${{ github.workspace }}/board.kicad_sch" \
      --format markdown -o review.md --fail-on error
```

`--fail-on error` (Standard) gibt Exit-Code 1 zurück, sobald ein Fehler vorliegt.

---

## MCP-Server

Der MCP-Server spricht JSON-RPC über stdio und funktioniert mit jedem
MCP-fähigen Client.

### Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kicad": {
      "command": "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe",
      "args": [
        "-m", "kicad_ai.mcp_server",
        "--project", "C:\\Pfad\\zu\\deinem\\Projekt"
      ],
      "env": {
        "PYTHONPATH": "C:\\Users\\stani\\Documents\\KiCad\\10.0\\plugins\\org.kicad.ai-review",
        "KICAD_AI_BACKEND": "ollama",
        "KICAD_AI_MODEL": "qwen2.5-coder:14b"
      }
    }
  }
}
```

Im Review-Fenster von KiCad gibt es **„MCP-Konfiguration anzeigen"**, das genau
diesen Block für das aktuelle Projekt erzeugt und in die Zwischenablage kopiert.

### Werkzeuge

| Werkzeug | Zweck |
|---|---|
| `list_projects` | Projekte in einem Verzeichnis finden |
| `get_project_info` | Kennzahlen, Blätter, Versorgungs- und Massenetze |
| `list_symbols` | Bauteile mit Pins, Footprint, Datenblatt |
| `list_nets` | Netze mit allen Pins, nach Typ gruppiert |
| `get_net` | Ein Netz im Detail mit Blattzugehörigkeit |
| `trace_connection` | „Wie ist A mit B verbunden?" — gemeinsame Netze und Pfade |
| `check_erc` | Offizielle KiCad-ERC-Ergebnisse |
| `review_schematic` | Vollständiges Review, optional mit KI |
| `derive_requirements` | Anforderungen mit Erfüllungsstatus |
| `ask_about_schematic` | Freie Frage an das Modell |
| `check_llm_status` | Backend-Erreichbarkeit prüfen |

Beispiele für Prompts:

- „Welche Netze verbinden U1 und J5?"
- „Zeig mir alle Bauteile ohne Footprint."
- „Prüfe, ob die Versorgung von U3 vollständig entkoppelt ist."
- „Welche Anforderungen an den Schaltplan lassen sich ableiten und welche fehlen?"

---

## Prüfregeln

### Netz (`netz`)
- `NET-SINGLE-PIN` — Netz mit nur einem Anschluss (Tippfehler im Label)
- `NET-NO-SOURCE` — Versorgungsnetz ohne treibende Quelle
- `NET-MULTI-SOURCE` — mehrere Ausgänge auf einem Netz (Kurzschlussrisiko)
- `NET-NO-SINK` — Ausgang ohne Last
- `LABEL-SINGLE-USE` — Label nur einmal verwendet
- `LABEL-NAMING` — Netzname verletzt die Konvention

### Versorgung (`power`)
- `POWER-NO-DECOUPLING` — Versorgungsnetz ohne Abblockkondensator
- `POWER-UNDECLARED` — Spannungsnetz ohne Spannung im Namen
- `GROUND-MISSING` — Bauteil mit Versorgungspin ohne Massebezug
- `CRYSTAL-CAP` — Oszillator ohne zwei Lastkondensatoren
- `I2C-PULLUP` — I2C-Bus ohne Pull-up-Widerstände

### Pins (`pin`)
- `PIN-FLOATING` — Eingangspin ohne jede Verbindung
- `PIN-OUTPUT-UNUSED` — Ausgangspin ohne Last

### Stückliste (`bom`)
- `BOM-NO-FOOTPRINT` — Bauteil ohne Footprint
- `BOM-NO-DATASHEET` — Bauteil ohne Datenblatt
- `BOM-GENERIC-VALUE` — Platzhalterwert wie `?` oder `TBD`
- `DNP-IN-BOM` — DNP-Bauteil fälschlich in der BOM

### Annotation und Hierarchie
- `SYM-DUPLICATE-REF` — Referenz doppelt vergeben
- `SYM-UNANNOTATED` — Symbol ohne Referenz
- `SHEET-UNUSED-PORT` — hierarchischer Port ohne Verwendung
- `CONNECTOR-NO-PIN-NAME` — Connector-Pins ohne sprechende Namen

### ERC
Alle offiziellen KiCad-ERC-Typen (`pin_not_connected`, `powerpin_not_driven`,
`two_power_symbols`, `similar_labels`, …) werden übernommen und übersetzt.
Zusätzlich werden ERC-Ausnahmen als Hinweis gemeldet — unterdrückte Fehler
verschwinden sonst still.

---

## Architektur

```
kicad_ai/
├── sexpr.py       S-Expression-Parser (ohne Fremdabhängigkeiten)
├── model.py       Domänenmodell: Design, Net, SymInstance, Pin
├── loader.py      kicad-cli-Brücke + Datei-Hierarchie-Traversierung
├── rules.py       23 deterministische Prüfregeln
├── llm.py         Ollama- und OpenAI-kompatibler Client
├── context.py     Budgetierter LLM-Kontext + Prompt
├── reviewer.py    Orchestrierung, Anforderungsmodell
├── report.py      Text / Markdown / HTML / JSON
├── ui.py          wx-Dialog
├── cli.py         Kommandozeile
└── mcp_server.py  MCP-Server (11 Werkzeuge)
```

**Zwei Quellen der Wahrheit:** Konnektivität kommt aus
`kicad-cli sch export netlist` — das ist KiCads eigene Auflösung inklusive
Hierarchie und Power-Symbolen. Der eigene Parser liefert Geometrie, Felder,
Blattstruktur und Pin-Namen, die das Netzlistformat nicht abbildet.

**Warum Regeln vor KI:** Die deterministischen Regeln laufen in Millisekunden,
sind reproduzierbar und erfinden nichts. Die KI bekommt den extrahierten Faktenkontext
und ergänzt nur, was Regeln nicht leisten können.

---

## Python-API

```python
from kicad_ai import SchematicReviewer, LLMConfig

reviewer = SchematicReviewer(
    llm_config=LLMConfig(backend="ollama", model="qwen2.5-coder:14b")
)
result = reviewer.review("board.kicad_sch", use_ai=True)

print(result.summary)
for finding in result.filtered(min_severity=Severity.WARNING):
    print(finding.severity.label, finding.rule, finding.title, finding.location())

for req in result.requirements:
    print(req.id, req.status, req.requirement)

result.to_json()
```

---

## Tests

```powershell
python tests\test_analyzer.py    # 37 Tests: Parser, Loader, Regeln, Reports, KI-Stufe
python tests\test_ui.py         # 4 Tests: wx-Dialog headless
python tests\smoke_mcp.py       # MCP-Runde über alle 11 Werkzeuge
powershell -ExecutionPolicy Bypass -File doctor.ps1
```

---

## Bekannte Grenzen

- **KiCad 10 IPC** unterstützt Plugins nur im PCB-Editor. Die Action erscheint
  dort und im Projektmanager; im Schaltplan-Editor erscheint sie automatisch ab
  KiCad 11, wo die IPC-API den Schaltplan abdeckt. Das Review selbst liest die
  `.kicad_sch`-Dateien direkt und ist daher unabhängig davon voll funktionsfähig.
- **Eigene Prüfregeln laufen ohne KI**, KI-Befunde tragen `confidence=0.7` und
  sind als solche gekennzeichnet.
- **Große Designs** werden für den LLM-Kontext budgetiert (Standard 120 Netze,
  250 Bauteile); Kürzungen werden im Kontext markiert.
- Das Plugin **ändert keine Designdaten**. Es ist reine Analyse.

---

## Lizenz

MIT