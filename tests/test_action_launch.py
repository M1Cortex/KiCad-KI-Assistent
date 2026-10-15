"""Verify that the toolbar action can actually create and show its window.

Reproduces exactly what KiCad does: a fresh interpreter, no existing wx.App,
the action module executed as a script. Pass ``--show`` to keep the dialog open
until it is closed by hand, otherwise it closes after a few seconds.

    python tests/test_action_launch.py
    python tests/test_action_launch.py --project C:\\Pfad\\board.kicad_sch
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = ROOT

DEFAULT_PROJECT = r"C:\Users\stani\Documents\KiCad\Projekte\RC_Filter"

DRIVER = r'''
import sys, threading, time
sys.path.insert(0, {plugin!r})

import wx
from kicad_ai import ui

PROJECT = {project!r}
HOLD = {hold}

def driver():
    time.sleep(2.0)
    dialog = None
    for w in wx.GetTopLevelWindows():
        if isinstance(w, ui.ReviewDialog):
            dialog = w
            break
    if dialog is None:
        print("FEHLER: kein ReviewDialog sichtbar")
        wx.GetApp().ExitMainLoop()
        return
    print("OK: Dialog sichtbar, Titel:", repr(dialog.GetTitle()))
    print("OK: Projekt:", dialog.project_path)
    print("OK: Blaetter:", dialog.notebook.GetPageCount())

    dialog.on_review(None)
    deadline = time.time() + {timeout}
    while time.time() < deadline and dialog.result is None:
        wx.Yield()
        time.sleep(0.2)
    if dialog.result is None:
        print("FEHLER: Review nicht abgeschlossen")
        wx.GetApp().ExitMainLoop()
        return

    result = dialog.result
    print("OK: Befunde in der Liste:", dialog.lst_findings.GetItemCount())
    print("OK: Anforderungen:", dialog.lst_requirements.GetItemCount())
    print("OK: KI:", result.llm_used, result.llm_model)
    print("OK: Statusleiste:", dialog.status.GetValue())

    if not {hold}:
        dialog.EndModal(wx.ID_OK)

threading.Thread(target=driver, daemon=True).start()
ui.run(PROJECT)
print("OK: sauber beendet")
'''


def main() -> int:
    # The child prints German text; the Windows console defaults to cp1252 and
    # would raise on characters outside it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = sys.argv[1:]
    show = "--show" in args
    project = DEFAULT_PROJECT
    if "--project" in args:
        project = args[args.index("--project") + 1]

    if not os.path.isdir(project):
        print(f"FEHLER: Projekt nicht gefunden: {project}")
        return 1

    source = DRIVER.format(
        plugin=PLUGIN,
        project=project,
        hold="True" if show else "False",
        timeout="600" if show else "300",
    )

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(source)
        driver_path = handle.name

    out_path = driver_path + ".out"
    err_path = driver_path + ".err"

    # Run in a child interpreter so the state matches KiCad's fresh process.
    proc = subprocess.run(
        [sys.executable, driver_path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900 if show else 420,
    )

    for path in (driver_path, out_path, err_path):
        try:
            os.unlink(path)
        except OSError:
            pass

    print(f"Exit-Code : {proc.returncode}")
    if proc.stdout.strip():
        print("--- stdout ---")
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print("--- stderr ---")
        print(proc.stderr.strip()[:3000])

    if "OK: Dialog sichtbar" in proc.stdout:
        return 0
    print("FEHLER: Das Fenster wurde nicht gezeigt.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())