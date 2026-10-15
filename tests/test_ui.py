"""Headless smoke tests for the review dialog and the drawn widgets.

Building the dialog without showing it catches sizer/parenting mistakes, and
driving the review through the button handler catches threading and lifecycle
problems. Rendering is exercised separately by tools/screenshot.py.

Run with KiCad's bundled interpreter::

    python tests/test_ui.py
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import wx  # noqa: E402

from kicad_ai.loader import KicadCli, SchematicLoader  # noqa: E402
from kicad_ai.reviewer import ReviewResult, SchematicReviewer  # noqa: E402
from kicad_ai.rules import Severity  # noqa: E402
from kicad_ai.theme import Theme, severity_colour, severity_label  # noqa: E402
from kicad_ai.widgets import Row, ScrollingList  # noqa: E402

DEMO = r"C:\Program Files\KiCad\10.0\share\kicad\demos\simulation\amplifier-ac\amplifier-ac.kicad_sch"

_app = wx.App(False)


def _demo_result() -> ReviewResult:
    reviewer = SchematicReviewer()
    design = SchematicLoader(KicadCli()).load(DEMO)
    findings, _ = reviewer.run_rules(design)
    return ReviewResult(design=design, findings=findings, summary="Testzusammenfassung")


class TestScrollingList(unittest.TestCase):
    """The list draws itself, so the geometry logic is worth testing."""

    def setUp(self):
        self.frame = wx.Frame(None, size=(800, 400))
        self.theme = Theme(dark=False)
        self.list = ScrollingList(self.frame, self.theme)
        self.list.SetSize(780, 300)

    def tearDown(self):
        self.frame.Destroy()

    def _rows(self, count: int) -> list[Row]:
        theme = self.theme
        return [
            Row(
                primary=f"Befund {i}",
                secondary="RULE-X",
                tertiary="Beschreibung des Befundes",
                badge="netz",
                colour=severity_colour(theme, Severity.ERROR),
                payload=Severity.ERROR,
                location="R1, R2",
            )
            for i in range(count)
        ]

    def test_empty_list(self):
        self.list.set_rows([])
        self.assertEqual(self.list.GetItemCount(), 0)
        self.assertIsNone(self.list.row_payload(0))

    def test_row_count_matches(self):
        self.list.set_rows(self._rows(7))
        self.assertEqual(self.list.GetItemCount(), 7)

    def test_payload_lookup(self):
        rows = self._rows(3)
        self.list.set_rows(rows)
        self.assertEqual(self.list.row_payload(1), Severity.ERROR)
        self.assertIsNone(self.list.row_payload(99))

    def test_sort_by_severity(self):
        theme = self.theme
        rows = [
            Row("Hinweis", payload=Severity.HINT, colour=severity_colour(theme, Severity.HINT)),
            Row("Fehler", payload=Severity.ERROR, colour=severity_colour(theme, Severity.ERROR)),
            Row("Warnung", payload=Severity.WARNING, colour=severity_colour(theme, Severity.WARNING)),
        ]
        self.list.set_rows(rows)
        self.list.sort_by_severity()
        self.assertEqual(
            [r.primary for r in self.list.rows],
            ["Fehler", "Warnung", "Hinweis"],
        )

    def test_paint_does_not_raise(self):
        self.list.set_rows(self._rows(30))
        dc = wx.ClientDC(self.list)
        self.list._paint_rows(dc)

    def test_paint_with_no_rows(self):
        self.list.set_rows([])
        dc = wx.ClientDC(self.list)
        self.list._paint_rows(dc)

    def test_ellipsize_shortens_long_text(self):
        dc = wx.ClientDC(self.list)
        from kicad_ai.widgets import _ellipsize

        short = _ellipsize(dc, "kurz", 500)
        long = _ellipsize(dc, "x" * 500, 60)
        self.assertEqual(short, "kurz")
        self.assertLess(len(long), 200)
        self.assertTrue(long.endswith("…"))


class TestDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(DEMO):
            raise unittest.SkipTest("KiCad demo not available")
        cls.result = _demo_result()

    def setUp(self):
        if not os.path.isfile(DEMO):
            self.skipTest("KiCad demo not available")
        self.dialog = None

    def tearDown(self):
        if self.dialog is not None:
            self.dialog.Destroy()

    def _open(self) -> None:
        from kicad_ai.ui import ReviewDialog

        self.dialog = ReviewDialog(DEMO, None, use_ai=False)

    def _fill(self) -> ReviewResult:
        self.dialog.result = self.result
        self.dialog._update_stats()
        self.dialog._refresh_findings()
        self.dialog._refresh_requirements()
        self.dialog._update_counts()
        return self.result

    def test_dialog_constructs(self):
        self._open()
        self.assertEqual(self.dialog.notebook.GetPageCount(), 3)
        self.assertIsNotNone(self.dialog.status)
        self.assertTrue(self.dialog.project_label.GetLabel())

    def test_findings_are_populated(self):
        self._open()
        result = self._fill()
        self.assertEqual(
            self.dialog.lst_findings.GetItemCount(),
            len(result.filtered(min_severity=Severity.HINT)),
        )

    def test_severity_filter(self):
        self._open()
        result = self._fill()
        self.dialog.cb_severity.SetSelection(1)  # Nur Fehler
        self.dialog.on_severity_changed(None)
        expected = len([f for f in result.findings if f.severity >= Severity.ERROR])
        self.assertEqual(self.dialog.lst_findings.GetItemCount(), expected)

    def test_stat_tiles_render(self):
        self._open()
        self._fill()
        self.assertEqual(len(self.dialog.stat_row.GetChildren()), 6)

    def test_status_line_has_counts(self):
        self._open()
        self._fill()
        self.assertIn("Fehler", self.dialog.status.GetValue())

    def test_detail_rendering(self):
        self._open()
        self._fill()
        self.dialog._show_detail(self.result.findings[0])
        self.assertIn("Regel", self.dialog.txt_detail.GetValue())

    def test_finding_activation_shows_detail(self):
        self._open()
        self._fill()
        self.dialog.on_finding_activated(0)
        self.assertEqual(self.dialog.notebook.GetSelection(), 2)

    def test_summary_card_updates(self):
        self._open()
        self._fill()
        self.dialog.summary.set_text("Neue Zusammenfassung")
        self.assertEqual(self.dialog.summary.text, "Neue Zusammenfassung")

    def test_export_format_mapping(self):
        self._open()
        for label, fmt in (("Markdown", "markdown"), ("HTML", "html"), ("JSON", "json")):
            self.dialog.cb_format.SetStringSelection(label)
            self.assertEqual(
                {"Markdown": "markdown", "HTML": "html", "JSON": "json"}[
                    self.dialog.cb_format.GetStringSelection()
                ],
                fmt,
            )

    def test_model_list_populated(self):
        self._open()
        # Either models were found, or a clear placeholder is shown.
        value = self.dialog.cb_model.GetStringSelection() or self.dialog.cb_model.GetValue()
        self.assertTrue(value)


class TestSeverityLabels(unittest.TestCase):
    def test_labels_are_readable(self):
        for severity in Severity:
            self.assertTrue(severity_label(severity))

    def test_colours_differ_per_severity(self):
        theme = Theme(dark=False)
        colours = [tuple(severity_colour(theme, s)) for s in Severity]
        self.assertEqual(len(set(colours)), len(list(Severity)))


if __name__ == "__main__":
    unittest.main(verbosity=2)