"""Capture a wx window to PNG using the Win32 window DC.

``wx.ClientDC`` cannot capture child native windows (static text, buttons),
so a Blit of a dialog comes out empty. Reading the window DC with BitBlt
includes every child window and therefore shows what the user really sees.

    python tools/screenshot.py --out bild.png --mode dialog|findings|dark
"""

from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import wx  # noqa: E402

from kicad_ai.rules import Finding, Severity  # noqa: E402
from kicad_ai.theme import Theme, severity_colour  # noqa: E402
from kicad_ai.widgets import Row, ScrollingList  # noqa: E402

PROJECT = r"C:\Users\stani\Documents\KiCad\Projekte\RC_Filter"

SRCCOPY = 0x00CC0020


def capture_window(window: wx.Window, path: str) -> bool:
    """BitBlt the window (including children) into a bitmap."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:  # pragma: no cover - Windows only
        return False

    try:
        from PIL import ImageGrab  # noqa: F401
    except ImportError:
        pass

    hwnd = int(window.GetHandle())
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    rect = wintypes.RECT()
    # WindowRect includes the frame; use it so nothing is clipped.
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return False

    window_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    gdi32.BitBlt(mem_dc, 0, 0, width, height, window_dc, 0, 0, SRCCOPY)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # negative => top-down
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(
        mem_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0
    )

    # GetDIBits returns 32bpp BGRA; convert through Pillow, which handles the
    # channel order properly (wxImage would need a manual swap).
    ok = False
    try:
        from PIL import Image as PILImage

        image = PILImage.frombuffer(
            "RGB", (width, height), buffer, "raw", "BGRX", 0, 1
        )
        image.save(path)
        ok = True
    except ImportError:
        fallback = wx.Image(width, height)
        fallback.SetData(buffer)
        ok = fallback.SaveFile(path, wx.BITMAP_TYPE_PNG)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, window_dc)
    return ok


SAMPLE = [
    Finding(rule="NET-NO-SOURCE", severity=Severity.ERROR,
            title="Versorgungsnetz '+12V' ohne Quelle",
            detail="C104.1, D101.1, U101.3 enthält keinen POWER_OUT-Pin. Das Netz kann nicht versorgt werden.",
            category="netz", refs=["C104", "D101", "U101"], nets=["+12V"],
            fix="PWR_FLAG setzen oder eine Spannungsquelle ergänzen."),
    Finding(rule="BOM-GENERIC-VALUE", severity=Severity.WARNING,
            title="R1 hat keinen konkreten Wert",
            detail="R1 hat den Wert 'R'. Ohne spezifischen Wert ist die Stückliste nicht eindeutig.",
            category="bom", refs=["R1"], fix="Konkreten Wert eintragen, z. B. R1 = 10k."),
    Finding(rule="PIN-FLOATING", severity=Severity.ERROR,
            title="U2.5 ist unverbunden",
            detail="U2 (MCU) Pin 5 'NRST' ist ein input-Pin ohne Verbindung.",
            category="pin", refs=["U2"], fix="Signalquelle ergänzen oder No-Connect setzen."),
    Finding(rule="BOM-NO-DATASHEET", severity=Severity.HINT,
            title="C1 ohne Datenblatt", detail="C1 hat kein Datenblatt-Feld gefüllt.",
            category="bom", refs=["C1"]),
]


def mode_dialog(out: str, dark: bool) -> None:
    app = wx.App(False)
    import kicad_ai.ui as ui_module

    original = ui_module.detect_dark
    ui_module.detect_dark = lambda: dark
    from kicad_ai import SchematicReviewer
    from kicad_ai.reviewer import ReviewResult
    from kicad_ai.ui import ReviewDialog

    dialog = ReviewDialog(PROJECT, None, use_ai=True)
    reviewer = SchematicReviewer()
    design = reviewer.load(PROJECT)
    findings, _ = reviewer.run_rules(design)
    result = ReviewResult(
        design=design,
        findings=findings,
        summary="Der Schaltplan enthält einen RC-Filter mit zwei Stufen. Die Bauteilewerte "
                "sind noch Platzhalter und müssen für die Stückliste ergänzt werden.",
    )
    dialog.result = result
    dialog._update_stats()
    dialog._refresh_findings()
    dialog._refresh_requirements()
    dialog._update_counts()
    dialog.summary.set_text(result.summary, severity_colour(dialog.theme, Severity.WARNING))
    dialog.status.ChangeValue("2 Fehler · 5 Warnungen · 21 Hinweise   →   13 angezeigt")

    dialog.Show()
    dialog.Raise()
    dialog.SetFocus()
    dialog.Layout()
    # Let every child settle: a screenshot taken too early shows half-painted
    # regions that look like a layout bug.
    for _ in range(6):
        dialog.Refresh(True)
        dialog.Update()
        wx.Yield()
        time.sleep(0.15)
    time.sleep(0.4)

    ok = capture_window(dialog, out)
    print(f"{'OK' if ok else 'FEHLER'} -> {out}")
    dialog.Destroy()
    ui_module.detect_dark = original


def mode_findings(out: str, dark: bool) -> None:
    app = wx.App(False)
    theme = Theme(dark=dark)
    frame = wx.Frame(None, title="Befunde", size=(1060, 420))
    panel = wx.Panel(frame)
    panel.SetBackgroundColour(theme.bg)
    sizer = wx.BoxSizer(wx.VERTICAL)
    listing = ScrollingList(panel, theme)
    rows = [
        Row(primary=f.title, secondary=f.rule, tertiary=f.detail, badge=f.category,
            colour=severity_colour(theme, f.severity), payload=f, location=f.location())
        for f in SAMPLE
    ]
    listing.set_rows(rows)
    sizer.Add(listing, 1, wx.EXPAND | wx.ALL, 10)
    panel.SetSizer(sizer)
    frame.Show()
    frame.Raise()
    frame.Layout()
    for _ in range(25):
        wx.Yield()
        time.sleep(0.03)

    ok = capture_window(frame, out)
    print(f"{'OK' if ok else 'FEHLER'} -> {out}")
    frame.Destroy()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(ROOT, "dist", "shot.png"))
    parser.add_argument("--mode", default="dialog", choices=["dialog", "findings"])
    parser.add_argument("--dark", action="store_true")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    if args.mode == "dialog":
        mode_dialog(args.out, args.dark)
    else:
        mode_findings(args.out, args.dark)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())