"""Report rendering in text, Markdown, HTML and JSON."""

from __future__ import annotations

import html
import os
from datetime import datetime
from typing import TYPE_CHECKING

from .rules import Finding, Severity

if TYPE_CHECKING:  # pragma: no cover
    from .reviewer import ReviewResult

__all__ = ["render_report", "write_report", "SEVERITY_ICONS"]

SEVERITY_ICONS = {
    Severity.ERROR: "[FEHLER]",
    Severity.WARNING: "[WARN]",
    Severity.INFO: "[INFO]",
    Severity.HINT: "[HINW]",
}

_SEVERITY_HTML = {
    Severity.ERROR: ("#d32f2f", "Fehler"),
    Severity.WARNING: ("#f57c00", "Warnung"),
    Severity.INFO: ("#1976d2", "Info"),
    Severity.HINT: ("#616161", "Hinweis"),
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


def _category(sev: Severity) -> str:
    return _CATEGORY_LABELS.get(sev, "")


def _group(findings: list[Finding]) -> dict[str, list[Finding]]:
    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.category, []).append(finding)
    return grouped


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #

def _render_text(result: "ReviewResult", findings: list[Finding], requirements_only: bool) -> str:
    lines: list[str] = []
    design = result.design
    stats = design.stats()
    counts = result.counts()

    lines.append("=" * 78)
    lines.append("  KiCad AI Review - Schaltplanprüfung")
    lines.append("=" * 78)
    lines.append(f"Projekt : {os.path.basename(design.root_file)}")
    lines.append(f"Pfad    : {os.path.dirname(design.root_file)}")
    if design.title_block.get("title"):
        lines.append(f"Titel   : {design.title_block['title']}")
    lines.append("")
    lines.append(
        f"Umfang  : {stats['components']} Bauteile, {stats['sheets']} Blätter, "
        f"{stats['nets']} Netze, {stats['pins']} Pin-Verbindungen"
    )
    lines.append(
        f"Befunde : {counts['error']} Fehler, {counts['warning']} Warnungen, "
        f"{counts['info']} Info, {counts['hint']} Hinweise"
    )
    lines.append(f"Dauer   : {result.duration_s:.1f}s")

    if result.llm_used:
        lines.append(f"KI      : {result.llm_backend} / {result.llm_model}")
    elif result.llm_error:
        lines.append(f"KI      : nicht verwendet ({result.llm_error})")

    lines.append("")
    lines.append("-" * 78)
    lines.append("ZUSAMMENFASSUNG")
    lines.append("-" * 78)
    for chunk in _wrap(result.summary or "Keine Zusammenfassung vorhanden.", 76):
        lines.append(chunk)

    if result.requirements or requirements_only:
        lines.append("")
        lines.append("-" * 78)
        lines.append("ANFORDERUNGEN")
        lines.append("-" * 78)
        reqs = result.requirements
        if not reqs:
            lines.append("(keine - KI-Analyse war deaktiviert)")
        else:
            status_icon = {
                "erfuellt": "[OK]     ",
                "teilweise": "[TEILW.] ",
                "fehlt": "[FEHLT]  ",
                "unklar": "[UNKLAR] ",
            }
            for req in reqs:
                lines.append(f"{status_icon.get(req.status, '[?]')} {req.id}: {req.requirement}")
                if req.evidence:
                    for chunk in _wrap(f"Beleg: {req.evidence}", 74, indent="      "):
                        lines.append(chunk)
                if req.gap:
                    for chunk in _wrap(f"Lücke: {req.gap}", 74, indent="      "):
                        lines.append(chunk)
                lines.append("")

    if result.questions:
        lines.append("")
        lines.append("-" * 78)
        lines.append("RÜCKFRAGEN AN DEN ENTWICKLER")
        lines.append("-" * 78)
        for question in result.questions:
            for i, chunk in enumerate(_wrap(question, 76, indent="  - ")):
                lines.append(chunk)

    if not requirements_only:
        lines.append("")
        lines.append("-" * 78)
        lines.append("BEFUNDE")
        lines.append("-" * 78)
        if not findings:
            lines.append("Keine Befunde.")
        else:
            for category, items in sorted(_group(findings).items()):
                label = _CATEGORY_LABELS.get(category, category)
                lines.append("")
                lines.append(f"[{label.upper()}]  {len(items)} Befund(e)")
                for finding in items:
                    lines.append("")
                    lines.append(
                        f"  {SEVERITY_ICONS[finding.severity]} {finding.rule} - {finding.title}"
                    )
                    for chunk in _wrap(finding.detail, 72, indent="      "):
                        lines.append(chunk)
                    location = finding.location()
                    if location != "-":
                        lines.append(f"      Ort      : {location}")
                    if finding.fix:
                        for chunk in _wrap(f"Empfehlung : {finding.fix}", 72, indent="      "):
                            lines.append(chunk)

    lines.append("")
    lines.append("=" * 78)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #

def _render_markdown(result: "ReviewResult", findings: list[Finding], requirements_only: bool) -> str:
    design = result.design
    stats = design.stats()
    counts = result.counts()
    sev_badge = {
        Severity.ERROR: "🔴",
        Severity.WARNING: "🟠",
        Severity.INFO: "🔵",
        Severity.HINT: "⚪",
    }

    out: list[str] = []
    out.append("# KiCad Schaltplan-Review")
    out.append("")
    out.append(f"**Projekt:** `{os.path.basename(design.root_file)}`  ")
    out.append(f"**Pfad:** `{os.path.dirname(design.root_file)}`  ")
    if design.title_block.get("title"):
        out.append(f"**Titel:** {design.title_block['title']}  ")
    out.append(f"**Datum:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
    out.append(f"**Dauer:** {result.duration_s:.1f} s")
    out.append("")

    out.append("## Übersicht")
    out.append("")
    out.append("| Kennzahl | Wert |")
    out.append("|---|---|")
    out.append(f"| Bauteile | {stats['components']} |")
    out.append(f"| Blätter | {stats['sheets']} |")
    out.append(f"| Netze | {stats['nets']} |")
    out.append(f"| Pin-Verbindungen | {stats['pins']} |")
    out.append(f"| Fehler | {counts['error']} |")
    out.append(f"| Warnungen | {counts['warning']} |")
    out.append(f"| Hinweise | {counts['info'] + counts['hint']} |")
    out.append("")

    if result.llm_used:
        out.append(f"> KI-Analyse: **{result.llm_backend}** / `{result.llm_model}`")
        out.append("")
    elif result.llm_error:
        out.append(f"> KI-Analyse nicht ausgeführt: {result.llm_error}")
        out.append("")

    out.append("## Zusammenfassung")
    out.append("")
    out.append(result.summary or "_(keine)_")
    out.append("")

    if result.requirements:
        out.append("## Abgeleitete Anforderungen")
        out.append("")
        out.append("| ID | Anforderung | Status | Beleg | Lücke |")
        out.append("|---|---|---|---|---|")
        icon = {"erfuellt": "✅ erfüllt", "teilweise": "🟡 teilweise", "fehlt": "🔴 fehlt", "unklar": "⚪ unklar"}
        for req in result.requirements:
            out.append(
                f"| {req.id} | {_md(req.requirement)} | {icon.get(req.status, req.status)} "
                f"| {_md(req.evidence)} | {_md(req.gap)} |"
            )
        out.append("")

    if result.questions:
        out.append("## Rückfragen")
        out.append("")
        for question in result.questions:
            out.append(f"- {question}")
        out.append("")

    if not requirements_only:
        out.append("## Befunde")
        out.append("")
        if not findings:
            out.append("_Keine Befunde._")
        else:
            for category, items in sorted(_group(findings).items()):
                label = _CATEGORY_LABELS.get(category, category)
                out.append(f"### {label}")
                out.append("")
                for finding in items:
                    out.append(f"- {sev_badge[finding.severity]} **{finding.rule}** — {finding.title}")
                    if finding.detail:
                        out.append(f"  - {finding.detail}")
                    location = finding.location()
                    if location != "-":
                        out.append(f"  - Ort: `{location}`")
                    if finding.fix:
                        out.append(f"  - **Empfehlung:** {finding.fix}")
                out.append("")

    return "\n".join(out).rstrip() + "\n"


def _md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

def _render_html(result: "ReviewResult", findings: list[Finding], requirements_only: bool) -> str:
    design = result.design
    stats = design.stats()
    counts = result.counts()
    esc = html.escape

    badge_css = {
        Severity.ERROR: "background:#d32f2f;color:#fff",
        Severity.WARNING: "background:#f57c00;color:#fff",
        Severity.INFO: "background:#1976d2;color:#fff",
        Severity.HINT: "background:#757575;color:#fff",
    }

    parts: list[str] = []
    parts.append("""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<title>KiCad Schaltplan-Review</title>
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:0;padding:2rem;
      background:#fafafa;color:#222;line-height:1.5}
 h1{margin-top:0}
 .cards{display:flex;gap:1rem;flex-wrap:wrap;margin:1.5rem 0}
 .card{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:1rem 1.5rem;min-width:120px}
 .card .n{font-size:1.8rem;font-weight:600}
 .card .l{color:#666;font-size:.85rem;text-transform:uppercase;letter-spacing:.04em}
 .finding{background:#fff;border-left:4px solid #ccc;border-radius:4px;padding:.75rem 1rem;margin:.6rem 0}
 .finding.error{border-left-color:#d32f2f}
 .finding.warning{border-left-color:#f57c00}
 .finding.info{border-left-color:#1976d2}
 .finding.hint{border-left-color:#757575}
 .badge{display:inline-block;padding:.1rem .5rem;border-radius:3px;font-size:.75rem;font-weight:600;
        text-transform:uppercase;letter-spacing:.03em;margin-right:.5rem}
 code{background:#eee;padding:.1rem .3rem;border-radius:3px;font-size:.9em}
 .meta{color:#666;font-size:.85rem}
 table{border-collapse:collapse;width:100%;background:#fff}
 th,td{border:1px solid #e0e0e0;padding:.4rem .6rem;text-align:left;font-size:.9rem}
 th{background:#f0f0f0}
</style></head><body>""")

    parts.append("<h1>KiCad Schaltplan-Review</h1>")
    parts.append(
        f'<p class="meta"><strong>{esc(os.path.basename(design.root_file))}</strong><br>'
        f"{esc(os.path.dirname(design.root_file))}<br>"
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')} &middot; {result.duration_s:.1f} s</p>"
    )

    parts.append('<div class="cards">')
    for label, value in (
        ("Bauteile", stats["components"]),
        ("Blätter", stats["sheets"]),
        ("Netze", stats["nets"]),
        ("Fehler", counts["error"]),
        ("Warnungen", counts["warning"]),
        ("Hinweise", counts["info"] + counts["hint"]),
    ):
        parts.append(f'<div class="card"><div class="n">{value}</div><div class="l">{label}</div></div>')
    parts.append("</div>")

    parts.append("<h2>Zusammenfassung</h2>")
    parts.append(f"<p>{esc(result.summary or 'Keine Zusammenfassung vorhanden.')}</p>")

    if result.requirements:
        parts.append("<h2>Abgeleitete Anforderungen</h2><table>")
        parts.append("<tr><th>ID</th><th>Anforderung</th><th>Status</th><th>Beleg</th><th>Lücke</th></tr>")
        icon = {"erfuellt": "✅", "teilweise": "🟡", "fehlt": "🔴", "unklar": "⚪"}
        for req in result.requirements:
            parts.append(
                "<tr><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    esc(req.id),
                    esc(req.requirement),
                    icon.get(req.status, ""),
                    esc(req.evidence),
                    esc(req.gap),
                )
            )
        parts.append("</table>")

    if not requirements_only:
        parts.append("<h2>Befunde</h2>")
        if not findings:
            parts.append("<p>Keine Befunde.</p>")
        else:
            for finding in findings:
                color, label = _SEVERITY_HTML[finding.severity]
                location = finding.location()
                parts.append(
                    f'<div class="finding {finding.severity.name.lower()}">'
                    f'<span class="badge" style="{badge_css[finding.severity]}">{esc(label)}</span>'
                    f"<strong>{esc(finding.title)}</strong> "
                    f'<span class="meta">({esc(finding.rule)})</span>'
                    f"<div>{esc(finding.detail)}</div>"
                )
                if location != "-":
                    parts.append(f'<div class="meta">Ort: <code>{esc(location)}</code></div>')
                if finding.fix:
                    parts.append(f"<div><strong>Empfehlung:</strong> {esc(finding.fix)}</div>")
                parts.append("</div>")

    parts.append("</body></html>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

def render_report(
    result: "ReviewResult",
    findings: list[Finding] | None = None,
    fmt: str = "text",
    requirements_only: bool = False,
) -> str:
    """Render *result* in the requested format."""
    if findings is None:
        findings = result.findings
    if fmt == "markdown":
        return _render_markdown(result, findings, requirements_only)
    if fmt == "html":
        return _render_html(result, findings, requirements_only)
    if fmt == "json":
        import json

        data = result.to_dict()
        data["findings"] = [f.to_dict() for f in findings]
        return json.dumps(data, ensure_ascii=False, indent=2)
    return _render_text(result, findings, requirements_only)


def write_report(result: "ReviewResult", path: str, fmt: str = "text", **kwargs) -> str:
    """Render and write a report, returning the path written."""
    content = render_report(result, fmt=fmt, **kwargs)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def _wrap(text: str, width: int, indent: str = "") -> list[str]:
    import textwrap

    if not text:
        return []
    return textwrap.wrap(text, width=width, subsequent_indent=indent) or [indent]