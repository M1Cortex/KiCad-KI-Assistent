## KiCad KI-Assistent 0.1.0

KI-gestütztes Schaltplan-Review für KiCad 10.

### Installation

1. Diese ZIP herunterladen: `kicad-ai-review-0.1.0.zip`
2. In KiCad: **Werkzeuge → Plugin- und Content-Verwaltung**
3. **Aus Datei installieren...** → ZIP wählen
4. **Änderungen anwenden**
5. IPC-Server aktivieren (Werkzeuge → Allgemeine Einstellungen → Reiter *Programmgr.* → *API-Server*)
6. KiCad neu starten

Die vollständige Anleitung steht in [INSTALL.md](https://github.com/M1Cortex/KiCad-KI-Assistent/blob/main/INSTALL.md),
inklusive Fehlerbehebung.

> Der Button erscheint in KiCad 10 im PCB-Editor und Projektmanager. Ab KiCad 11
> zusätzlich im Schaltplan-Editor. Das Review selbst liest die `.kicad_sch`-Dateien
> direkt und ist vollständig funktionsfähig.

### Enthalten

- 23 deterministische Prüfregeln (Netze, Versorgung, Pins, BOM, Hierarchie)
- ERC-Übernahme aus KiCad inklusive unterdrückter Ausnahmen
- Anforderungsableitung mit Erfüllungsstatus
- KI-Analyse über Ollama oder jede OpenAI-kompatible API (optional)
- MCP-Server mit 11 Werkzeugen für Claude Desktop, Cursor und VS Code
- CLI mit Exit-Code-Gating für CI

### Voraussetzungen

KiCad 10.0 oder neuer. `requests` und `wxPython` bringt KiCad mit.

MIT-Lizenz.
