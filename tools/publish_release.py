#!/usr/bin/env python3
"""Publish the plugin to GitHub: push the tag, create a release with the PCM zip.

Prerequisites:
  * GitHub CLI (``gh``) installed and authenticated: ``gh auth login``
  * A git remote named ``origin`` pointing at your GitHub repository

Usage:
    python tools/publish_release.py --repo OWNER/REPO --version 0.1.0
    python tools/publish_release.py --repo OWNER/REPO --version 0.1.0 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(args: list[str], dry_run: bool = False, capture: bool = False) -> str:
    if dry_run:
        print("  [dry-run]", " ".join(args))
        return ""
    print("  $", " ".join(args))
    proc = subprocess.run(
        args, cwd=ROOT, capture_output=capture, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        if capture:
            sys.stderr.write(proc.stderr or "")
        raise SystemExit(f"Fehlgeschlagen ({proc.returncode}): {' '.join(args)}")
    return proc.stdout if capture else ""


def gh_available() -> bool:
    try:
        run(["gh", "--version"], capture=True)
        return True
    except (FileNotFoundError, SystemExit):
        return False


def manual_instructions(repo: str, version: str, zip_path: str) -> None:
    """Print the exact steps when GitHub CLI is unavailable."""
    tag = f"v{version}"
    print("\n" + "=" * 66)
    print("GitHub CLI ('gh') fehlt - manuelle Schritte:")
    print("=" * 66)
    print(f"""
1. GitHub CLI installieren (PowerShell als Administrator):

     winget install --id GitHub.cli

2. Anmelden:

     gh auth login

3. Dieses Verzeichnis als Repository anlegen und hochladen:

     git init
     git add -A
     git commit -m "Initial commit: KiCad KI-Assistent 0.1.0"
     git branch -M main
     git remote add origin https://github.com/{repo}.git
     git push -u origin main

4. Release mit der ZIP erstellen:

     gh release create {tag} "{zip_path}" ^
       --title "KiCad KI-Assistent {version}" ^
       --notes-file release_notes.md ^
       --verify-tag

5. Pruefen:

     https://github.com/{repo}/releases/tag/{tag}
""")
    print("Danach erreicht jeder Nutzer die Datei unter:")
    print(f"  https://github.com/{repo}/releases/download/{tag}/kicad-ai-review-{version}.zip")
    print("=" * 66)


def ensure_clean_tree() -> None:
    status = run(["git", "status", "--porcelain"], capture=True)
    if status.strip():
        print("Achtung: es gibt uncommittete Aenderungen:")
        for line in status.strip().splitlines():
            print("   ", line)
        answer = input("Trotzdem fortfahren? (j/N) ")
        if answer.lower() != "j":
            raise SystemExit("Abgebrochen.")


def build_package() -> str:
    print("\n[1/5] PCM-Paket bauen")
    out = run(
        [sys.executable, os.path.join(ROOT, "tools", "build_package.py")],
        capture=True,
    )
    for line in (out or "").splitlines():
        if "archive" in line or "sha256" in line or "size" in line:
            print("  ", line.strip())
    with open(os.path.join(ROOT, "package", "metadata.json"), "r", encoding="utf-8") as handle:
        version = json.load(handle)["versions"][0]["version"]
    zip_path = os.path.join(ROOT, "dist", f"kicad-ai-review-{version}.zip")
    if not os.path.isfile(zip_path):
        raise SystemExit(f"Paket nicht gefunden: {zip_path}")
    return zip_path


def run_tests() -> None:
    print("\n[2/5] Tests ausfuehren")
    suites = ["tests/test_analyzer.py", "tests/test_ai_pipeline.py", "tests/test_ui.py", "tests/smoke_mcp.py"]
    for suite in suites:
        out = run([sys.executable, os.path.join(ROOT, suite)], capture=True)
        summary = [ln for ln in (out or "").splitlines() if ln.startswith("Ran ") or ln == "OK"]
        print(f"   {suite}: {' / '.join(summary) or '?'}")


def update_download_url(repo: str, version: str) -> None:
    print(f"\n[3/5] Download-URL aktualisieren ({repo})")
    meta_path = os.path.join(ROOT, "package", "metadata.json")
    with open(meta_path, "r", encoding="utf-8") as handle:
        meta = json.load(handle)
    url = (
        f"https://github.com/{repo}/releases/download/"
        f"v{version}/kicad-ai-review-{version}.zip"
    )
    changed = False
    for entry in meta["versions"]:
        if entry.get("version") == version and entry.get("download_url") != url:
            entry["download_url"] = url
            changed = True
    if changed:
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print("   ", url, "(bereits korrekt)" if not changed else "")


def verify_package() -> None:
    print("\n[4/5] Paketinhalt pruefen")
    zip_path = next(
        os.path.join(ROOT, "dist", f)
        for f in os.listdir(os.path.join(ROOT, "dist"))
        if f.endswith(".zip")
    )
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        checks = {
            "metadata.json an Root": "metadata.json" in names,
            "plugin.json im Pluginordner": any(
                n.endswith("plugins/org.kicad.ai-review/plugin.json") for n in names
            ),
            "keine Tests im Paket": not any("/tests/" in n for n in names),
            "keine __pycache__": not any("__pycache__" in n for n in names),
            "Archiv intakt": zf.testzip() is None,
        }
    for name, ok in checks.items():
        print(f"   [{'OK' if ok else 'FEHLER'}] {name}")
    if not all(checks.values()):
        raise SystemExit("Paketpruefung fehlgeschlagen.")


def write_release_notes(version: str) -> str:
    notes_path = os.path.join(ROOT, "release_notes.md")
    notes = f"""## KiCad KI-Assistent {version}

KI-gestütztes Schaltplan-Review für KiCad 10.

### Installation

1. Diese ZIP herunterladen: `kicad-ai-review-{version}.zip`
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
"""
    with open(notes_path, "w", encoding="utf-8") as handle:
        handle.write(notes)
    return notes_path


def publish(repo: str, version: str, dry_run: bool) -> None:
    print("\n[5/5] Zu GitHub veröffentlichen")
    tag = f"v{version}"
    zip_path = os.path.join(ROOT, "dist", f"kicad-ai-review-{version}.zip")
    notes_path = write_release_notes(version)

    if not dry_run:
        run(["git", "add", "-A"])
        run(["git", "commit", "-m", f"Release {tag}"])
        run(["git", "tag", "-a", tag, "-m", f"KiCad KI-Assistent {version}"])

    if not gh_available():
        manual_instructions(repo, version, zip_path)
        return

    run(["git", "push", "origin", "main"], dry_run)
    run(["git", "push", "origin", tag], dry_run)

    run([
        "gh", "release", "create", tag, zip_path,
        "--repo", repo,
        "--title", f"KiCad KI-Assistent {version}",
        "--notes-file", notes_path,
        "--verify-tag",
    ], dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plugin auf GitHub veroeffentlichen")
    parser.add_argument("--repo", required=True, help="GitHub-Repo als OWNER/REPO")
    parser.add_argument("--version", default="", help="Version (Standard: aus metadata.json)")
    parser.add_argument("--dry-run", action="store_true", help="Nichts ausfuehren, nur zeigen")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    if not gh_available():
        print("Hinweis: GitHub CLI nicht gefunden - es werden manuelle Schritte ausgegeben.")
    if not args.dry_run:
        ensure_clean_tree()

    if not args.skip_tests:
        run_tests()

    zip_path = build_package()
    version = args.version or os.path.basename(zip_path).split("-")[-1].replace(".zip", "")

    if not args.dry_run:
        update_download_url(args.repo, version)
        # Rebuild so the archive embeds the correct download_url.
        build_package()

    verify_package()

    publish(args.repo, version, args.dry_run)

    print("\nFertig." if not args.dry_run else "\nDry-run abgeschlossen.")
    if not args.dry_run:
        print(f"Release: https://github.com/{args.repo}/releases/tag/v{version}")
        print(f"ZIP     : https://github.com/{args.repo}/releases/download/v{version}/kicad-ai-review-{version}.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())