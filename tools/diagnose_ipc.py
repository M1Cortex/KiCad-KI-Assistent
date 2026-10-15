"""Diagnose why the IPC action cannot find the open project.

Run with the plugin's own interpreter while KiCad is open::

    python tools/diagnose_ipc.py

Checks, in order:
  1. is the KiCad API enabled in the settings?
  2. does the named pipe / socket exist?
  3. can kicad-python connect at all?
  4. which document types are reported as open?
"""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime, timezone
from getpass import getuser
from pathlib import Path


def section(title: str) -> None:
    print()
    print(f"--- {title} ---")


def check_settings() -> dict:
    section("1. KiCad-Einstellungen")
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "kicad" / "10.0" / "kicad_common.json",
        Path(os.environ.get("APPDATA", "")) / "kicad" / "10.0" / "kicad.json",
    ]
    found = False
    for path in candidates:
        if not path.is_file():
            print(f"    (nicht vorhanden) {path}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"    FEHLER beim Lesen {path.name}: {exc}")
            continue
        found = True
        api = data.get("api")
        if api is None:
            print(f"    {path.name}: kein 'api'-Block")
        else:
            print(f"    {path.name}: {api}")
    if not found:
        print("    Keine KiCad-Konfiguration gefunden.")
    return {"found": found}


def check_socket() -> Path | None:
    section("2. IPC-Socket")
    env_socket = os.environ.get("KICAD_API_SOCKET")
    if env_socket:
        print(f"    KICAD_API_SOCKET = {env_socket}")
    else:
        print("    KICAD_API_SOCKET nicht gesetzt (Startet KiCad nicht ueber den Plugin-Mechanismus)")

    if platform.system() == "Windows":
        base = Path(os.environ.get("TEMP", "")) / "kicad"
        paths = [base / "api.sock"]
    else:
        paths = [Path("/tmp/kicad/api.sock"), Path("/tmp/api.sock")]

    for path in paths:
        exists = path.exists()
        print(f"    {'JA ' if exists else 'NEIN'}  {path}")
        if exists:
            return path
    return None


def check_kipy() -> None:
    section("3. Verbindung mit kicad-python")
    try:
        from kipy import KiCad
    except ImportError:
        print("    FEHLER: kipy ist nicht installiert.")
        print("    Loesung: requirements.txt installieren oder in KiCad")
        print("             'Plugin-Umgebung neu erstellen' ausloesen.")
        return

    try:
        version = KiCad().get_version()
        print(f"    verbunden, KiCad-Version {version}")
    except Exception as exc:  # noqa: BLE001
        print(f"    FEHLER: {type(exc).__name__}: {exc}")
        print("    Moegliche Ursache: API-Server nicht aktiv oder Socket fehlt.")
        return

    section("4. Offene Dokumente")
    from kipy.proto.common.types import DocumentType

    try:
        listing = KiCad().get_open_documents()
    except TypeError:
        listing = []
        for name in ("PCB", "SCHEMATIC", "FOOTPRINT", "SYMBOL"):
            doc_type = getattr(DocumentType, name, None)
            if doc_type is None:
                continue
            try:
                listing.extend(KiCad().get_open_documents(doc_type))
            except Exception:  # noqa: BLE001
                pass

    if not listing:
        print("    (keine offenen Dokumente gemeldet)")
    for doc in listing:
        print(f"    {doc}")

    section("5. Projektpfad")
    for doc in listing:
        project = getattr(doc, "project", None)
        path = getattr(project, "path", "") if project is not None else ""
        print(f"    {getattr(doc, 'type', '?')}: path={getattr(doc, 'path', '')!r}")
        if path:
            print(f"      -> Projekt: {path}")
            return
    print("    Kein Projektpfad ermittelt.")


def main() -> int:
    print("KiCad-IPC-Diagnose")
    print(f"Python {sys.version.split()[0]}  |  Interpreter {sys.executable}")
    print(f"Zeitpunkt {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}")
    print(f"Benutzer {getuser()}")

    check_settings()
    check_socket()
    check_kipy()

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())