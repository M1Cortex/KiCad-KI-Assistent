"""Modernised wx interface for the schematic review.

The layout is built in code so the plugin stays one self-contained package.
Design goals:

* flat surfaces with consistent spacing instead of nested native frames
* severity communicated by colour *and* by an explicit text pill, so it also
  works for users who cannot rely on colour alone
* the AI model selectable directly in the window, without leaving the review
* readable in light and dark mode
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

import wx

from .panels import Card, StatRow, SummaryCard
from .report import write_report
from .reviewer import ReviewCancelled, SchematicReviewer
from .rules import Severity
from .theme import Theme, detect_dark, severity_colour, severity_label, status_colour, status_label
from .widgets import Row, ScrollingList

if TYPE_CHECKING:  # pragma: no cover
    from .reviewer import ReviewResult

__all__ = ["ReviewDialog", "run"]

_SEVERITY_CHOICES = [
    ("Alle", Severity.HINT),
    ("Nur Fehler", Severity.ERROR),
    ("Fehler + Warnungen", Severity.WARNING),
    ("Ab Info", Severity.INFO),
]

_CATEGORY_LABELS = {
    "erc": "ERC", "netz": "Netz", "power": "Versorgung", "pin": "Pin",
    "bom": "Stückliste", "annotation": "Annotation", "hierarchie": "Hierarchie",
    "signal": "Signal", "schutz": "Schutz", "produktion": "Produktion",
    "test": "Test", "mechanik": "Mechanik", "general": "Allgemein",
}

# Strong separators (4 semicolons) are easy to misread in code.
WIDTH_FINDING_TITLE = 460
WIDTH_RULE = 240
WIDTH_LOCATION = 300


class ReviewDialog(wx.Dialog):
    """Main dialog: run a review, browse findings, export."""

    def __init__(
        self,
        project_path: str,
        parent: wx.Window | None = None,
        use_ai: bool = True,
        instruction: str = "",
    ) -> None:
        super().__init__(
            parent,
            title="KiCad KI-Assistent",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.project_path = project_path
        self.result: ReviewResult | None = None
        self._use_ai = use_ai
        self._instruction = instruction
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._settings = wx.Config("kicad_ai_review")

        self.theme = Theme(dark=detect_dark())
        self.SetBackgroundColour(self.theme.bg)
        self.SetSize(wx.Size(1180, 780))
        self.SetMinSize(wx.Size(860, 560))

        self._build_ui()
        self._load_settings()

    # -- construction ---------------------------------------------------- #

    def _build_ui(self) -> None:
        theme = self.theme
        root = wx.BoxSizer(wx.VERTICAL)

        root.Add(self._build_header(), 0, wx.EXPAND | wx.ALL, 16)
        root.Add(self._build_stats(), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)
        root.Add(self._build_toolbar(), 0, wx.EXPAND | wx.ALL, 16)
        self.summary = SummaryCard(self, theme)
        root.Add(self.summary, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)

        self.notebook = wx.Notebook(self)
        self.notebook.SetBackgroundColour(theme.bg)
        self.notebook.SetOwnBackgroundColour(theme.bg)

        # Findings page
        page = wx.Panel(self.notebook)
        page.SetBackgroundColour(theme.bg)
        page.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.lbl_finding_count = wx.StaticText(page, label="")
        self.lbl_finding_count.SetForegroundColour(theme.text_faint)
        self.lbl_finding_count.SetFont(theme.font(size=8))
        sizer.Add(self.lbl_finding_count, 0, wx.LEFT | wx.BOTTOM, 6)
        self.lst_findings = ScrollingList(page, theme, on_activate=self.on_finding_activated)
        sizer.Add(self.lst_findings, 1, wx.EXPAND)
        page.SetSizer(sizer)
        self.page_findings = page
        self.notebook.AddPage(page, "Befunde")

        # Requirements page
        page = wx.Panel(self.notebook)
        page.SetBackgroundColour(theme.bg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.lbl_req_count = wx.StaticText(page, label="")
        self.lbl_req_count.SetForegroundColour(theme.text_faint)
        self.lbl_req_count.SetFont(theme.font(size=8))
        sizer.Add(self.lbl_req_count, 0, wx.LEFT | wx.BOTTOM, 6)
        self.lst_requirements = ScrollingList(page, theme, on_activate=self.on_requirement_activated)
        sizer.Add(self.lst_requirements, 1, wx.EXPAND)
        page.SetSizer(sizer)
        self.notebook.AddPage(page, "Anforderungen")

        # Details page
        page = wx.Panel(self.notebook)
        page.SetBackgroundColour(theme.bg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.txt_detail = wx.TextCtrl(
            page,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.HSCROLL,
        )
        self.txt_detail.SetBackgroundColour(theme.surface_alt)
        self.txt_detail.SetForegroundColour(theme.text)
        self.txt_detail.SetFont(theme.font(size=9, mono=True))
        sizer.Add(self.txt_detail, 1, wx.EXPAND | wx.ALL, 4)
        page.SetSizer(sizer)
        self.notebook.AddPage(page, "Details")

        root.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 16)
        root.Add(self._build_footer(), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)

        self.SetSizer(root)

        self.cb_severity.Bind(wx.EVT_COMBOBOX, self.on_severity_changed)

    def _build_header(self) -> wx.Sizer:
        theme = self.theme
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        badge = wx.StaticText(self, label="KI")
        badge.SetForegroundColour(wx.Colour(255, 255, 255))
        badge.SetBackgroundColour(theme.accent)
        badge.SetFont(theme.font(size=11, bold=True))
        badge.SetMinSize(wx.Size(34, 34))
        sizer.Add(badge, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        text = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="Schaltplan-Review")
        title.SetForegroundColour(theme.text)
        title.SetFont(theme.font(size=15, bold=True))
        text.Add(title, 0)

        folder = project_dir(self.project_path)
        self.project_label = wx.StaticText(self, label=folder)
        self.project_label.SetForegroundColour(theme.text_dim)
        self.project_label.SetFont(theme.font(size=9))
        text.Add(self.project_label, 0, wx.TOP, 2)
        sizer.Add(text, 1)
        return sizer

    def _build_stats(self) -> wx.Sizer:
        self.stat_row = StatRow(self, self.theme)
        self.stat_row.set_stats([])
        return self.stat_row

    def _build_toolbar(self) -> wx.Sizer:
        theme = self.theme
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_review = wx.Button(self, label="Review starten")
        self.btn_review.SetFont(theme.font(size=10, bold=True))
        self.btn_review.Bind(wx.EVT_BUTTON, self.on_review)
        sizer.Add(self.btn_review, 0, wx.RIGHT, 16)

        self.cb_ai = wx.CheckBox(self, label="KI-Analyse")
        self.cb_ai.SetValue(self._use_ai)
        self.cb_ai.SetForegroundColour(theme.text_dim)
        self.cb_ai.SetToolTip("Erfordert ein konfiguriertes Backend (Ollama oder API)")
        self.cb_ai.Bind(wx.EVT_CHECKBOX, self.on_ai_toggled)
        sizer.Add(self.cb_ai, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        lbl = wx.StaticText(self, label="Modell")
        lbl.SetForegroundColour(theme.text_faint)
        lbl.SetFont(theme.font(size=8))
        sizer.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self.cb_model = wx.ComboBox(self, style=wx.CB_READONLY, size=wx.Size(230, -1))
        self.cb_model.Bind(wx.EVT_COMBOBOX, self.on_model_changed)
        sizer.Add(self.cb_model, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        lbl = wx.StaticText(self, label="Anzeige")
        lbl.SetForegroundColour(theme.text_faint)
        lbl.SetFont(theme.font(size=8))
        sizer.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self.cb_severity = wx.ComboBox(self, choices=[c[0] for c in _SEVERITY_CHOICES], style=wx.CB_READONLY)
        self.cb_severity.SetSelection(0)
        sizer.Add(self.cb_severity, 0, wx.ALIGN_CENTER_VERTICAL)

        sizer.AddStretchSpacer(1)

        self.btn_cancel = wx.Button(self, label="Abbrechen")
        self.btn_cancel.Enable(False)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)
        sizer.Add(self.btn_cancel, 0, wx.ALIGN_CENTER_VERTICAL)
        return sizer

    def _build_footer(self) -> wx.Sizer:
        theme = self.theme
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.cb_format = wx.ComboBox(self, choices=["Markdown", "HTML", "JSON"], style=wx.CB_READONLY)
        self.cb_format.SetSelection(0)
        sizer.Add(self.cb_format, 0, wx.RIGHT, 8)

        self.btn_export = wx.Button(self, label="Bericht speichern")
        self.btn_export.Enable(False)
        self.btn_export.Bind(wx.EVT_BUTTON, self.on_export)
        sizer.Add(self.btn_export, 0, wx.RIGHT, 8)

        self.btn_mcp = wx.Button(self, label="MCP-Konfiguration")
        self.btn_mcp.Bind(wx.EVT_BUTTON, self.on_show_mcp)
        sizer.Add(self.btn_mcp, 0)

        sizer.AddStretchSpacer(1)

        self.status = wx.TextCtrl(self, value="Bereit", style=wx.TE_READONLY)
        self.status.SetBackgroundColour(theme.surface_alt)
        self.status.SetForegroundColour(theme.text_dim)
        self.status.SetFont(theme.font(size=8))
        sizer.Add(self.status, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 12)

        self.btn_close = wx.Button(self, label="Schließen")
        self.btn_close.Bind(wx.EVT_BUTTON, self.on_close)
        sizer.Add(self.btn_close, 0, wx.LEFT, 8)
        return sizer

    # -- settings -------------------------------------------------------- #

    def _load_settings(self) -> None:
        try:
            self._settings.SetPath("/")
            if self._settings.HasEntry("use_ai"):
                self.cb_ai.SetValue(bool(self._settings.Read("use_ai", 1)))
            if self._settings.HasEntry("min_severity"):
                index = int(self._settings.Read("min_severity", 0))
                self.cb_severity.SetSelection(max(0, min(index, len(_SEVERITY_CHOICES) - 1)))
        except Exception:  # noqa: BLE001 - settings are best effort
            pass
        self._load_models()

    def _save_settings(self) -> None:
        try:
            self._settings.Write("use_ai", self.cb_ai.GetValue())
            self._settings.Write("min_severity", self.cb_severity.GetSelection())
        except Exception:  # noqa: BLE001
            pass

    def _load_models(self) -> None:
        """Populate the model dropdown from the configured backend."""
        from .config import AIConfig
        from .llm import build_client

        self.cb_model.Clear()
        try:
            config = AIConfig.load()
            client = build_client(config.to_llm_config())
            names = client.models()
        except Exception as exc:  # noqa: BLE001 - backend may be offline
            print(f"[kicad-ai] Modelliste nicht abrufbar: {exc}", file=sys.stderr)
            self.cb_model.SetValue("— kein Backend —")
            return

        if not names:
            self.cb_model.SetValue("— keine Modelle —")
            return

        self.cb_model.Set(names)
        current = config.model
        if current in names:
            self.cb_model.SetSelection(names.index(current))
        else:
            best = client.recommend() if hasattr(client, "recommend") else names[0]
            if best in names:
                self.cb_model.SetSelection(names.index(best))
                self._auto_model = best

    def on_ai_toggled(self, event: wx.CommandEvent) -> None:
        enabled = self.cb_ai.GetValue()
        self.cb_model.Enable(enabled)

    def on_model_changed(self, event: wx.CommandEvent) -> None:
        from .config import AIConfig

        name = self.cb_model.GetStringSelection()
        if not name:
            return
        try:
            config = AIConfig.load()
            config.model = name
            config.save()
            self.status.ChangeValue(f"Modell gesetzt: {name}")
        except Exception as exc:  # noqa: BLE001
            self.status.ChangeValue(f"Modell konnte nicht gespeichert werden: {exc}")

    # -- severity -------------------------------------------------------- #

    def _min_severity(self) -> Severity:
        index = max(0, self.cb_severity.GetSelection())
        return _SEVERITY_CHOICES[index][1]

    def on_severity_changed(self, _event: wx.CommandEvent) -> None:
        if self.result is not None:
            self._refresh_findings()
            self._update_counts()

    # -- population ------------------------------------------------------ #

    def _refresh_findings(self) -> None:
        if self.result is None:
            return
        theme = self.theme
        rows = []
        for finding in self.result.filtered(min_severity=self._min_severity()):
            category = _CATEGORY_LABELS.get(finding.category, finding.category)
            rows.append(
                Row(
                    primary=finding.title,
                    secondary=finding.rule,
                    tertiary=finding.detail,
                    badge=category,
                    colour=severity_colour(theme, finding.severity),
                    payload=finding,
                    location=finding.location(),
                )
            )
        rows.sort(key=lambda r: -int(r.payload.severity))
        self.lst_findings.set_rows(rows)
        self.lbl_finding_count.SetLabel(f"{len(rows)} BEFUND(E)")

    def _refresh_requirements(self) -> None:
        theme = self.theme
        rows = []
        for req in (self.result.requirements if self.result else []):
            rows.append(
                Row(
                    primary=req.requirement,
                    secondary=req.id,
                    tertiary=req.gap or req.evidence,
                    badge=status_label(req.status),
                    colour=status_colour(theme, req.status),
                    payload=req,
                )
            )
        self.lst_requirements.set_rows(rows)
        self.lbl_req_count.SetLabel(f"{len(rows)} ANFORDERUNG(EN)")

    def _update_stats(self) -> None:
        stats = self.result.design.stats()
        counts = self.result.counts()
        theme = self.theme
        self.stat_row.set_stats(
            [
                (str(stats["components"]), "Bauteile", None),
                (str(stats["sheets"]), "Blätter", None),
                (str(stats["nets"]), "Netze", None),
                (str(stats["pins"]), "Pins", None),
                (
                    str(counts["error"]),
                    "Fehler",
                    severity_colour(theme, Severity.ERROR) if counts["error"] else None,
                ),
                (
                    str(counts["warning"]),
                    "Warnungen",
                    severity_colour(theme, Severity.WARNING) if counts["warning"] else None,
                ),
            ]
        )

    def _update_counts(self) -> None:
        if self.result is None:
            return
        counts = self.result.counts()
        shown = self.lst_findings.GetItemCount()
        parts = [f"{counts.get(k, 0)} {severity_label(Severity[k.upper()])}" for k in ("error", "warning", "info", "hint")]
        self.status.ChangeValue(f"{' · '.join(parts)}   →   {shown} angezeigt")

    # -- review ---------------------------------------------------------- #

    def on_review(self, _event: wx.CommandEvent) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._save_settings()
        self._cancel.clear()
        self.btn_review.Enable(False)
        self.btn_cancel.Enable(True)
        self.btn_export.Enable(False)
        self.status.ChangeValue("Review läuft …")
        wx.Yield()

        use_ai = self.cb_ai.GetValue()
        instruction = self.txt_instruction.GetValue().strip() if hasattr(self, "txt_instruction") else ""
        instruction = self._instruction or instruction
        path = self.project_path

        def progress(message: str, percent: int) -> None:
            wx.CallAfter(self.status.ChangeValue, f"{message}  ({percent}%)")

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

    def on_cancel(self, _event: wx.CommandEvent) -> None:
        self._cancel.set()
        self.status.ChangeValue("Wird abgebrochen …")

    def _on_done(self, result: "ReviewResult | None", error: str | None) -> None:
        self.btn_review.Enable(True)
        self.btn_cancel.Enable(False)

        if result is None:
            self.status.ChangeValue(f"Review fehlgeschlagen: {error}")
            wx.MessageBox(
                f"Review fehlgeschlagen:\n\n{error}",
                "KiCad KI-Assistent",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        self.result = result
        self._update_stats()
        self._refresh_findings()
        self._refresh_requirements()
        self.btn_export.Enable(True)

        worst = Severity.HINT
        for finding in result.findings:
            worst = max(worst, finding.severity)
        self.summary.set_text(
            result.summary,
            severity_colour(self.theme, worst) if result.findings else self.theme.accent,
        )
        self._update_counts()

        counts = result.counts()
        status = f"Fertig in {result.duration_s:.1f} s"
        if result.llm_used:
            status += f"  ·  KI: {result.llm_model}"
        elif result.llm_error:
            status += f"  ·  KI: {result.llm_error}"
        self.status.ChangeValue(status)
        self.notebook.SetSelection(0)

        if counts["error"]:
            icon = wx.ICON_ERROR
        elif counts["warning"]:
            icon = wx.ICON_WARNING
        else:
            icon = wx.ICON_INFORMATION
        wx.MessageBox(
            f"{counts['error']} Fehler, {counts['warning']} Warnungen, "
            f"{counts['info']} Hinweise.",
            "Review abgeschlossen",
            wx.OK | icon,
            self,
        )

    # -- detail ---------------------------------------------------------- #

    def on_finding_activated(self, index: int) -> None:
        """Double-click or Enter on a finding opens its details."""
        payload = self.lst_findings.row_payload(index)
        if payload is not None:
            self.notebook.SetSelection(2)
            self._show_detail(payload)

    def on_requirement_activated(self, index: int) -> None:
        req = self.lst_requirements.row_payload(index)
        if req is None:
            return
        lines = [
            f"ID         : {req.id}",
            f"Status     : {status_label(req.status)}",
            "",
            req.requirement,
        ]
        if req.evidence:
            lines += ["", "BELEG:", req.evidence]
        if req.gap:
            lines += ["", "LÜCKE:", req.gap]
        self.notebook.SetSelection(2)
        self.txt_detail.SetValue("\n".join(lines))

    def _show_detail(self, finding) -> None:
        theme = self.theme
        category = _CATEGORY_LABELS.get(finding.category, finding.category)
        lines = [
            f"Regel       : {finding.rule}",
            f"Schwere     : {severity_label(finding.severity)} ({finding.severity.name})",
            f"Kategorie   : {category}",
            f"Quelle      : {'KI' if finding.source == 'ai' else 'Regel / ERC'}",
            f"Konfidenz   : {finding.confidence:.0%}",
            "",
            finding.title,
            "",
            finding.detail,
        ]
        location = finding.location()
        if location != "-":
            lines += ["", f"ORT: {location}"]
        if finding.fix:
            lines += ["", "EMPFEHLUNG:", finding.fix]
        self.txt_detail.SetValue("\n".join(lines))

    # -- export ---------------------------------------------------------- #

    def on_export(self, _event: wx.CommandEvent) -> None:
        if self.result is None:
            return
        fmt = {"Markdown": "markdown", "HTML": "html", "JSON": "json"}[
            self.cb_format.GetStringSelection()
        ]
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
            write_report(
                self.result,
                path,
                fmt=fmt,
                findings=self.result.filtered(self._min_severity()),
            )
            self.status.ChangeValue(f"Bericht gespeichert: {path}")
            wx.MessageBox(f"Bericht gespeichert:\n{path}", "KiCad KI-Assistent", wx.OK, self)
        except Exception as exc:  # noqa: BLE001
            wx.MessageBox(f"Speichern fehlgeschlagen:\n{exc}", "Fehler", wx.OK | wx.ICON_ERROR, self)

    # -- MCP ------------------------------------------------------------- #

    def on_show_mcp(self, _event: wx.CommandEvent) -> None:
        project = os.path.dirname(self.project_path)
        package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config = (
            "{\n"
            '  "mcpServers": {\n'
            '    "kicad": {\n'
            f'      "command": "{sys.executable}",\n'
            '      "args": ["-m", "kicad_ai.mcp_server", "--project", '
            f'"{project.replace(chr(92), chr(92) * 2)}"],\n'
            '      "env": {\n'
            f'        "PYTHONPATH": "{package_root.replace(chr(92), chr(92) * 2)}"\n'
            "      }\n"
            "    }\n"
            "  }\n"
            "}"
        )
        dlg = wx.TextEntryDialog(self, "MCP-Client-Konfiguration kopieren:", "MCP", config)
        try:
            if dlg.ShowModal() == wx.ID_OK and dlg.GetValue():
                if wx.TheClipboard.Open():
                    try:
                        wx.TheClipboard.SetData(wx.TextDataObject(dlg.GetValue()))
                        self.status.ChangeValue("Konfiguration in die Zwischenablage kopiert")
                    finally:
                        wx.TheClipboard.Close()
        finally:
            dlg.Destroy()

    # -- close ----------------------------------------------------------- #

    def on_close(self, _event: wx.CommandEvent) -> None:
        self._save_settings()
        self._cancel.set()
        self.EndModal(wx.ID_OK)


def project_dir(path: str) -> str:
    """Directory shown in the header."""
    return os.path.dirname(os.path.abspath(path)) or os.path.abspath(path)


_APP_REF: wx.App | None = None


def ensure_app() -> wx.App:
    """Return the wx application, creating it on first use.

    KiCad launches actions in a bare interpreter, so no ``wx.App`` exists yet.
    The instance is kept in a module global: a temporary would be garbage
    collected immediately and every later window creation would raise
    ``PyNoAppError``.
    """
    global _APP_REF
    app = wx.GetApp()
    if app is None:
        # False keeps stdout/stderr intact so KiCad can report diagnostics.
        app = wx.App(False)
        _APP_REF = app
    return app


def run(project_path: str, parent: wx.Window | None = None) -> int:
    """Show the review dialog for *project_path*."""
    ensure_app()

    dialog = ReviewDialog(project_path, parent)
    try:
        dialog.Centre(wx.BOTH)
        dialog.ShowModal()
    finally:
        dialog.Destroy()
    return wx.ID_OK