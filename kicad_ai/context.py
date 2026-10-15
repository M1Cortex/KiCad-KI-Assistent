"""Turn a :class:`Design` into a compact, token-efficient context for the LLM.

Large designs would otherwise blow past the context window, so the context is
budgeted: the most relevant sheets/nets are prioritised and everything is
elided with explicit markers so the model knows something was omitted.
"""

from __future__ import annotations

import json
from typing import Any

from .model import Design, Net, PinType

__all__ = ["DesignContext", "build_context", "render_prompt"]


class DesignContext:
    """A budgeted, serialisable snapshot of the design."""

    def __init__(self, data: dict[str, Any], text: str) -> None:
        self.data = data
        self.text = text

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=indent)


def _net_summary(net: Net) -> dict[str, Any]:
    by_type: dict[str, list[str]] = {}
    for ref, pin, ptype, func in net.nodes:
        key = ptype.value if isinstance(ptype, PinType) else str(ptype)
        label = f"{ref}.{pin}"
        if func and func not in ("", "passive"):
            label = f"{ref}.{pin}({func})"
        by_type.setdefault(key, []).append(label)
    return {
        "net": net.name,
        "nodes": len(net.nodes),
        "refs": sorted(net.refs),
        "pins_by_type": by_type,
        "is_power": net.is_power,
        "is_ground": net.is_ground,
        "sheets": sorted(net.sheets),
    }


def build_context(
    design: Design,
    max_nets: int = 120,
    max_symbols: int = 250,
    focus_refs: list[str] | None = None,
) -> DesignContext:
    """Build the context object for the given design.

    ``focus_refs`` biases the budget towards nets that touch those references,
    which is used when the user selects components in the UI.
    """
    focus = set(focus_refs or [])
    symbols = design.component_symbols

    comp_list = []
    for sym in symbols[:max_symbols]:
        comp_list.append(
            {
                "ref": sym.ref,
                "value": sym.value,
                "lib": sym.lib_id,
                "footprint": sym.footprint,
                "sheet": sym.sheet_path,
                "datasheet": sym.datasheet,
                "desc": sym.description,
                "dnp": sym.dnp,
                "pins": [
                    {
                        "n": p.number,
                        "name": p.name,
                        "type": p.type.value,
                    }
                    for p in sym.pins
                ],
            }
        )

    nets = list(design.nets)

    def priority(net: Net) -> tuple:
        if focus and net.refs & focus:
            return (0, -len(net.nodes))
        if net.is_power:
            return (1, -len(net.nodes))
        return (2, -len(net.nodes))

    nets.sort(key=priority)
    net_list = [_net_summary(n) for n in nets[:max_nets]]

    sheets = [
        {"path": s.path, "file": s.filename, "title": s.title, "ports": list(s.port_names)}
        for s in design.sheets
    ]

    labels: dict[str, int] = {}
    for label in design.labels:
        labels[label.text] = labels.get(label.text, 0) + 1

    data: dict[str, Any] = {
        "project": {
            "root_schematic": design.root_file,
            "title": design.title_block.get("title", ""),
            "company": design.title_block.get("company", ""),
            "rev": design.title_block.get("rev", ""),
        },
        "statistics": design.stats(),
        "sheets": sheets,
        "components": comp_list,
        "nets": net_list,
        "label_counts": labels,
        "truncated": {
            "components": max(0, len(symbols) - max_symbols),
            "nets": max(0, len(design.nets) - max_nets),
        },
        "power_symbols": sorted({s.ref for s in design.symbols if s.is_power_symbol}),
        "net_classes": sorted(
            {
                str(n.data_class)
                for n in design.nets
                if getattr(n, "data_class", None)
            }
        ) if hasattr(design.nets[0], "data_class") and design.nets else [],
    }

    return DesignContext(data, render_text(data))


def _fmt_components(items: list[dict]) -> str:
    lines = []
    for c in items:
        pins = ", ".join(
            f"{p['n']}:{p['name'] or '?'}<{p['type']}>" for p in c.get("pins", [])
        )
        header = f"{c['ref']} [{c['value']}] {c['lib']} (Blatt {c['sheet']})"
        if c.get("dnp"):
            header += " [DNP]"
        lines.append(f"{header}\n    Pins: {pins}")
    return "\n".join(lines)


def _fmt_nets(items: list[dict]) -> str:
    lines = []
    for n in items:
        by_type = n["pins_by_type"]
        groups = "; ".join(f"{k}: {', '.join(v)}" for k, v in by_type.items())
        flags = []
        if n["is_power"]:
            flags.append("POWER")
        if n["is_ground"]:
            flags.append("GND")
        flag = f" [{','.join(flags)}]" if flags else ""
        lines.append(f"{n['net']}{flag} ({n['nodes']} Knoten)\n    {groups}")
    return "\n".join(lines)


def render_text(data: dict[str, Any]) -> str:
    """Render the context as compact German text for the prompt."""
    stats = data["statistics"]
    parts = [
        "=== PROJEKT ===",
        f"Root-Schematic: {data['project']['root_schematic']}",
        f"Titel: {data['project']['title'] or '(leer)'}",
        "",
        "=== STATISTIK ===",
        f"Blätter: {stats['sheets']}, Bauteile: {stats['components']}, "
        f"Netze: {stats['nets']}, Pin-Verbindungen: {stats['pins']}",
        "",
        "=== BLÄTTER (Hierarchie) ===",
    ]
    for sheet in data["sheets"]:
        ports = ", ".join(sheet["ports"]) or "-"
        parts.append(f"{sheet['path']}  Ports: {ports}")
    parts += ["", "=== BAUTEILE ==="]
    parts.append(_fmt_components(data["components"]))
    parts += ["", "=== NETZE ==="]
    parts.append(_fmt_nets(data["nets"]))
    if data["truncated"]["components"] or data["truncated"]["nets"]:
        parts += [
            "",
            f"HINWEIS: {data['truncated']['components']} Bauteile und "
            f"{data['truncated']['nets']} Netze wurden gekürzt.",
        ]
    return "\n".join(parts)


SYSTEM_PROMPT = """\
Du bist ein erfahrener Elektronik-Hardware-Entwickler und Reviewer für KiCad-Schaltpläne.

Deine Aufgabe:
1. Prüfe den vorliegenden Schaltplan auf elektrische und funktionale Fehler.
2. Leite aus dem Schaltplan erkennbare Anforderungen ab (Versorgung, Schnittstellen, Schutz, \
Takt, Terminierung, Produktion).
3. Markiere, welche Anforderungen im Schaltplan erfüllt sind und welche fehlen oder \
unzureichend umgesetzt sind.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt in folgender Struktur (kein Markdown, \
keine Vorbemerkung):

{
  "summary": "2-4 Sätze Gesamteinschätzung des Schaltplans",
  "findings": [
    {
      "rule": "KURZER_REGELNAME",
      "severity": "error|warning|info",
      "title": "Kurzer Titel auf Deutsch",
      "detail": "Technische Begründung, erkläre WARUM das ein Problem ist",
      "category": "power|signal|netz|bom|mechanik|test|produktion|schutz",
      "refs": ["R1", "U3"],
      "nets": ["+3V3"],
      "fix": "Konkrete, umsetzbare Empfehlung"
    }
  ],
  "requirements": [
    {
      "id": "REQ-001",
      "requirement": "Aus dem Schaltplan abgeleitete Anforderung",
      "status": "erfuellt|teilweise|fehlt|unklar",
      "evidence": "Refs/Netze, die die Anforderung belegen",
      "gap": "Was fehlt, wenn status != erfuellt"
    }
  ],
  "questions": [
    "Rückfragen an den Entwickler, wenn die Spezifikation fehlt"
  ]
}

Regeln für dich:
- Beziehe dich IMMER auf konkrete Referenzdesignatoren und Netznamen aus dem Kontext.
- Erfinde keine Bauteile, die nicht im Kontext stehen.
- Melde nur echte Probleme. Lieber 5 belastbare Hinweise als 20 Vermutungen.
- Bei 'unklar'/'teilweise' muss 'gap' ausgefüllt sein.
- Alles auf Deutsch.\
"""


def render_prompt(context: DesignContext, extra_instruction: str = "") -> list[dict[str, str]]:
    """Build the chat message list for a review request."""
    user = (
        "Hier ist der extrahierte Schaltplan-Kontext.\n\n"
        f"{context.text}\n\n"
        "Erstelle jetzt das Review nach der vorgegebenen JSON-Struktur."
    )
    if extra_instruction:
        user += f"\n\nZusätzliche Aufgabe des Nutzers: {extra_instruction}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_review_response(text: str) -> dict[str, Any]:
    """Parse the model's JSON reply, tolerating markdown fences and prose."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}