"""Typed domain model for a KiCad schematic hierarchy.

The model is intentionally independent of both the file format and the IPC API,
so the same analysis code works on files, on IPC data and in unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

__all__ = [
    "PinType",
    "ELECTRICAL_TYPES",
    "SymInstance",
    "Pin",
    "Net",
    "SheetInstance",
    "Label",
    "Design",
    "port_direction_guess",
]


class PinType(str, Enum):
    """KiCad electrical pin types as used in the netlist export."""

    INPUT = "input"
    OUTPUT = "output"
    BIDIR = "bidirectional"
    TRISTATE = "tri_state"
    PASSIVE = "passive"
    FREE = "free"
    UNSPECIFIED = "unspecified"
    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    OPEN_COLLECTOR = "open_collector"
    OPEN_EMITTER = "open_emitter"
    NO_CONNECT = "no_connect"

    @property
    def is_power(self) -> bool:
        return self in (PinType.POWER_IN, PinType.POWER_OUT)

    @property
    def is_source(self) -> bool:
        """Pin actively drives the net (a power source or an output)."""
        return self in (PinType.POWER_OUT, PinType.OUTPUT)

    @property
    def is_sink(self) -> bool:
        return self in (PinType.POWER_IN, PinType.INPUT)

    @property
    def is_passive(self) -> bool:
        return self in (PinType.PASSIVE, PinType.UNSPECIFIED, PinType.FREE)


#: Pin types that do not influence connectivity semantics.
ELECTRICAL_TYPES = {p.value for p in PinType}


def port_direction_guess(name: str) -> PinType:
    """Heuristic pin direction from a pin/port name.

    Used only when the netlist does not carry an electrical type, which happens
    for hierarchical sheet ports in older exports.
    """
    upper = name.upper()
    if any(k in upper for k in ("GND", "VCC", "VDD", "VSS", "VEE", "VBAT", "AVDD", "AGND", "DGND")):
        return PinType.PASSIVE
    if upper.startswith(("IN_", "I_")) or upper.endswith(("_IN", "IN")) and "OUT" not in upper:
        return PinType.INPUT
    if upper.startswith(("OUT", "O_")) or upper.endswith("_OUT"):
        return PinType.OUTPUT
    if upper.startswith(("IO", "OE", "EN", "IRQ")):
        return PinType.BIDIR
    return PinType.PASSIVE


@dataclass
class Pin:
    """A single pin of a placed symbol instance.

    Mutable because the loader enriches pins with electrical types after the
    netlist export has been parsed.
    """

    number: str
    name: str
    type: PinType

    @property
    def label(self) -> str:
        return self.name or self.number


@dataclass
class SymInstance:
    """A symbol placed on a schematic sheet."""

    ref: str
    value: str
    lib_id: str
    sheet_path: str
    uuid: str = ""
    at: tuple[float, float] = (0.0, 0.0)
    mirror: str = ""
    properties: dict[str, str] = field(default_factory=dict)
    pins: list[Pin] = field(default_factory=list)
    unit: int = 1
    exclude_from_bom: bool = False
    exclude_from_board: bool = False
    dnp: bool = False

    @property
    def is_power_symbol(self) -> bool:
        return self.ref.startswith("#")

    @property
    def footprint(self) -> str:
        return self.properties.get("Footprint", "")

    @property
    def description(self) -> str:
        return self.properties.get("Description", "")

    @property
    def datasheet(self) -> str:
        return self.properties.get("Datasheet", "")

    @property
    def is_jumper(self) -> bool:
        return self.ref.startswith("JP")

    @property
    def is_connector(self) -> bool:
        return self.ref.startswith("J")

    @property
    def is_testpoint(self) -> bool:
        return self.ref.startswith("TP")

    @property
    def designator_prefix(self) -> str:
        """``R12`` -> ``R``.  Empty when the reference has no trailing number."""
        stripped = self.ref.lstrip("#")
        return "".join(ch for ch in stripped if not ch.isdigit())


@dataclass
class Net:
    """A electrical net with all connected pins."""

    name: str
    code: int = 0
    nodes: list[tuple[str, str, PinType, str]] = field(default_factory=list)
    """``(ref, pin_number, pin_type, pin_function)`` tuples."""
    sheets: set[str] = field(default_factory=set)

    @property
    def refs(self) -> set[str]:
        return {ref for ref, _, _, _ in self.nodes}

    def pin_types(self) -> list[PinType]:
        return [t for _, _, t, _ in self.nodes]

    @property
    def has_source(self) -> bool:
        return any(t.is_source for t in self.pin_types())

    @property
    def has_sink(self) -> bool:
        return any(t.is_sink for t in self.pin_types())

    @property
    def is_power(self) -> bool:
        return any(t.is_power for t in self.pin_types())

    @property
    def is_ground(self) -> bool:
        return _looks_like_ground(self.name)

    @property
    def degree(self) -> int:
        return len(self.nodes)

    def nodes_of_type(self, *types: PinType) -> list[tuple[str, str, PinType, str]]:
        return [n for n in self.nodes if n[2] in types]

    def describe(self) -> str:
        parts = [f"{ref}.{pin}" for ref, pin, _, _ in self.nodes]
        return f"{self.name}: " + ", ".join(parts)


@dataclass
class SheetInstance:
    """One hierarchical sheet (the root sheet included)."""

    path: str
    """Full hierarchical path, e.g. ``/power/analog``.  Root is ``/``."""
    filename: str
    uuid: str
    title: str = ""
    parent_path: str = ""
    port_names: dict[str, str] = field(default_factory=dict)
    """``{port_name: port_type}`` for hierarchical connections."""

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1] or "root"


@dataclass
class Label:
    """A local, hierarchical or global label occurrence."""

    text: str
    kind: str  # "local" | "global" | "hierarchical"
    sheet_path: str
    at: tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0


@dataclass
class Design:
    """The complete flattened design: all sheets, symbols and nets."""

    root_file: str
    sheets: list[SheetInstance] = field(default_factory=list)
    symbols: list[SymInstance] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    project_path: str = ""
    project_data: dict = field(default_factory=dict)
    title_block: dict[str, str] = field(default_factory=dict)
    #: ``(ref, pin_number)`` pairs explicitly marked with a no-connect flag
    no_connects: set[tuple[str, str]] = field(default_factory=set)

    def net_by_name(self, name: str) -> Net | None:
        for net in self.nets:
            if net.name == name:
                return net
        return None

    def symbol_by_ref(self, ref: str) -> SymInstance | None:
        for sym in self.symbols:
            if sym.ref == ref:
                return sym
        return None

    @property
    def component_symbols(self) -> list[SymInstance]:
        """Symbols excluding power symbols, no-connects and text-only items."""
        return [s for s in self.symbols if not s.is_power_symbol and s.ref not in ("#FLG",)]

    def power_nets(self) -> list[Net]:
        return [n for n in self.nets if n.is_power or _looks_like_power(n.name)]

    def ground_nets(self) -> list[Net]:
        return [n for n in self.nets if n.is_ground]

    def nets_touching(self, refs: Iterable[str]) -> list[Net]:
        wanted = set(refs)
        return [n for n in self.nets if n.refs & wanted]

    def stats(self) -> dict[str, int]:
        return {
            "sheets": len(self.sheets),
            "components": len(self.component_symbols),
            "symbols_total": len(self.symbols),
            "nets": len(self.nets),
            "pins": sum(len(n.nodes) for n in self.nets),
            "labels": len(self.labels),
        }


def _looks_like_ground(name: str) -> bool:
    upper = name.upper().lstrip("/")
    return upper in {
        "GND", "AGND", "DGND", "PGND", "SGND", "GNDA", "GNDD", "VSS", "VEE", "EARTH",
        "CHASSIS", "SHIELD", "0V",
    } or upper.endswith(("GND", "GROUND", "VSS", "VEE"))


def _looks_like_power(name: str) -> bool:
    upper = name.upper().lstrip("/")
    if _looks_like_ground(upper):
        return False
    if upper.startswith(("+", "-")) and any(ch.isdigit() for ch in upper):
        return True
    return upper in {"VCC", "VDD", "VBAT", "VIN", "VUSB", "VDDA", "VAA", "VDDQ", "PVDD"} or (
        upper.startswith("V") and any(ch.isdigit() for ch in upper)
    )