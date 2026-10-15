"""Render the review dialog to PNG so its appearance can be inspected.

    python tools/screenshot_dialog.py [--out bild.png]

Runs a review, waits for it to finish and grabs the window bitmap with the
plausible whole design, then restores the dialog without touching the frame
chrome so the real window can still be shown afterwards.
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

import wx  # noqa: E402

from kicad_ai import ui  # noqa: E402

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]

PROJECT = ARGS[1] if len(ARGS) > 1 else r"C:\Users\stani\Documents\KiCad\Projekte\RC_Filter"
OUT = ARGS[0] if ARGS and ARGS[0].lower().endswith(".png") else os.path.join(ROOT, "dist", "dialog.png")


def capture() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    app = wx.App(False)

    def driver() -> None:
        time.sleep(1.5)
        dialog = None
        for w in wx.GetTopLevelWindows():
            if isinstance(w, ui.ReviewDialog):
                dialog = w
                break
        if dialog is None:
            print("FEHLER: Dialog nicht gefunden")
            wx.GetApp().ExitMainLoop()
            return

        print("Dialog gefunden, aktualisiere Projekt...")
        dialog.cb_ai.SetValue(False)
        dialog.txt_instruction.SetValue(
            "Prüfe bitte die Versorgungsfilterung und die Bauteilewerte"
        )
        dialog.cb_severity.SetSelection(0)
        dialog.on_review(None)

        deadline = time.time() + 180
        while time.time() < deadline and dialog.result is None:
            wx.Yield()
            time.sleep(0.2)

        if dialog.result is None:
            print("FEHLER: Review nicht fertig")
            wx.GetApp().ExitMainLoop()
            return

        print(f"Befunde: {dialog.lst_findings.GetItemCount()}")
        print(f"Anforderungen: {dialog.lst_requirements.GetItemCount()}")

        # Give the layout a chance to settle before grabbing it.
        for _ in range(20):
            dialog.Layout()
            dialog.Update()
            wx.Yield()
            time.sleep(0.05)

        size = dialog.GetSize()
        rect = wx.Rect(0, 0, size.width, size.height)
        bitmap = wx.Bitmap(size.width, size.height)
        context = wx.MemoryDC(bitmap)
        try:
            dialog.Refresh()
            dialog.Update()
            wx.Yield()
            time.sleep(0.3)
            context.Blit(0, 0, size.width, size.height, wx.ClientDC(dialog), wx.Point(0, 0))
            context.SelectObject(wx.NullBitmap)
        finally:
            context.SelectObject(wx.NullBitmap)
            del context

        bitmap.SaveFile(OUT, wx.BITMAP_TYPE_PNG)
        print(f"Gespeichert: {OUT} ({size.width}x{size.height})")

        dialog.EndModal(wx.ID_OK)

    threading.Thread(target=driver, daemon=True).start()
    ui.run(PROJECT)
    return 0


if __name__ == "__main__":
    raise SystemExit(capture())