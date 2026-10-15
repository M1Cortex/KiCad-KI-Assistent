"""wxPython user interface for the review dialog.

Built entirely in code (no .fbp / wxFormBuilder file) so the plugin stays a
single self-contained package. Only wx is required, which KiCad ships.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

import wx
import wx.lib.mixins.listctrl as listmix

from .report import write_report
from .reviewer import ReviewCancelled, SchematicReviewer
from .rules import Severity

if TYPE_CHECKING:  # pragma: no cover
    from .reviewer import ReviewResult

__all__ = ["ReviewDialog"]

_SEVERITY_COLORS = {
    Severity.ERROR: wx.Colour(211, 47, 47),
    Severity.WARNING: wx.Colour(245, 124, 0),
    Severity.INFO: wx.Colour(25, 118, 210),
    Severity.HINT: wx.Colour(97, 97, 97),
}

_SEVERITY_LABELS = {
    Severity.ERROR: "Fehler",
    Severity.WARNING: "Warnung",
    Severity.INFO: "Info",
    Severity.HINT: "Hinweis",
}

_REQUIREMENT_COLORS = {
    "erfuellt": wx.Colour(46, 125, 50),
    "teilweise": wx.Colour(245, 124, 0),
    "fehlt": wx.Colour(211, 47, 47),
    "unklar": wx.Colour(117, 117, 117),
}


_CATEGORY_LABELS = {
    "erc": "ERC",
    "netz": "Netz",
    "power": "Versorgung",
    "pin": "Pin",
    "bom": "Stückliste",
    "annotation": "Annotation",
    "hierarchie": "Hierarchie",
    "signal": "Signal",
    "schutz": "Schutz",
    "produktion": "Produktion",
    "test": "Test",
    "mechanik": "Mechanik",
    "general": "Allgemein",
}


class FindingList(wx.ListCtrl, listmix.ListCtrlAutoWidthMixin):
    """Flat list of findings, colour coded by severity."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            style=wx.LC_REPORT | wx.LC_VRULES | wx.LC_HRULES | wx.BORDER_SIMPLE,
        )
        listmix.ListCtrlAutoWidthMixin.__init__(self)
        self.InsertColumn(0, "Schwere", width=90)
        self.InsertColumn(1, "Regel", width=200)
        self.InsertColumn(2, "Befund", width=520)
        self.InsertColumn(3, "Ort", width=260)

    def populate(self, result: "ReviewResult", min_severity: Severity) -> None:
        self.DeleteAllItems()
        for finding in result.filtered(min_severity=min_severity):
            row = self.InsertItem(self.GetItemCount(), _SEVERITY_LABELS[finding.severity])
            self.SetItem(row, 1, finding.rule)
            self.SetItem(row, 2, finding.title)
            self.SetItem(row, 3, finding.location())
            self.SetItemTextColour(row, _SEVERITY_COLORS[finding.severity])
            self.SetItemData(row, int(finding.severity))

    def sort_by_severity(self) -> None:
        """Re-insert rows ordered by descending severity.

        wx's SortItems callback signature differs across wxPython builds, so
        the ordering is done in Python where it is unambiguous.
        """
        rows = [
            [
                self.GetItemText(r, c)
                for c in range(self.GetColumnCount())
            ]
            for r in range(self.GetItemCount())
        ]
        data = [self.GetItemData(r) for r in range(self.GetItemCount())]
        colours = [self.GetItemTextColour(r) for r in range(self.GetItemCount())]

        order = sorted(range(len(rows)), key=lambda i: -data[i])
        self.DeleteAllItems()
        for i in order:
            row = self.InsertItem(self.GetItemCount(), rows[i][0])
            for c in range(1, len(rows[i])):
                self.SetItem(row, c, rows[i][c])
            self.SetItemTextColour(row, colours[i])
            self.SetItemData(row, data[i])


class RequirementList(wx.ListCtrl, listmix.ListCtrlAutoWidthMixin):
    """Flat list of derived requirements."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            style=wx.LC_REPORT | wx.LC_VRULES | wx.LC_HRULES | wx.BORDER_SIMPLE,
        )
        listmix.ListCtrlAutoWidthMixin.__init__(self)
        self.InsertColumn(0, "ID", width=110)
        self.InsertColumn(1, "Status", width=90)
        self.InsertColumn(2, "Anforderung", width=460)
        self.InsertColumn(3, "Beleg", width=240)
        self.InsertColumn(4, "L·cke", width=240)

    def populate(self, result: "ReviewResult") -> None:
        self.DeleteAllItems()
        for req in result.requirements:
            row = self.InsertItem(self.GetItemCount(), req.id)
            self.SetItem(row, 1, req.status)
            self.SetItem(row, 2, req.requirement)
            self.SetItem(row, 3, req.evidence)
            self.SetItem(row, 4, req.gap)
            self.SetItemTextColour(row, _REQUIREMENT_COLORS.get(req.status, wx.Colour(0, 0, 0)))


class ReviewDialog(wx.Dialog):
    """Main dialog: run a review, browse findings and requirements, export."""

    def __init__(
        self,
        project_path: str,
        parent: wx.Window | None = None,
        use_ai: bool = True,
        instruction: str = "",
    ) -> None:
        style = wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        super().__init__(parent, title="KiCad AI Review", style=style)
        self.project_path = project_path
        self.result: ReviewResult | None = None
        self._use_ai = use_ai
        self._instruction = instruction
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None

        self.SetSize(wx.Size(1150, 760))
        self.SetMinSize(wx.Size(820, 520))
        self._build_ui()
        self._load_settings()

    # -- construction ----------------------------------------------------- #

    def _build_ui(self) -> None:
        root = wx.BoxSizer(wx.VERTICAL)

        # --- Projektzeile ---
        project_frame = wx.StaticBox(self, label="Projekt")
        project_box = wx.StaticBoxSizer(project_frame)
        self.project_label = wx.StaticText(
            project_frame, label=os.path.dirname(self.project_path) or "."
        )
        self.project_label.SetForegroundColour(wx.Colour(80, 80, 80))
        project_box.Add(self.project_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        project_box.AddStretchSpacer(1)
        root.Add(project_box, 0, wx.EXPAND | wx.ALL, 6)

        # --- Optionen ---
        options = wx.BoxSizer(wx.HORIZONTAL)
        self.cb_ai = wx.CheckBox(self, label="KI-Analyse verwenden")
        self.cb_ai.SetValue(self._use_ai)
        self.cb_ai.SetToolTip("Erfordert ein konfiguriertes Backend (Ollama oder API)")
        options.Add(self.cb_ai, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        options.Add(wx.StaticText(self, label="Mindestschweregrad:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.cb_severity = wx.ComboBox(
            self, choices=["Alle", "Fehler", "Warnung", "Info"], style=wx.CB_READONLY
        )
        self.cb_severity.SetSelection(0)
        options.Add(self.cb_severity, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        self.btn_review = wx.Button(self, label="Review starten")
        self.btn_review.Bind(wx.EVT_BUTTON, self.on_review)
        options.Add(self.btn_review, 0, wx.ALIGN_CENTER_VERTICAL)

        self.btn_cancel = wx.Button(self, label="Abbrechen")
        self.btn_cancel.Enable(False)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)
        options.Add(self.btn_cancel, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        root.Add(options, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # --- Zusatzfrage ---
        self.txt_instruction = wx.TextCtrl(self, value=self._instruction)
        self.txt_instruction.SetHint("Optionale Zusatzfrage an die KI, z. B. 'Pr·fe die I2C-Trennung'")
        root.Add(self.txt_instruction, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # --- Fortschritt ---
        self.gauge = wx.Gauge(self, range=100)
        self.gauge.Hide()

        # wx.Dialog has no status bar API, so use a read-only text control that
        # behaves the same but can live in the dialog sizer.
        self.status = wx.TextCtrl(self, value="Bereit", style=wx.TE_READONLY)

        # --- Notebook ---
        self.notebook = wx.Notebook(self)

        page_find = wx.Panel(self.notebook)
        sizer_find = wx.BoxSizer(wx.VERTICAL)
        self.lbl_summary = wx.StaticText(page_find, label="Noch kein Review ausgef·hrt.")
        self.lbl_summary.Wrap(1080)
        sizer_find.Add(self.lbl_summary, 0, wx.EXPAND | wx.ALL, 8)
        self.lst_findings = FindingList(page_find)
        sizer_find.Add(self.lst_findings, 1, wx.EXPAND | wx.ALL, 6)
        page_find.SetSizer(sizer_find)
        self.notebook.AddPage(page_find, "Befunde")

        page_req = wx.Panel(self.notebook)
        sizer_req = wx.BoxSizer(wx.VERTICAL)
        self.lst_requirements = RequirementList(page_req)
        sizer_req.Add(self.lst_requirements, 1, wx.EXPAND | wx.ALL, 6)
        page_req.SetSizer(sizer_req)
        self.notebook.AddPage(page_req, "Anforderungen")

        page_detail = wx.Panel(self.notebook)
        sizer_detail = wx.BoxSizer(wx.VERTICAL)
        self.txt_detail = wx.TextCtrl(page_detail, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        sizer_detail.Add(self.txt_detail, 1, wx.EXPAND | wx.ALL, 6)
        page_detail.SetSizer(sizer_detail)
        self.notebook.AddPage(page_detail, "Details")

        root.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 6)

        # --- Export ---
        export = wx.BoxSizer(wx.HORIZONTAL)
        self.cb_format = wx.ComboBox(self, choices=["Markdown", "HTML", "JSON"], style=wx.CB_READONLY)
        self.cb_format.SetSelection(0)
        export.Add(wx.StaticText(self, label="Export:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        export.Add(self.cb_format, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        self.btn_export = wx.Button(self, label="Bericht speichern")
        self.btn_export.Enable(False)
        self.btn_export.Bind(wx.EVT_BUTTON, self.on_export)
        export.Add(self.btn_export, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self.btn_open_mcp = wx.Button(self, label="MCP-Konfiguration anzeigen")
        self.btn_open_mcp.Bind(wx.EVT_BUTTON, self.on_show_mcp)
        export.Add(self.btn_open_mcp, 0, wx.ALIGN_CENTER_VERTICAL)
        export.AddStretchSpacer(1)

        self.btn_close = wx.Button(self, label="Schlie·Ÿen")
        self.btn_close.Bind(wx.EVT_BUTTON, self.on_close)
        export.Add(self.btn_close, 0)
        root.Add(export, 0, wx.EXPAND | wx.ALL, 8)

        root.Add(self.gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        root.Add(self.status, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(root)

        self.lst_findings.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_finding_selected)
        self.cb_severity.Bind(wx.EVT_COMBOBOX, self.on_severity_changed)

    def _load_settings(self) -> None:
        self._settings = wx.Config("kicad_ai_review")
        self._settings.SetPath("/")

    def _save_settings(self) -> None:
        try:
            self._settings.Write("use_ai", self.cb_ai.GetValue())
            self._settings.Write("min_severity", self.cb_severity.GetSelection())
        except Exception:  # noqa: BLE001 - settings are best effort
            pass

    # -- severity --------------------------------------------------------- #

    def _min_severity(self) -> Severity:
        mapping = [Severity.HINT, Severity.ERROR, Severity.WARNING, Severity.INFO]
        return mapping[max(0, self.cb_severity.GetSelection())]

    def on_severity_changed(self, _event: wx.CommandEvent) -> None:
        if self.result is not None:
            self.lst_findings.populate(self.result, self._min_severity())
            self.lst_findings.sort_by_severity()
            self._update_counts()

    def _update_counts(self) -> None:
        counts = self.result.counts() if self.result else {}
        shown = self.lst_findings.GetItemCount()
        self.status.ChangeValue(
            f"{counts.get('error', 0)} Fehler · {counts.get('warning', 0)} Warnungen · "
            f"{counts.get('info', 0)} Info · {counts.get('hint', 0)} Hinweise "
            f"(angezeigt: {shown})"
        )

    # -- review ----------------------------------------------------------- #

    def on_review(self, _event: wx.CommandEvent) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._save_settings()
        self._cancel.clear()
        self.btn_review.Enable(False)
        self.btn_cancel.Enable(True)
        self.btn_export.Enable(False)
        self.gauge.Show()
        self.gauge.SetValue(0)
        self.Layout()
        wx.Yield()

        use_ai = self.cb_ai.GetValue()
        instruction = self.txt_instruction.GetValue().strip()
        path = self.project_path

        def progress(message: str, percent: int) -> None:
            wx.CallAfter(self._on_progress, message, percent)

        def work() -> None:
            reviewer = SchematicReviewer()
            try:
                result = reviewer.review(
                    path,
                    use_ai=use_ai,
                    progress=progress,
                    extra_instruction=instruction,
                    cancel=self._cancel.is_set,
                )
                wx.CallAfter(self._on_done, result, None)
            except ReviewCancelled:
                wx.CallAfter(self._on_done, None, "Abgebrochen")
            except Exception as exc:  # noqa: BLE001
                import traceback

                wx.CallAfter(self._on_done, None, f"{exc}\n\n{traceback.format_exc()}")

        self._worker = threading.Thread(target=work, daemon=True, name="kicad-ai-review")
        self._worker.start()

    def _on_progress(self, message: str, percent: int) -> None:
        self.gauge.SetValue(max(0, min(100, percent)))
        self.status.ChangeValue(message)

    def _on_done(self, result: "ReviewResult | None", error: str | None) -> None:
        self.btn_review.Enable(True)
        self.btn_cancel.Enable(False)
        self.gauge.Hide()
        self.Layout()
        if result is None:
            self.status.ChangeValue(f"Review fehlgeschlagen: {error}")
            wx.MessageBox(f"Review fehlgeschlagen:\n\n{error}", "KiCad AI Review", wx.OK | wx.ICON_ERROR, self)
            return

        self.result = result
        self.lbl_summary.SetLabel(result.summary or "Keine Zusammenfassung.")
        self.lst_findings.populate(result, self._min_severity())
        self.lst_findings.sort_by_severity()
        self.lst_requirements.populate(result)
        self.btn_export.Enable(True)
        self._update_counts()

        counts = result.counts()
        status = f"Fertig in {result.duration_s:.1f}s"
        if result.llm_error:
            status += f" - KI: {result.llm_error}"
        self.status.ChangeValue(status)
        self.notebook.SetSelection(0)
        wx.MessageBox(
            f"{counts['error']} Fehler, {counts['warning']} Warnungen, "
            f"{counts['info']} Hinweise.",
            "Review abgeschlossen",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def on_cancel(self, _event: wx.CommandEvent) -> None:
        self._cancel.set()
        self.status.ChangeValue("Wird abgebrochen ...")

    # -- detail ----------------------------------------------------------- #

    def on_finding_selected(self, event: wx.ListEvent) -> None:
        row = event.GetIndex()
        self.notebook.SetSelection(2)
        wx.Yield()
        severity = self.lst_findings.GetItemData(row)
        for finding in (self.result.findings if self.result else []):
            if int(finding.severity) == severity:
                # match by rule to disambiguate equal severities
                if self.lst_findings.GetItemText(row, 1) == finding.rule:
                    self._show_detail(finding)
                    return
        wx.Yield()

    def _show_detail(self, finding) -> None:
        lines = [
            f"Regel      : {finding.rule}",
            f"Schwere    : {_SEVERITY_LABELS[finding.severity]} ({finding.severity.name})",
            f"Kategorie  : {_CATEGORY_LABELS.get(finding.category, finding.category)}",
            f"Quelle     : {'KI' if finding.source == 'ai' else 'Regel/ERC'}",
            f"Konfidenz  : {finding.confidence:.0%}",
            "",
            finding.title,
            "",
            finding.detail,
        ]
        if finding.location() != "-":
            lines += ["", f"Ort        : {finding.location()}"]
        if finding.fix:
            lines += ["", "EMPFEHLUNG:", finding.fix]
        self.txt_detail.SetValue("\n".join(lines))

    # -- export ----------------------------------------------------------- #

    def on_export(self, _event: wx.CommandEvent) -> None:
        if self.result is None:
            return
        fmt_map = {"Markdown": "markdown", "HTML": "html", "JSON": "json"}
        fmt = fmt_map[self.cb_format.GetStringSelection()]
        extensions = {"markdown": "md", "html": "html", "json": "json"}

        default = os.path.join(
            os.path.dirname(self.project_path),
            f"ai-review.{extensions[fmt]}",
        )
        with wx.FileDialog(
            self,
            "Bericht speichern",
            defaultFile=os.path.basename(default),
            defaultDir=os.path.dirname(default),
            wildcard=f"{fmt.upper()} (*.{extensions[fmt]})|*.{extensions[fmt]}",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            write_report(self.result, path, fmt=fmt, findings=self.result.filtered(self._min_severity()))
            self.status.ChangeValue(f"Bericht gespeichert: {path}")
            wx.MessageBox(f"Bericht gespeichert:\n{path}", "KiCad AI Review", wx.OK, self)
        except Exception as exc:  # noqa: BLE001
            wx.MessageBox(f"Speichern fehlgeschlagen:\n{exc}", "Fehler", wx.OK | wx.ICON_ERROR, self)

    # -- MCP helper ------------------------------------------------------- #

    def on_show_mcp(self, _event: wx.CommandEvent) -> None:
        project = os.path.dirname(self.project_path)
        python = sys.executable
        config = (
            "{\n"
            '  "mcpServers": {\n'
            '    "kicad": {\n'
            '      "command": "%s",\n'
            '      "args": ["-m", "kicad_ai.mcp_server", "--project", "%s"],\n'
            '      "env": {\n'
            '        "PYTHONPATH": "%s",\n'
            '        "KICAD_AI_BACKEND": "ollama",\n'
            '        "KICAD_AI_MODEL": "qwen2.5-coder:14b"\n'
            "      }\n"
            "    }\n"
            "  }\n"
            "}"
            % (
                python,
                project.replace("\\", "\\\\"),
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))).replace("\\", "\\\\"),
            )
        )
        dlg = wx.TextEntryDialog(self, "MCP-Client-Konfiguration kopieren:", "MCP", config)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                text = dlg.GetValue()
                if text and wx.TheClipboard.Open():
                    try:
                        # wxPython >= 3 needs the data wrapped in an object
                        # supporting the right format; a plain str raises
                        # TypeError: unexpected type 'str'.
                        data = wx.TextDataObject(text)
                        wx.TheClipboard.SetData(data)
                        self.status.ChangeValue("Konfiguration in die Zwischenablage kopiert")
                    finally:
                        wx.TheClipboard.Close()
        finally:
            dlg.Destroy()

    # -- close ------------------------------------------------------------ #

    def on_close(self, _event: wx.CommandEvent) -> None:
        self._save_settings()
        self._cancel.set()
        self.EndModal(wx.ID_OK)


#: Strong reference to the wx.App.
#:
#: ``wx.App(...)`` returns an object that owns the platform event loop. If the
#: only reference is a temporary, CPython collects it right away, the app is
#: shut down, and any later window creation raises
#: ``PyNoAppError: The wx.App object must be created first!``. KiCad runs
#: actions in a throwaway interpreter, so there is no other owner to keep it
#: alive.
_APP_REF: wx.App | None = None


def run(project_path: str, parent: wx.Window | None = None) -> int:
    """Show the review dialog for *project_path*.

    KiCad launches actions in a bare interpreter, so no ``wx.App`` exists yet
    and wx refuses to create any window without one.

    The app object is intentionally not destroyed afterwards: tearing it down
    while windows are still registered produces
    ``'UnregisterClass' failed ... class still has open windows`` and can take
    down the hosting KiCad editor. The interpreter exits right after anyway.
    """
    global _APP_REF

    if wx.GetApp() is None:
        # False: keep stdout/stderr intact so KiCad can report diagnostics.
        _APP_REF = wx.App(False)

    dialog = ReviewDialog(project_path, parent)
    try:
        dialog.ShowModal()
    finally:
        dialog.Destroy()
    return wx.ID_OK