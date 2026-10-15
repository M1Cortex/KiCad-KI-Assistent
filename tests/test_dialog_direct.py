"""Open the dialog with an explicit project and close it again.

Skips IPC detection so the window can be tested independently of the state of
a running KiCad.

    python tests/test_dialog_direct.py
"""

from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import wx  # noqa: E402

from kicad_ai import SchematicReviewer, ui  # noqa: E402
from kicad_ai.reviewer import ReviewResult  # noqa: E402

PROJECT = sys.argv[1] if len(sys.argv) > 1 else (
    r"C:\Users\stani\Documents\KiCad\Projekte\RC_Filter"
)


def driver() -> None:
    time.sleep(2.0)
    dialog = None
    for window in wx.GetTopLevelWindows():
        if isinstance(window, ui.ReviewDialog):
            dialog = window
            break
    if dialog is None:
        print("FEHLER: ReviewDialog nicht sichtbar")
        print("        offene Fenster:", [w.GetTitle() for w in wx.GetTopLevelWindows()])
        wx.GetApp().ExitMainLoop()
        return

    print("OK: Dialog sichtbar:", repr(dialog.GetTitle()))
    print("OK: Position:", dialog.GetPosition(), "Groesse:", dialog.GetSize())

    dialog.on_review(None)
    deadline = time.time() + 300
    while time.time() < deadline and dialog.result is None:
        wx.Yield()
        time.sleep(0.2)

    if dialog.result is None:
        print("FEHLER: Review nicht fertig")
        wx.GetApp().ExitMainLoop()
        return

    print("OK: Befunde:", dialog.lst_findings.GetItemCount())
    print("OK: Status:", dialog.status.GetValue())
    time.sleep(0.5)
    dialog.EndModal(wx.ID_OK)


def main() -> int:
    if not os.path.isdir(PROJECT):
        print(f"FEHLER: Projekt nicht gefunden: {PROJECT}")
        return 1

    ui.ensure_app()
    threading.Thread(target=driver, daemon=True).start()

    # Fill the dialog with a real review before showing it, so the test also
    # covers the population path.
    dialog = ui.ReviewDialog(PROJECT, None, use_ai=False)
    try:
        reviewer = SchematicReviewer()
        design = reviewer.load(PROJECT)
        findings, _ = reviewer.run_rules(design)
        dialog.result = ReviewResult(design=design, findings=findings, summary="Test")
        dialog._update_stats()
        dialog._refresh_findings()
        dialog.Centre(wx.BOTH)
        dialog.ShowModal()
        print("OK: sauber beendet")
        return 0
    finally:
        dialog.Destroy()


if __name__ == "__main__":
    raise SystemExit(main())