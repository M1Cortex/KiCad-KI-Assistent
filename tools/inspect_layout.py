"""Report the geometry of every widget in the review dialog.

Mislesionen are hard to spot in a screenshot; the actual rects make an overflow
or a mis-sized child obvious.

    python tools/inspect_layout.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import wx  # noqa: E402

from kicad_ai import SchematicReviewer  # noqa: E402
from kicad_ai.reviewer import ReviewResult  # noqa: E402
from kicad_ai.ui import ReviewDialog  # noqa: E402

PROJECT = r"C:\Users\stani\Documents\KiCad\Projekte\RC_Filter"

app = wx.App(False)
dialog = ReviewDialog(PROJECT, None, use_ai=True)

reviewer = SchematicReviewer()
design = reviewer.load(PROJECT)
findings, _ = reviewer.run_rules(design)
dialog.result = ReviewResult(design=design, findings=findings, summary="Test")
dialog._update_stats()
dialog._refresh_findings()
dialog._refresh_requirements()

dialog.Show()
dialog.Layout()
for _ in range(10):
    wx.Yield()

print(f"Dialog          : pos={dialog.GetPosition()} size={dialog.GetSize()}")
print(f"ClientSize      : {dialog.GetClientSize()}")
print(f"Beste Groesse   : {dialog.GetBestSize()}")
print(f"MinSize         : {dialog.GetMinSize()}")
print()

sizer = dialog.GetSizer()
print(f"Top-Level-Sizer : {type(sizer).__name__}")
print(f"  min={sizer.GetMinSize()}")
print()

print(f"{'Widget':<26} {'Pos':<16} {'Size':<16} Inhalt")
print("-" * 78)


def walk(window, depth=0, seen=None):
    if seen is None:
        seen = set()
    for child in window.GetChildren():
        if id(child) in seen:
            continue
        seen.add(id(child))
        rect = wx.Rect(child.GetPosition(), child.GetSize())
        flag = ""
        if rect.x < 0 or rect.y < 0:
            flag = "  <-- NEGATIV"
        elif child.IsShown():
            parent = wx.Rect(window.GetClientSize())
            if rect.GetRight() > parent.width + 2:
                flag = "  <-- RAGT RECHTS RAUS"
            if rect.GetBottom() > parent.height + 2:
                flag = "  <-- RAGT UNTEN RAUS"
        text = ""
        if isinstance(child, wx.StaticText):
            text = child.GetLabel()[:24]
        elif isinstance(child, wx.Button):
            text = child.GetLabel()
        elif isinstance(child, wx.ComboBox):
            text = f"{len(child.GetItems())} Eintraege"
        elif isinstance(child, wx.Notebook):
            text = f"{child.GetPageCount()} Seiten"
        print(
            f"{'  ' * depth}{child.GetName() or type(child).__name__:<26} "
            f"{str(tuple(child.GetPosition())):<16} "
            f"{str(tuple(child.GetSize())):<16} {text}{flag}"
        )
        if isinstance(child, (wx.Panel, wx.Notebook)):
            walk(child, depth + 1, seen)


walk(dialog)
print()
print(f"StatTiles       : {dialog.stat_row.GetChildren().__len__()}")
for tile in dialog.stat_row.GetChildren():
    print(f"   {tile.GetPosition()} {tile.GetSize()}")
print(f"Scrollbars      : {[type(c).__name__ for c in dialog.lst_findings.GetChildren()]}")

dialog.Destroy()