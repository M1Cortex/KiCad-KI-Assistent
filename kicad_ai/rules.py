"""Deterministic schematic rule engine.

These checks do not need an LLM. They run in milliseconds and give the AI a
verified factual base to reason about, which keeps hallucinated findings low.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum

from .model import Design, Net, PinType, SymInstance

__all__ = ["Severity", "Finding", "RuleContext", "check_all", "RULES"]


class Severity(IntEnum):
    ERROR = 4
    WARNING = 3
    INFO = 2
    HINT = 1

    @property
    def label(self) -> str:
        return self.name

    @classmethod
    def parse(cls, raw: str) -> "Severity":
        return {
            "error": cls.ERROR,
            "warn": cls.WARNING,
            "warning": cls.WARNING,
            "info": cls.INFO,
            "hint": cls.HINT,
        }.get(raw.strip().lower(), cls.INFO)


@dataclass
class Finding:
    """One rule violation or observation."""

    rule: str
    severity: Severity
    title: str
    detail: str
    category: str = "general"
    refs: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    sheet: str = ""
    fix: str = ""
    source: str = "rule"  # "rule" | "erc" | "ai"
    confidence: float = 1.0

    def location(self) -> str:
        bits = []
        if self.refs:
            bits.append(", ".join(self.refs[:6]))
        if self.nets:
            bits.append("net " + ", ".join(self.nets[:4]))
        if self.sheet and self.sheet != "/":
            bits.append(f"sheet {self.sheet}")
        return " | ".join(bits) if bits else "-"

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity.name,
            "title": self.title,
            "detail": self.detail,
            "category": self.category,
            "refs": self.refs,
            "nets": self.nets,
            "sheet": self.sheet,
            "fix": self.fix,
            "source": self.source,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class RuleContext:
    """Shared state for the rule engine."""

    design: Design
    erc_violations: list[dict] = field(default_factory=list)
    #: ``{ref: [pin numbers]}`` for pins marked with a no-connect flag
    no_connects: set[tuple[str, str]] = field(default_factory=set)
    enabled: set[str] = field(default_factory=set)

    def ref_of_erc_item(self, description: str) -> str:
        match = re.search(r"\b([A-Z]{1,4}\d+)\b", description)
        return match.group(1) if match else ""


# --------------------------------------------------------------------------- #
# Rule registry
# --------------------------------------------------------------------------- #

RULES: dict[str, str] = {
    "ERC-PROPAGATION": "Übernimmt die offiziellen KiCad-ERC-Ergebnisse",
    "NET-SINGLE-PIN": "Netz mit nur einem einzigen Pin (typoherber Anhang)",
    "NET-NO-SOURCE": "Netz ohne treibende Quelle (Ausgang oder Power_OUT)",
    "NET-MULTI-SOURCE": "Mehrere Stromquellen auf einem Netz (Kontakt zweier Ausgänge)",
    "NET-NO-SINK": "Netz ohne Senke (Eingang oder Power_IN)",
    "POWER-NO-DECOUPLING": "Versorgungsnetz ohne Abblockkondensator",
    "POWER-UNDECLARED": "Spannungsversorgung ohne Netzklasse oder Wert im Namen",
    "GROUND-MISSING": "Bauteil mit Versorgungspin ohne Massebezug",
    "PIN-FLOATING": "Eingangspin ohne jede Verbindung",
    "PIN-OUTPUT-UNUSED": "Ausgangspin ohne Last",
    "BOM-NO-FOOTPRINT": "Bauteil ohne zugewiesenes Footprint",
    "BOM-NO-DATASHEET": "Bauteil ohne Datenblatt-URL",
    "BOM-GENERIC-VALUE": "Bauteil mit generischem Platzhalterwert",
    "LABEL-SINGLE-USE": "Lokales Label wird nur einmal verwendet (Verdacht auf Tippfehler)",
    "LABEL-NAMING": "Netzname verletzt die Namenskonvention",
    "SHEET-UNUSED-PORT": "Hierarchischer Port ohne Verwendung im Blatt",
    "ERC-EXCLUDED": "Als ERC-Ausnahme markiert ( bewusst unterdrückt)",
    "SYM-DUPLICATE-REF": "Referenzdesignator doppelt vergeben",
    "SYM-UNANNOTATED": "Referenzdesignator nicht annotiert",
    "CONNECTOR-NO-PIN-NAME": "Pin eines Connectors ohne sprechenden Namen",
    "DNP-IN-BOM": "Bauteil als DNP markiert, aber nicht aus BOM ausgeschlossen",
    "TESTPOINT-COVERAGE": "Keine Testpunkte für relevante Netze vorhanden",
    "CRYSTAL-CAP": "Quarzoszillator ohne Lastkondensatoren",
    "I2C-PULLUP": "I2C-Bus ohne Pull-up-Widerstände",
}


def check_all(ctx: RuleContext) -> list[Finding]:
    """Run every rule and return the findings sorted by severity."""
    findings: list[Finding] = []
    for rule in (
        _erc_propagation,
        _erc_excluded,
        _net_single_pin,
        _net_no_source,
        _net_multi_source,
        _net_no_sink,
        _power_no_decoupling,
        _power_undeclared,
        _ground_missing,
        _pin_floating,
        _pin_output_unused,
        _bom_no_footprint,
        _bom_no_datasheet,
        _bom_generic_value,
        _label_single_use,
        _label_naming,
        _sheet_unused_port,
        _sym_duplicate_ref,
        _sym_unannotated,
        _connector_no_pin_name,
        _dnp_in_bom,
        _crystal_caps,
        _i2c_pullups,
    ):
        findings.extend(rule(ctx))
    findings.sort(key=lambda f: (-int(f.severity), f.rule))
    return findings


# --------------------------------------------------------------------------- #
# ERC bridging
# --------------------------------------------------------------------------- #

_SEVERITY_MAP = {
    "error": Severity.ERROR,
    "warning": Severity.WARNING,
    "exclusion": Severity.INFO,
}


def _erc_propagation(ctx: RuleContext) -> list[Finding]:
    out: list[Finding] = []
    for v in ctx.erc_violations:
        sev = v.get("severity", "warning")
        if sev == "exclusion":
            continue
        refs = [ctx.ref_of_erc_item(i.get("description", "")) for i in v.get("items", [])]
        refs = [r for r in refs if r]
        out.append(
            Finding(
                rule=f"ERC-{v.get('type', 'unknown').upper()}",
                severity=_SEVERITY_MAP.get(sev, Severity.WARNING),
                title=_erc_title(v.get("type", "")),
                detail=v.get("description", ""),
                category="erc",
                refs=refs,
                sheet=v.get("sheet", ""),
                fix="In KiCad: Inspect > Electrical Rules Checker",
                source="erc",
            )
        )
    return out


_ERC_TITLES = {
    "pin_not_connected": "Pin nicht verbunden",
    "pin_not_driven": "Pin nicht getrieben",
    "pin_not_connected_end": "Offenes Drahtende",
    "powerpin_not_driven": "Power-Pin ohne Quelle",
    "two_power_symbols": "Zwei Power-Symbole am selben Netz",
    "conflict_pin_to_net": "Pin-Konflikt auf dem Netz",
    "different_unit_footprint": "Abweichendes Footprint pro Unit",
    "different_unit_net": "Abweichendes Netz pro Unit",
    "lib_symbol_mismatch": "Symbol weicht von der Bibliothek ab",
    "lib_symbol_issues": "Symbol-Problem in der Bibliothek",
    "unannotated": "Nicht annotiertes Symbol",
    "similar_labels": "Ähnliche Labels (Tippfehler?)",
    "similar_power": "Ähnliche Power-Namen",
    "global_label_dangling": "Global Label ohne Verwendung",
    "hier_label_mismatch": "Hierarchisches Label passt nicht",
    "missing_input_pin": "Fehlender Eingangspin am Treiber",
    "missing_output_pin": "Fehlender Ausgangspin am Treiber",
    "missing_bidi_pin": "Fehlender bidirektionaler Pin am Treiber",
    "missing_unidir_pin": "Fehlender unidirektionaler Pin am Treiber",
    "missing_power_pin": "Fehlender Power-Pin am Treiber",
    "no_connect_connected": "No-Connect an verbundenem Pin",
    "no_connect_dangling": "No-Connect an freiem Pin",
    "pin_not_connected": "Pin nicht verbunden",
    "bus_entry_connected": "Bus-Eintrag unerwartet verbunden",
    "endpoint_off_grid": "Endpunkt nicht auf Raster",
}


def _erc_title(erc_type: str) -> str:
    return _ERC_TITLES.get(erc_type, erc_type.replace("_", " ").capitalize())


def _erc_excluded(ctx: RuleContext) -> list[Finding]:
    out: list[Finding] = []
    for v in ctx.erc_violations:
        if v.get("severity") != "exclusion":
            continue
        refs = [ctx.ref_of_erc_item(i.get("description", "")) for i in v.get("items", [])]
        out.append(
            Finding(
                rule=f"ERC-EXCLUDED-{v.get('type', 'unknown').upper()}",
                severity=Severity.INFO,
                title="ERC-Verstoß als Ausnahme unterdrückt",
                detail=v.get("description", ""),
                category="erc",
                refs=[r for r in refs if r],
                sheet=v.get("sheet", ""),
                fix="Prüfen, ob die Ausnahme noch berechtigt ist",
                source="erc",
                confidence=0.9,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Net rules
# --------------------------------------------------------------------------- #

def _net_single_pin(ctx: RuleContext) -> list[Finding]:
    out = []
    for net in ctx.design.nets:
        if net.degree == 1:
            ref, pin, _, _ = net.nodes[0]
            if (ref, pin) in ctx.no_connects:
                continue
            out.append(
                Finding(
                    rule="NET-SINGLE-PIN",
                    severity=Severity.WARNING,
                    title=f"Netz '{net.name}' hat nur einen Anschluss",
                    detail=(
                        f"Das Netz {net.name} ist nur mit {ref}.{pin} verbunden. "
                        "Meist ein Tippfehler im Label oder ein fehlender Anschluss."
                    ),
                    category="netz",
                    refs=[ref],
                    nets=[net.name],
                    fix=f"Label/Netznamen prüfen oder Anschluss an {ref}.{pin} ergänzen.",
                )
            )
    return out


def _net_no_source(ctx: RuleContext) -> list[Finding]:
    out = []
    for net in ctx.design.nets:
        types = net.pin_types()
        if not types or net.degree < 2:
            continue
        if net.is_power and not net.has_source:
            out.append(
                Finding(
                    rule="NET-NO-SOURCE",
                    severity=Severity.ERROR,
                    title=f"Versorgungsnetz '{net.name}' ohne Quelle",
                    detail=(
                        f"{net.describe()} enthält keinen POWER_OUT-Pin. "
                        "Das Netz kann nicht versorgt werden."
                    ),
                    category="netz",
                    nets=[net.name],
                    refs=sorted(net.refs)[:8],
                    fix="PWR_FLAG setzen oder eine Spannungsquelle / einen Regler ergänzen.",
                )
            )
    return out


def _net_multi_source(ctx: RuleContext) -> list[Finding]:
    out = []
    for net in ctx.design.nets:
        sources = net.nodes_of_type(PinType.POWER_OUT, PinType.OUTPUT)
        if len(sources) > 1:
            out.append(
                Finding(
                    rule="NET-MULTI-SOURCE",
                    severity=Severity.ERROR,
                    title=f"Mehrere Quellen auf '{net.name}'",
                    detail=(
                        "Diese Pins treiben dasselbe Netz aktiv an: "
                        + ", ".join(f"{r}.{p}" for r, p, _, _ in sources)
                        + ". Beim Verbinden zweier Ausgänge droht Kurzschluss."
                    ),
                    category="netz",
                    nets=[net.name],
                    refs=[r for r, _, _, _ in sources],
                    fix="Trenn widerstand (0 Ω) einfügen oder die Architektur ändern.",
                )
            )
    return out


def _net_no_sink(ctx: RuleContext) -> list[Finding]:
    out = []
    for net in ctx.design.nets:
        types = net.pin_types()
        if not types or net.degree < 2:
            continue
        if all(t.is_source or t.is_passive for t in types) and not net.is_power:
            if any(t == PinType.OUTPUT for t in types):
                out.append(
                    Finding(
                        rule="NET-NO-SINK",
                        severity=Severity.INFO,
                        title=f"Netz '{net.name}' hat keine Senke",
                        detail=(
                            f"{net.describe()} enthält nur Ausgänge und passive Bauteile. "
                            "Der Ausgang treibt nicht in eine Last."
                        ),
                        category="netz",
                        nets=[net.name],
                        refs=sorted(net.refs)[:8],
                        fix="Prüfen, ob die angeschlossene Last fehlt oder falsch benannt ist.",
                    )
                )
    return out


# --------------------------------------------------------------------------- #
# Power rules
# --------------------------------------------------------------------------- #

_CAP_REF = re.compile(r"^C\d+$", re.I)
_REG_REF = re.compile(r"^(U|L|Q|IC)\d+$", re.I)


def _power_no_decoupling(ctx: RuleContext) -> list[Finding]:
    """Every non-trivial supply net should have at least one bypass capacitor."""
    out = []
    caps_by_net: dict[str, list[str]] = {}
    for net in ctx.design.nets:
        caps_by_net[net.name] = [
            ref for ref, _, ptype, _ in net.nodes if _CAP_REF.match(ref) and ptype == PinType.PASSIVE
        ]

    for net in ctx.design.power_nets():
        if net.is_ground:
            continue
        if net.degree < 2:
            continue
        if caps_by_net.get(net.name):
            continue
        # Net without any IC at all is probably a switched/load net, skip
        ic_refs = [
            ref for ref in net.refs
            if _REG_REF.match(ref) and ref not in ("#PWR",)
        ]
        if not ic_refs:
            continue
        out.append(
            Finding(
                rule="POWER-NO-DECOUPLING",
                severity=Severity.WARNING,
                title=f"Kein Abblockkondensator an '{net.name}'",
                detail=(
                    f"{net.describe()} versorgt {len(ic_refs)} aktive Bauteile "
                    f"({', '.join(sorted(ic_refs)[:6])}), hat aber keinen Abblockkondensator."
                ),
                category="power",
                nets=[net.name],
                refs=sorted(ic_refs)[:8],
                fix=f"Kondensator (100 nF) parallel zu {net.name}/GND ergänzen.",
            )
        )
    return out


def _power_undeclared(ctx: RuleContext) -> list[Finding]:
    out = []
    for net in ctx.design.power_nets():
        name = net.name.lstrip("/")
        if not re.match(r"^[+-]?\d+(\.\d+)?V\d*$", name.upper()) and not name.upper().startswith(
            ("VCC", "VDD", "VBAT", "VIN")
        ):
            out.append(
                Finding(
                    rule="POWER-UNDECLARED",
                    severity=Severity.HINT,
                    title=f"Spannungsnetz '{net.name}' ohne Spannung im Namen",
                    detail=(
                        "Konvention: Versorgungsnetze als '+3V3', '+5V' oder '+1V8' benennen, "
                        "damit Spannung und Reihenfolge eindeutig sind."
                    ),
                    category="power",
                    nets=[net.name],
                    fix="Netz umbenennen und Spannungswert im Namen ergänzen.",
                )
            )
    return out


def _ground_missing(ctx: RuleContext) -> list[Finding]:
    """Active parts with a power pin but no ground reference anywhere in design."""
    grounds = {n.name for n in ctx.design.ground_nets()}
    if not grounds:
        return []
    out = []
    for sym in ctx.design.component_symbols:
        power_pins = [p for p in sym.pins if p.type.is_power]
        if not power_pins:
            continue
        touching = ctx.design.nets_touching([sym.ref])
        if not any(n.is_ground for n in touching):
            out.append(
                Finding(
                    rule="GROUND-MISSING",
                    severity=Severity.WARNING,
                    title=f"{sym.ref} ohne Massebezug",
                    detail=(
                        f"{sym.ref} ({sym.value}) hat Versorgungspins "
                        f"({', '.join(p.label for p in power_pins[:4])}), "
                        "ist aber an keinem Massenetz verbunden."
                    ),
                    category="power",
                    refs=[sym.ref],
                    fix="Masseverbindung zum GND-Netz ergänzen.",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Pin rules
# --------------------------------------------------------------------------- #

def _pin_floating(ctx: RuleContext) -> list[Finding]:
    out = []
    connected = {(ref, pin): net.name for net in ctx.design.nets for ref, pin, _, _ in net.nodes}
    for sym in ctx.design.component_symbols:
        if sym.is_connector or sym.is_testpoint:
            continue
        for pin in sym.pins:
            if pin.type not in (PinType.INPUT, PinType.POWER_IN):
                continue
            key = (sym.ref, pin.number)
            if key in connected or key in ctx.no_connects:
                continue
            out.append(
                Finding(
                    rule="PIN-FLOATING",
                    severity=Severity.ERROR,
                    title=f"{sym.ref}.{pin.number} ist unverbunden",
                    detail=(
                        f"{sym.ref} ({sym.value}) Pin {pin.number} '{pin.label}' ist ein "
                        f"{pin.type.value}-Pin ohne Verbindung."
                    ),
                    category="pin",
                    refs=[sym.ref],
                    fix="Signalquelle ergänzen oder No-Connect setzen, falls beabsichtigt.",
                )
            )
    return out


def _pin_output_unused(ctx: RuleContext) -> list[Finding]:
    out = []
    for net in ctx.design.nets:
        outputs = net.nodes_of_type(PinType.OUTPUT)
        if not outputs or net.degree < 2:
            continue
        loads = [n for n in net.nodes if n[2] in (PinType.INPUT, PinType.PASSIVE)]
        if not loads:
            out.append(
                Finding(
                    rule="PIN-OUTPUT-UNUSED",
                    severity=Severity.INFO,
                    title=f"Ausgang auf '{net.name}' ohne Last",
                    detail=f"{net.describe()} - der Ausgang ist nicht belastet.",
                    category="pin",
                    nets=[net.name],
                    refs=[r for r, _, _, _ in outputs],
                    fix="Prüfen, ob die angeschlossene Schaltung fehlt.",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# BOM / annotation rules
# --------------------------------------------------------------------------- #

_GENERIC_VALUES = {"", "?", "XX", "XXX", "TBD", "TODO", "DUMMY", "N/A", "NA", "-", "VALUE", "VAL"}


def _bom_no_footprint(ctx: RuleContext) -> list[Finding]:
    out = []
    for sym in ctx.design.component_symbols:
        if sym.is_power_symbol or sym.exclude_from_bom:
            continue
        if not sym.footprint.strip():
            out.append(
                Finding(
                    rule="BOM-NO-FOOTPRINT",
                    severity=Severity.WARNING,
                    title=f"{sym.ref} ohne Footprint",
                    detail=f"{sym.ref} ({sym.value}) hat kein Footprint zugewiesen.",
                    category="bom",
                    refs=[sym.ref],
                    fix="Footprint zuweisen (Tools > Assign Footprints / Annotation).",
                )
            )
    return out


def _bom_no_datasheet(ctx: RuleContext) -> list[Finding]:
    out = []
    for sym in ctx.design.component_symbols:
        if sym.exclude_from_bom or sym.is_power_symbol:
            continue
        if sym.datasheet.strip() in ("", "~"):
            out.append(
                Finding(
                    rule="BOM-NO-DATASHEET",
                    severity=Severity.HINT,
                    title=f"{sym.ref} ohne Datenblatt",
                    detail=f"{sym.ref} ({sym.value}) hat kein Datenblatt-Feld gefüllt.",
                    category="bom",
                    refs=[sym.ref],
                    fix="Datenblatt-URL aus der Symbolbibliothek übernehmen.",
                )
            )
    return out


def _bom_generic_value(ctx: RuleContext) -> list[Finding]:
    out = []
    for sym in ctx.design.component_symbols:
        if sym.exclude_from_bom or sym.is_power_symbol:
            continue
        if sym.value.strip().upper() in _GENERIC_VALUES:
            out.append(
                Finding(
                    rule="BOM-GENERIC-VALUE",
                    severity=Severity.WARNING,
                    title=f"{sym.ref} hat Platzhalterwert",
                    detail=f"{sym.ref} hat den Wert '{sym.value}' - die Stückliste ist damit nicht eindeutig.",
                    category="bom",
                    refs=[sym.ref],
                    fix="Konkreten Wert eintragen (z. B. 10k, 100nF, STM32F103).",
                )
            )
    return out


def _dnp_in_bom(ctx: RuleContext) -> list[Finding]:
    out = []
    for sym in ctx.design.component_symbols:
        if sym.dnp and not sym.exclude_from_bom:
            out.append(
                Finding(
                    rule="DNP-IN-BOM",
                    severity=Severity.INFO,
                    title=f"{sym.ref} ist DNP, aber in der BOM",
                    detail=(
                        f"{sym.ref} ({sym.value}) ist als 'do not populate' markiert, "
                        "wird aber in der Stückliste mitbestückt."
                    ),
                    category="bom",
                    refs=[sym.ref],
                    fix="Symboleigenschaft 'Exclude from BOM/Board' setzen oder DNP-Markierung entfernen.",
                )
            )
    return out


def _sym_duplicate_ref(ctx: RuleContext) -> list[Finding]:
    """Report references that are genuinely assigned more than once.

    Multi-unit symbols (op amps, regulators) are placed several times with the
    same reference and different ``unit`` numbers. Those are legitimate, so a
    repeated reference only counts as an error when the units collide.
    """
    seen: dict[str, list[SymInstance]] = {}
    for sym in ctx.design.symbols:
        if sym.ref.startswith("#"):
            continue
        seen.setdefault(sym.ref, []).append(sym)

    out = []
    for ref, syms in seen.items():
        if len(syms) < 2:
            continue

        # Multi-unit placement: on each sheet, every placement has its own unit
        # number. If that holds everywhere the repetition is legitimate.
        units_by_sheet: dict[str, list[int]] = {}
        for sym in syms:
            units_by_sheet.setdefault(sym.sheet_path, []).append(sym.unit)

        is_multi_unit = all(
            len(set(units)) == len(units) for units in units_by_sheet.values()
        )
        if is_multi_unit:
            continue

        out.append(
            Finding(
                rule="SYM-DUPLICATE-REF",
                severity=Severity.ERROR,
                title=f"Referenz '{ref}' mehrfach vergeben",
                detail=(
                    f"'{ref}' liegt {len(syms)}-mal im Design "
                    f"({', '.join(sorted({s.sheet_path for s in syms}))}). "
                    "Das ist bei Multi-Unit-Symbolen nur zulässig, wenn jede "
                    "Einheit eine eigene Unit-Nummer hat."
                ),
                category="annotation",
                refs=[ref],
                fix="Erneute Annotation ausführen (Tools > Annotate Schematic).",
            )
        )
    return out


def _sym_unannotated(ctx: RuleContext) -> list[Finding]:
    out = []
    for sym in ctx.design.symbols:
        if sym.ref.startswith("#"):
            continue
        if not sym.ref or "?" in sym.ref:
            out.append(
                Finding(
                    rule="SYM-UNANNOTATED",
                    severity=Severity.ERROR,
                    title="Symbol ohne Referenzdesignator",
                    detail=f"Ein Symbol ({sym.lib_id}) auf {sym.sheet_path} ist nicht annotiert.",
                    category="annotation",
                    sheet=sym.sheet_path,
                    fix="Tools > Annotate Schematic ausführen.",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Net naming / labels
# --------------------------------------------------------------------------- #

def _label_single_use(ctx: RuleContext) -> list[Finding]:
    counts: dict[str, int] = {}
    for label in ctx.design.labels:
        if label.kind == "local":
            counts[label.text] = counts.get(label.text, 0) + 1
    out = []
    for text, count in counts.items():
        if count != 1:
            continue
        matching_net = ctx.design.net_by_name(text)
        if matching_net is not None and matching_net.degree > 1:
            continue  # resolved through hierarchy, fine
        out.append(
            Finding(
                rule="LABEL-SINGLE-USE",
                severity=Severity.WARNING,
                title=f"Label '{text}' nur einmal verwendet",
                detail=(
                    f"Das lokale Label '{text}' erscheint nur an einer Stelle. "
                    "Meist ein Tippfehler, der ein Netz ungewollt aufteilt."
                ),
                category="netz",
                nets=[text],
                fix="Schreibweise mit anderen Labels abgleichen.",
            )
        )
    return out


_NAMING_RE = re.compile(r"^[A-Za-z0-9_+\-./\[\]]+$")


def _label_naming(ctx: RuleContext) -> list[Finding]:
    out = []
    seen: set[str] = set()
    for net in ctx.design.nets:
        name = net.name
        if name in seen or name.startswith("Net-(") or name.startswith("unconnected-"):
            continue
        seen.add(name)
        problem = None
        if " " in name:
            problem = "enthält Leerzeichen"
        elif not _NAMING_RE.match(name):
            problem = "enthält ungültige Zeichen"
        elif name.islower() and not name.startswith("net"):
            problem = "nur Kleinschreibung"
        if problem:
            out.append(
                Finding(
                    rule="LABEL-NAMING",
                    severity=Severity.HINT,
                    title=f"Netzname '{name}' unkonventionell",
                    detail=f"Der Netzname {problem}.",
                    category="netz",
                    nets=[name],
                    fix="Auf SNAKE_CASE oder Spannungs-Konvention umstellen.",
                )
            )
    return out


def _sheet_unused_port(ctx: RuleContext) -> list[Finding]:
    out = []
    for sheet in ctx.design.sheets:
        if not sheet.port_names:
            continue
        used = {
            label.text
            for label in ctx.design.labels
            if label.sheet_path == sheet.path
        }
        for port_name in sheet.port_names:
            if port_name not in used:
                out.append(
                    Finding(
                        rule="SHEET-UNUSED-PORT",
                        severity=Severity.WARNING,
                        title=f"Port '{port_name}' in {sheet.path} unbenutzt",
                        detail=(
                            f"Der hierarchische Port '{port_name}' auf Blatt '{sheet.path}' "
                            "hat im Inneren kein passendes Label."
                        ),
                        category="hierarchie",
                        sheet=sheet.path,
                        fix="Label im Blatt ergänzen oder Port entfernen.",
                    )
                )
    return out


def _connector_no_pin_name(ctx: RuleContext) -> list[Finding]:
    out = []
    for sym in ctx.design.component_symbols:
        if not sym.is_connector or not sym.pins:
            continue
        unnamed = [p for p in sym.pins if not p.name.strip()]
        if len(unnamed) >= max(2, len(sym.pins) // 2):
            out.append(
                Finding(
                    rule="CONNECTOR-NO-PIN-NAME",
                    severity=Severity.INFO,
                    title=f"{sym.ref}: Pins ohne Namen",
                    detail=(
                        f"{sym.ref} ({sym.value}) hat {len(unnamed)} unbenannte Pins. "
                        "Ohne Pin-Namen ist das Board-Layout schwer prüfbar."
                    ),
                    category="bom",
                    refs=[sym.ref],
                    fix="Pin-Namen in der Symbolbibliothek vergeben (z. B. VBUS, GND, D+).",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Design-pattern specific checks
# --------------------------------------------------------------------------- #

_OSC_RE = re.compile(r"(XTAL|OSC|QUARTZ|CRYSTAL)", re.I)


def _crystal_caps(ctx: RuleContext) -> list[Finding]:
    out = []
    for sym in ctx.design.component_symbols:
        if not _OSC_RE.search(sym.value + sym.ref + sym.lib_id):
            continue
        nets = ctx.design.nets_touching([sym.ref])
        cap_count = 0
        for net in nets:
            cap_count += sum(1 for ref, _, _, _ in net.nodes if _CAP_REF.match(ref))
        if cap_count < 2:
            out.append(
                Finding(
                    rule="CRYSTAL-CAP",
                    severity=Severity.WARNING,
                    title=f"{sym.ref}: Oszillator ohne zwei Lastkondensatoren",
                    detail=(
                        f"{sym.ref} ({sym.value}) hat nur {cap_count} Kondensator(en) im Netz. "
                        "Üblich sind zwei Lastkondensatoren zwischen Oszillator und Masse."
                    ),
                    category="power",
                    refs=[sym.ref],
                    fix="Zwei Lastkondensatoren (typ. 12-22 pF) parallel zum Quarz ergänzen.",
                )
            )
    return out


_I2C_HINT = re.compile(r"^(SDA|SCL|I2C.*|.*\.I2C)$", re.I)
_I2C_REF = re.compile(r"(I2C|PCF8574|PCA95|FT232|CH34|MAX3[0-9]{3}|CP210|24C0|24C[0-9]{2})", re.I)


def _i2c_pullups(ctx: RuleContext) -> list[Finding]:
    out = []
    bus_nets = [n for n in ctx.design.nets if _I2C_HINT.match(n.name)]
    if not bus_nets:
        return out
    for net in bus_nets:
        has_pullup = False
        for ref in net.refs:
            sym = ctx.design.symbol_by_ref(ref)
            if sym and sym.designator_prefix.upper() == "R":
                has_pullup = True
                break
        if has_pullup:
            continue
        if net.degree < 2:
            continue
        out.append(
            Finding(
                rule="I2C-PULLUP",
                severity=Severity.WARNING,
                title=f"I2C-Bus '{net.name}' ohne Pull-up",
                detail=(
                    f"{net.describe()} - ein I2C-Bus benötigt Pull-up-Widerstände "
                    "(typ. 4,7 kΩ auf die Versorgung), sonst sind die Leitungen floatend."
                ),
                category="signal",
                nets=[net.name],
                refs=sorted(net.refs)[:8],
                fix="Je zwei Pull-up-Widerstände (4,7 kΩ) zwischen SDA/SCL und Versorgung ergänzen.",
            )
        )
    return out