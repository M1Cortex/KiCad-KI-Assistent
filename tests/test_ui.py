"""Headless smoke test for the wx dialog.

Builds the dialog without showing it and fills it with a real review result,
which catches sizer/parenting mistakes that only surface at construction.
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

DEMO = r"C:\Program Files\KiCad\10.0\share\kicad\demos\simulation\amplifier-ac\amplifier-ac.kicad_sch"
_app = wx.App(False)


class TestDialog(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(DEMO):
            self.skipTest("KiCad demo not available")
        self.dialog = None

    def tearDown(self):
        if self.dialog is not None:
            self.dialog.Destroy()

    def test_dialog_constructs(self):
        from kicad_ai.ui import ReviewDialog

        self.dialog = ReviewDialog(DEMO, None, use_ai=False)
        self.assertTrue(self.dialog.notebook.GetPageCount() >= 3)
        self.assertIsNotNone(self.dialog.status)

    def test_populates_from_review(self):
        from kicad_ai.ui import ReviewDialog

        reviewer = SchematicReviewer()
        design = SchematicLoader(KicadCli()).load(DEMO)
        findings, _ = reviewer.run_rules(design)
        result = ReviewResult(design=design, findings=findings, summary="Dialogtest")

        self.dialog = ReviewDialog(DEMO, None, use_ai=False)
        self.dialog.lbl_summary.SetLabel(result.summary)
        self.dialog.lst_findings.populate(result, self.dialog._min_severity())
        self.dialog.lst_findings.sort_by_severity()
        self.dialog.lst_requirements.populate(result)
        self.dialog._update_counts()

        self.assertEqual(self.dialog.lst_findings.GetItemCount(), len(findings))
        self.assertIn("Fehler", self.dialog.status.GetValue())

    def test_severity_filter(self):
        from kicad_ai.ui import ReviewDialog

        reviewer = SchematicReviewer()
        design = SchematicLoader(KicadCli()).load(DEMO)
        findings, _ = reviewer.run_rules(design)
        result = ReviewResult(design=design, findings=findings, summary="x")

        self.dialog = ReviewDialog(DEMO, None, use_ai=False)
        self.dialog.lst_findings.populate(result, Severity.ERROR)
        errors_only = self.dialog.lst_findings.GetItemCount()
        expected = len([f for f in findings if f.severity >= Severity.ERROR])
        self.assertEqual(errors_only, expected)

    def test_detail_rendering(self):
        from kicad_ai.ui import ReviewDialog

        reviewer = SchematicReviewer()
        design = SchematicLoader(KicadCli()).load(DEMO)
        findings, _ = reviewer.run_rules(design)
        result = ReviewResult(design=design, findings=findings, summary="x")

        self.dialog = ReviewDialog(DEMO, None, use_ai=False)
        self.dialog.result = result
        self.dialog._show_detail(findings[0])
        text = self.dialog.txt_detail.GetValue()
        self.assertIn("Regel", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)