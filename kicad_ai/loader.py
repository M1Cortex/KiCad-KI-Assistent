"""Load a KiCad schematic hierarchy into the :class:`Design` model.

Two independent sources of truth are combined:

* ``kicad-cli sch export netlist`` provides authoritative connectivity
  (including KiCad's own label/power/hierarchy resolution).
* The raw ``.kicad_sch`` files provide geometry, fields, sheet structure and
  pin names that the netlist format does not expose.

When ``kicad-cli`` is unavailable the loader still produces a usable model from
the files alone, but net connectivity will be incomplete.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Iterable

from .model import Design, Label, Net, Pin, PinType, SheetInstance, SymInstance
from .sexpr import SymList, Symbol, parse_file

__all__ = ["SchematicLoader", "KicadCli", "load_design", "resolve_project"]


# --------------------------------------------------------------------------- #
# kicad-cli bridge
# --------------------------------------------------------------------------- #

class KicadCliError(RuntimeError):
    pass


class KicadCli:
    """Thin wrapper around the ``kicad-cli`` executable."""

    def __init__(self, executable: str | None = None, timeout: int = 300) -> None:
        self.executable = executable or self._discover()
        self.timeout = timeout

    @staticmethod
    def _discover() -> str:
        """Locate ``kicad-cli`` next to the running interpreter, or on PATH."""
        env = os.environ.get("KICAD_CLI")
        if env and os.path.isfile(env):
            return env
        exe = "kicad-cli.exe" if os.name == "nt" else "kicad-cli"
        candidates = []
        # .../bin/python.exe -> .../bin/kicad-cli.exe
        python_dir = os.path.dirname(os.path.abspath(os.environ.get("KICAD_PYTHON", sys_executable())))
        candidates.append(os.path.join(python_dir, exe))
        kicad_dir = os.environ.get("KICAD_ROOT")
        if kicad_dir:
            candidates.append(os.path.join(kicad_dir, "bin", exe))
        for cand in candidates:
            if os.path.isfile(cand):
                return cand
        return exe  # rely on PATH

    @property
    def available(self) -> bool:
        try:
            out = self._run(["version"])
            return "kicad-cli" in out.lower() or bool(out.strip())
        except Exception:
            return False

    def version(self) -> str:
        try:
            return self._run(["version"]).strip()
        except Exception:
            return "unknown"

    def _run(self, args: list[str]) -> str:
        cmd = [self.executable, *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise KicadCliError(f"kicad-cli not found at {self.executable!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise KicadCliError("kicad-cli timed out") from exc
        if proc.returncode != 0:
            raise KicadCliError(proc.stderr.strip() or f"kicad-cli exited {proc.returncode}")
        return proc.stdout

    def export_netlist(self, sch_path: str, fmt: str = "kicadxml") -> str:
        """Export a netlist and return the file content."""
        suffix = ".xml" if fmt == "kicadxml" else ".net"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            out_path = tmp.name
        try:
            self._run(
                ["sch", "export", "netlist", "--format", fmt, "-o", out_path, sch_path]
            )
            with open(out_path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        finally:
            _silent_unlink(out_path)

    def run_erc(self, sch_path: str, severity: str = "all") -> list[dict]:
        """Run ERC and return the violations as a list of dictionaries."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            out_path = tmp.name
        try:
            args = ["sch", "erc", "--format", "json", "-o", out_path]
            if severity in ("all", "error", "warning"):
                args.append(f"--severity-{severity}")
            args.append(sch_path)
            self._run(args)
            with open(out_path, "r", encoding="utf-8", errors="replace") as handle:
                data = ET.fromstring(handle.read())
        except ET.ParseError:
            return []
        finally:
            _silent_unlink(out_path)
        return _parse_erc_json(data)

    def export_bom_xml(self, sch_path: str) -> list[dict]:
        """Return BOM components (legacy XML format)."""
        try:
            content = self._run(
                ["sch", "export", "python-bom", "-o", "-", sch_path]
            )
        except KicadCliError:
            return []
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return []
        comps = []
        for node in root.iter("comp"):
            ref = node.get("ref", "")
            value = ""
            for field in node.iter("field"):
                if field.get("name") == "Value":
                    value = field.text or ""
            comps.append({"ref": ref, "value": value})
        return comps


def sys_executable() -> str:
    import sys

    return sys.executable


def _silent_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _parse_erc_json(root: ET.Element) -> list[dict]:
    violations: list[dict] = []
    for sheet in root.iter("sheet"):
        path = sheet.get("path", "/")
        for v in sheet.iter("violation"):
            items = []
            for item in v.iter("item"):
                pos = item.find("pos")
                items.append(
                    {
                        "description": item.get("description", ""),
                        "uuid": item.get("uuid", ""),
                        "pos": {
                            "x": float(pos.get("x", 0)) if pos is not None else 0.0,
                            "y": float(pos.get("y", 0)) if pos is not None else 0.0,
                        },
                    }
                )
            violations.append(
                {
                    "sheet": path,
                    "type": v.get("type", ""),
                    "severity": v.get("severity", ""),
                    "description": v.get("description", ""),
                    "items": items,
                }
            )
    return violations


# --------------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------------- #

def resolve_project(path: str) -> tuple[str, str]:
    """Return ``(root_schematic, project_file)`` for a path that may point at any
    part of a KiCad project (project, schematic, board or directory)."""
    path = os.path.abspath(path)
    if os.path.isdir(path):
        schematics = sorted(f for f in os.listdir(path) if f.endswith(".kicad_sch"))
        projects = sorted(f for f in os.listdir(path) if f.endswith(".kicad_pro"))
        if not schematics:
            raise FileNotFoundError(f"no .kicad_sch found in {path}")
        root = schematics[0]
        pro = projects[0] if projects else ""
        return os.path.join(path, root), os.path.join(path, pro)
    if path.endswith(".kicad_pcb"):
        base = path[: -len(".kicad_pcb")]
        for ext in (".kicad_sch", ".kicad_pro"):
            cand = base + ext
            if os.path.isfile(cand):
                return cand if ext == ".kicad_sch" else cand, base + ".kicad_pro"
        raise FileNotFoundError(f"no schematic next to {path}")
    if path.endswith(".kicad_pro"):
        base = path[: -len(".kicad_pro")]
        cand = base + ".kicad_sch"
        if not os.path.isfile(cand):
            raise FileNotFoundError(f"no root schematic next to {path}")
        return cand, path
    if not path.endswith(".kicad_sch"):
        raise ValueError(f"unsupported input {path!r}")
    pro = path[: -len(".kicad_sch")] + ".kicad_pro"
    return path, pro if os.path.isfile(pro) else ""


class SchematicLoader:
    """Builds a :class:`Design` from a schematic hierarchy."""

    def __init__(self, cli: KicadCli | None = None) -> None:
        self.cli = cli or KicadCli()
        self._lib_symbols: dict[str, SymList] = {}

    # -- hierarchy -------------------------------------------------------- #

    def load(self, path: str) -> Design:
        root_sch, project_path = resolve_project(path)
        design = Design(root_file=root_sch, project_path=project_path)
        if project_path and os.path.isfile(project_path):
            try:
                design.project_data = parse_file(project_path).to_dict()
                tb = parse_file(project_path).at("title_block")
                if tb is not None:
                    design.title_block = {
                        k: v for k, v in tb.to_dict().items() if isinstance(v, str)
                    }
            except Exception:
                design.project_data = {}

        self._walk(root_sch, "/", design, set())
        self._load_nets(root_sch, design)
        self._collect_no_connects(root_sch, design)
        return design

    def _collect_no_connects(self, root_sch: str, design: Design) -> None:
        """Map no-connect markers onto ``(ref, pin)`` pairs.

        KiCad stores no-connects as a flag on the symbol instance, so we expand
        every pin of a flagged symbol. That is slightly coarse (a no-connect on
        one pin marks all), which errs on the side of suppressing a warning.
        """
        by_uuid = {s.uuid: s for s in design.symbols if s.uuid}
        try:
            tree = parse_file(root_sch)
        except Exception:
            return
        for uuid in _has_no_connect(tree):
            sym = by_uuid.get(uuid)
            if sym is None:
                continue
            for pin in sym.pins:
                design.no_connects.add((sym.ref, pin.number))

    def _walk(self, sch_file: str, path: str, design: Design, seen: set[str]) -> None:
        real = os.path.realpath(sch_file)
        if real in seen:
            return
        seen.add(real)
        tree = parse_file(sch_file)
        folder = os.path.dirname(sch_file)

        sheet = SheetInstance(
            path=path,
            filename=sch_file,
            uuid=_root_uuid(tree),
            title=_title_from_sch(tree),
            parent_path=path.rsplit("/", 1)[0] or "/",
        )
        for pin in tree.all("pin"):
            if pin.at("name") is not None:
                sheet.port_names[str(pin.value_of("name"))] = str(pin.value_of("type", 2) or "")
        design.sheets.append(sheet)

        # Cache lib_symbols so pin definitions can be resolved later.
        lib = tree.at("lib_symbols")
        if lib is not None:
            for symbol in lib.all("symbol"):
                name = str(symbol.value_of("name") or symbol[1] if len(symbol) > 1 else "")
                if name:
                    self._lib_symbols[name] = symbol

        # Labels
        for kind, tag in (("local", "label"), ("global", "global_label"), ("hierarchical", "hierarchical_label")):
            for lbl in tree.all(tag):
                pos = lbl.xy() or (0.0, 0.0)
                rot = float(lbl.value_of("at", 3) or 0.0)
                design.labels.append(
                    Label(
                        text=str(lbl.value_of("text") or ""),
                        kind=kind,
                        sheet_path=path,
                        at=pos,
                        rotation=rot,
                    )
                )

        # Symbols
        for sym in tree.all("symbol"):
            instance = self._parse_symbol(sym, path)
            if instance is not None:
                design.symbols.append(instance)

        # Sub-sheets. Sheetname/Sheetfile are direct children of (sheet ...).
        for sub in tree.all("sheet"):
            filename = ""
            title = ""
            for prop in sub.all("property"):
                pair = prop.keyval()
                if not pair:
                    continue
                name, val = pair
                if name == "Sheetfile":
                    filename = val
                elif name == "Sheetname":
                    title = val
            if not filename:
                continue
            child_path = f"{path.rstrip('/')}/{title or os.path.splitext(os.path.basename(filename))[0]}"
            child_file = os.path.join(folder, filename)
            if os.path.isfile(child_file):
                self._walk(child_file, child_path, design, seen)

    def _parse_symbol(self, sym: SymList, sheet_path: str) -> SymInstance | None:
        lib_id = str(sym.value_of("lib_id") or "")
        if not lib_id:
            return None
        ref = ""
        value = ""
        props: dict[str, str] = {}
        for prop in sym.all("property"):
            pair = prop.keyval()
            if not pair:
                continue
            name, val = pair
            if name == "Reference":
                ref = val
            elif name == "Value":
                value = val
            props[name] = val
        if not ref:
            ref = str(sym.value_of("uuid") or "")
        at = sym.xy() or (0.0, 0.0)
        mirror = str(sym.value_of("mirror") or "")
        instance = SymInstance(
            ref=ref,
            value=value,
            lib_id=lib_id,
            sheet_path=sheet_path,
            uuid=str(sym.value_of("uuid") or ""),
            at=at,
            mirror=mirror,
            properties=props,
            unit=int(sym.value_of("unit") or 1),
        )
        # ``on_board``/``in_bom`` are normally "yes" and carry no exclusion meaning,
        # so only the explicit exclude flag counts here.
        instance.exclude_from_bom = bool(
            _sym_flag(sym, "exclude_from_bom", "exclude_from_board")
            or props.get("Exclude from BOM", "").lower() in ("yes", "true")
        )
        instance.dnp = bool(
            _sym_flag(sym, "dnp")
            or props.get("DNP", "").lower() in ("yes", "true")
            or props.get("Populate", "").lower() == "no"
        )
        instance.pins = self._resolve_pins(lib_id, props)
        return instance

    def _resolve_pins(self, lib_id: str, props: dict[str, str]) -> list[Pin]:
        """Determine pin names/types for a symbol instance.

        Prefers the instance's own unit pin mapping; otherwise falls back to the
        cached ``lib_symbols`` definition; otherwise leaves pins empty (the
        netlist import will still attach connectivity by number).
        """
        pins: list[Pin] = []
        definition = self._lib_symbols.get(lib_id)
        if definition is None:
            return pins
        for sub in definition.deep_all("symbol"):
            for pin in sub.all("pin"):
                number = str(pin.value_of("number") or "")
                name = str(pin.value_of("name") or "")
                etype = str(pin.value_of("electrical", 1) or "passive")
                pins.append(Pin(number=number, name=name, type=_pin_type(etype)))
        return pins

    # -- connectivity ------------------------------------------------------ #

    def _load_nets(self, root_sch: str, design: Design) -> None:
        """Attach nets from the kicad-cli netlist export."""
        try:
            xml_text = self.cli.export_netlist(root_sch, fmt="kicadxml")
        except Exception:
            return
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return

        nets_el = root.find("nets")
        if nets_el is None:
            return
        for net_el in nets_el.findall("net"):
            name = net_el.get("name", "")
            code = int(net_el.get("code", "0") or 0)
            nodes = []
            sheets: set[str] = set()
            for node in net_el.findall("node"):
                ref = node.get("ref", "")
                pin = node.get("pin", "")
                pintype = _pin_type(node.get("pintype", "passive"))
                pinfunc = node.get("pinfunction", "")
                nodes.append((ref, pin, pintype, pinfunc))
                sym = design.symbol_by_ref(ref)
                if sym is not None:
                    sheets.add(sym.sheet_path)
            design.nets.append(Net(name=name, code=code, nodes=nodes, sheets=sheets))

        self._attach_pin_names(design)

    def _attach_pin_names(self, design: Design) -> None:
        """Fill in pin names/types on symbols from the netlist electrical types."""
        by_ref = {s.ref: s for s in design.symbols}
        for net in design.nets:
            for ref, pin_num, pintype, pinfunc in net.nodes:
                sym = by_ref.get(ref)
                if sym is None:
                    continue
                for pin in sym.pins:
                    if pin.number == pin_num:
                        if pintype != PinType.UNSPECIFIED:
                            pin.type = pintype
                        if pinfunc and not pin.name:
                            pin.name = pinfunc
                        break
                else:
                    # pin not present in the cached definition - add it
                    sym.pins.append(Pin(number=pin_num, name=pinfunc, type=pintype))


def _pin_type(raw: str) -> PinType:
    try:
        return PinType(raw)
    except ValueError:
        return PinType.PASSIVE


def _sym_flag(sym: SymList, *names: str) -> bool:
    """Return True if any of the named boolean flags is present and set to yes.

    KiCad writes these as ``(dnp no)`` / ``(in_bom yes)``. Absent means False.
    """
    for name in names:
        node = sym.at(name)
        if node is not None and len(node) > 1 and str(node[1]) == "yes":
            return True
    return False


def _has_no_connect(tree: SymList) -> set[str]:
    """Return the UUIDs of symbols carrying a ``no_connect`` marker.

    No-connect flags are stored on the symbol instance, so the set is keyed by
    symbol UUID and resolved to references by the caller.
    """
    out: set[str] = set()
    for sym in tree.all("symbol"):
        if _sym_flag(sym, "no_connect"):
            uuid = str(sym.value_of("uuid") or "")
            if uuid:
                out.add(uuid)
    return out


def _root_uuid(tree: SymList) -> str:
    return str(tree.value_of("uuid") or "")


def _title_from_sch(tree: SymList) -> str:
    tb = tree.at("title_block")
    if tb is None:
        return ""
    return str(tb.value_of("title") or "")