"""Model Context Protocol (MCP) server exposing the schematic review as tools.

Run as::

    python -m kicad_ai.mcp_server --project /pfad/zum/projekt

Speaks MCP over stdio using JSON-RPC 2.0. Only the standard library is used so
the plugin keeps working without extra dependencies; the official ``mcp`` SDK is
used automatically when it is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from typing import Any

from .context import build_context
from .loader import KicadCli, SchematicLoader, resolve_project
from .llm import LLMConfig, build_client
from .model import Design
from .reviewer import ReviewResult, SchematicReviewer
from .rules import Severity

__all__ = ["MCPServer", "TOOLS", "main"]

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "kicad-ai-review", "version": "0.1.0"}


# --------------------------------------------------------------------------- #
# Tool definitions
# --------------------------------------------------------------------------- #

def TOOLS(project_hint: str | None = None) -> list[dict[str, Any]]:
    """Return the MCP tool descriptors offered to the client."""
    proj_desc = (
        "Pfad zum KiCad-Projekt (.kicad_pro, .kicad_sch, .kicad_pcb oder Ordner). "
        "Wird der Parameter weggelassen, wird das zuletzt geöffnete Projekt verwendet."
    )
    return [
        {
            "name": "list_projects",
            "description": "Listet KiCad-Projekte in einem Verzeichnis auf.",
            "inputSchema": {
                "type": "object",
                "properties": {"directory": {"type": "string", "description": "Zu durchsuchender Ordner"}},
                "required": ["directory"],
            },
        },
        {
            "name": "get_project_info",
            "description": "Gibt Kennzahlen zum Schaltplan zurück: Bauteile, Netze, Blätter, Statistik.",
            "inputSchema": {
                "type": "object",
                "properties": {"project": {"type": "string", "description": proj_desc}},
            },
        },
        {
            "name": "list_symbols",
            "description": "Listet alle Bauteile mit Referenz, Wert, Footprint und Pins auf.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": proj_desc},
                    "filter": {"type": "string", "description": "Teilstring für Referenz/Wert/Footprint"},
                    "limit": {"type": "integer", "description": "Max. Anzahl (Standard 200)"},
                },
            },
        },
        {
            "name": "list_nets",
            "description": "Listet Netze mit allen angeschlossenen Pins und Pin-Typen auf.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": proj_desc},
                    "filter": {"type": "string", "description": "Teilstring für den Netznamen"},
                    "limit": {"type": "integer", "description": "Max. Anzahl (Standard 200)"},
                },
            },
        },
        {
            "name": "get_net",
            "description": "Detailinfo zu einem Netz inkl. aller Pins und Blattzugehörigkeit.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": proj_desc},
                    "net": {"type": "string", "description": "Exakter Netzname"},
                },
                "required": ["net"],
            },
        },
        {
            "name": "trace_connection",
            "description": (
                "Prüft die Verbindung zwischen zwei Referenzdesignatoren (z. B. 'U1' und 'J3') "
                "und nennt alle Netze, die beide verbinden. Beantwortet questions wie "
                "'Wie ist der Sensor an den ADC angeschlossen?'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": proj_desc},
                    "from": {"type": "string", "description": "Referenz A, z. B. U1"},
                    "to": {"type": "string", "description": "Referenz B, z. B. J3"},
                },
                "required": ["from", "to"],
            },
        },
        {
            "name": "check_erc",
            "description": "Führt die offizielle KiCad-ERC-Prüfung aus und liefert die Verstöße.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": proj_desc},
                    "severity": {"type": "string", "enum": ["all", "error", "warning"]},
                },
            },
        },
        {
            "name": "review_schematic",
            "description": (
                "Vollständiges Schaltplan-Review: deterministische Prüfregeln plus optionale "
                "KI-Analyse. Liefert Fehler, Warnungen und abgeleitete Anforderungen."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": proj_desc},
                    "use_ai": {
                        "type": "boolean",
                        "description": "KI-Analyse verwenden (erfordert konfiguriertes Backend)",
                    },
                    "min_severity": {
                        "type": "string",
                        "enum": ["error", "warning", "info", "hint"],
                        "description": "Mindestschweregrad der Rückgabe",
                    },
                    "include_requirements": {"type": "boolean"},
                },
            },
        },
        {
            "name": "derive_requirements",
            "description": (
                "Leitet Anforderungen aus dem Schaltplan ab (Versorgung, Schnittstellen, "
                "Schutz, Takt, Terminierung, Produktion) und bewertet deren Erfüllung."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": proj_desc},
                    "use_ai": {"type": "boolean"},
                },
            },
        },
        {
            "name": "ask_about_schematic",
            "description": (
                "Stellt eine freie Frage zum Schaltplan und beantwortet sie auf Basis des "
                "extrahierten Netzkontexts (mit KI). Für Fragen, die keine der festen Tools abdecken."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": proj_desc},
                    "question": {"type": "string", "description": "Die Frage"},
                },
                "required": ["question"],
            },
        },
        {
            "name": "check_llm_status",
            "description": "Prüft, ob ein KI-Backend (Ollama oder API) erreichbar und konfiguriert ist.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #

class MCPServer:
    """A minimal, dependency-free MCP server over stdio."""

    def __init__(self, default_project: str | None = None) -> None:
        self.default_project = default_project
        self.reviewer = SchematicReviewer()
        self._design_cache: dict[str, Design] = {}
        self._lock = threading.Lock()
        self._running = True

    # -- helpers ---------------------------------------------------------- #

    def _resolve(self, args: dict) -> str:
        project = args.get("project") or self.default_project
        if not project:
            raise ValueError(
                "Kein Projekt angegeben und kein Standardprojekt konfiguriert. "
                "Bitte 'project' setzen oder den Server mit --project starten."
            )
        root, _ = resolve_project(project)
        with self._lock:
            cached = self._design_cache.get(root)
        if cached is not None:
            return root
        _log(f"lade Projekt {root}")
        design = SchematicLoader(self.reviewer.cli).load(root)
        with self._lock:
            self._design_cache.clear()
            self._design_cache[root] = design
        return root

    def _design(self, args: dict) -> Design:
        root = self._resolve(args)
        with self._lock:
            return self._design_cache[root]

    # -- JSON-RPC plumbing ------------------------------------------------ #

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params") or {}

        if method == "initialize":
            result: Any = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "KI-gestütztes KiCad-Schaltplan-Review. Nutze zuerst "
                    "review_schematic für einen Überblick, danach get_net/trace_connection "
                    "für Detailfragen."
                ),
            }
        elif method in ("notifications/initialized", "initialized"):
            return None
        elif method == "tools/list":
            result = {"tools": TOOLS()}
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                text = self.call_tool(name, args)
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            except Exception as exc:  # noqa: BLE001 - report to client
                result = {
                    "content": [{"type": "text", "text": f"Fehler: {exc}"}],
                    "isError": True,
                }
        elif method == "ping":
            result = {}
        elif method == "shutdown":
            self._running = False
            result = {}
        else:
            if req_id is None:
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unbekannte Methode: {method}"},
            }

        if req_id is None:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    # -- tools ------------------------------------------------------------ #

    def call_tool(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            raise ValueError(f"Unbekanntes Werkzeug: {name}")
        return handler(args)

    def _tool_list_projects(self, args: dict) -> str:
        directory = args.get("directory", ".")
        out = []
        for entry in sorted(os.listdir(directory)):
            full = os.path.join(directory, entry)
            if entry.endswith(".kicad_pro") and os.path.isfile(full):
                sch = full[: -len(".kicad_pro")] + ".kicad_sch"
                out.append(
                    {
                        "project": full,
                        "has_schematic": os.path.isfile(sch),
                        "name": entry[: -len(".kicad_pro")],
                    }
                )
        return _json({"directory": os.path.abspath(directory), "projects": out})

    def _tool_get_project_info(self, args: dict) -> str:
        design = self._design(args)
        return _json(
            {
                "root_schematic": design.root_file,
                "title_block": design.title_block,
                "statistics": design.stats(),
                "sheets": [
                    {"path": s.path, "title": s.title, "ports": list(s.port_names)} for s in design.sheets
                ],
                "power_nets": [n.name for n in design.power_nets()],
                "ground_nets": [n.name for n in design.ground_nets()],
            }
        )

    def _tool_list_symbols(self, args: dict) -> str:
        design = self._design(args)
        needle = (args.get("filter") or "").lower()
        limit = int(args.get("limit") or 200)
        items = []
        for sym in design.component_symbols:
            haystack = f"{sym.ref} {sym.value} {sym.lib_id} {sym.footprint}".lower()
            if needle and needle not in haystack:
                continue
            items.append(
                {
                    "ref": sym.ref,
                    "value": sym.value,
                    "library_id": sym.lib_id,
                    "footprint": sym.footprint,
                    "sheet": sym.sheet_path,
                    "datasheet": sym.datasheet,
                    "description": sym.description,
                    "dnp": sym.dnp,
                    "pins": [
                        {"number": p.number, "name": p.name, "type": p.type.value} for p in sym.pins
                    ],
                }
            )
            if len(items) >= limit:
                break
        return _json({"count": len(items), "symbols": items})

    def _tool_list_nets(self, args: dict) -> str:
        design = self._design(args)
        needle = (args.get("filter") or "").lower()
        limit = int(args.get("limit") or 200)
        items = []
        for net in design.nets:
            if needle and needle not in net.name.lower():
                continue
            items.append(
                {
                    "net": net.name,
                    "degree": net.degree,
                    "is_power": net.is_power,
                    "is_ground": net.is_ground,
                    "sheets": sorted(net.sheets),
                    "nodes": [
                        {"ref": r, "pin": p, "type": t.value, "function": f}
                        for r, p, t, f in net.nodes
                    ],
                }
            )
            if len(items) >= limit:
                break
        return _json({"count": len(items), "nets": items})

    def _tool_get_net(self, args: dict) -> str:
        design = self._design(args)
        name = args.get("net", "")
        net = design.net_by_name(name)
        if net is None:
            matches = [n.name for n in design.nets if name.lower() in n.name.lower()]
            return _json({"found": False, "net": name, "similar": matches[:20]})
        return _json(
            {
                "found": True,
                "net": net.name,
                "degree": net.degree,
                "is_power": net.is_power,
                "is_ground": net.is_ground,
                "has_source": net.has_source,
                "sheets": sorted(net.sheets),
                "nodes": [
                    {
                        "ref": r,
                        "pin": p,
                        "type": t.value,
                        "function": f,
                        "value": getattr(design.symbol_by_ref(r), "value", ""),
                    }
                    for r, p, t, f in net.nodes
                ],
            }
        )

    def _tool_trace_connection(self, args: dict) -> str:
        design = self._design(args)
        a = str(args.get("from", "")).strip().upper()
        b = str(args.get("to", "")).strip().upper()
        nets_a = {n.name: n for n in design.nets_touching([a])}
        nets_b = {n.name: n for n in design.nets_touching([b])}
        shared = sorted(set(nets_a) & set(nets_b))
        sym_a = design.symbol_by_ref(a)
        sym_b = design.symbol_by_ref(b)
        return _json(
            {
                "from": a,
                "from_exists": sym_a is not None,
                "from_value": getattr(sym_a, "value", ""),
                "from_nets": sorted(nets_a),
                "to": b,
                "to_exists": sym_b is not None,
                "to_value": getattr(sym_b, "value", ""),
                "to_nets": sorted(nets_b),
                "shared_nets": shared,
                "connected": bool(shared),
                "paths": [
                    {
                        "net": name,
                        "nodes": [f"{r}.{p} ({t.value})" for r, p, t, _ in nets_a[name].nodes],
                    }
                    for name in shared
                ],
            }
        )

    def _tool_check_erc(self, args: dict) -> str:
        design = self._design(args)
        severity = args.get("severity", "all")
        try:
            violations = self.reviewer.cli.run_erc(design.root_file, severity=severity)
        except Exception as exc:  # noqa: BLE001
            return _json({"error": str(exc), "violations": []})
        counts: dict[str, int] = {}
        for v in violations:
            counts[v.get("severity", "?")] = counts.get(v.get("severity", "?"), 0) + 1
        return _json({"count": len(violations), "counts": counts, "violations": violations})

    def _tool_review_schematic(self, args: dict) -> str:
        root = self._resolve(args)
        use_ai = bool(args.get("use_ai", True))
        include_reqs = bool(args.get("include_requirements", True))
        min_sev = Severity.parse(str(args.get("min_severity", "hint")))

        _log(f"review {root} (ai={use_ai})")
        result = self.reviewer.review(root, use_ai=use_ai)
        data = result.to_dict()
        data["findings"] = [f for f in data["findings"] if Severity[f["severity"]] >= min_sev]
        if not include_reqs:
            data.pop("requirements", None)
        return _json(data)

    def _tool_derive_requirements(self, args: dict) -> str:
        root = self._resolve(args)
        use_ai = bool(args.get("use_ai", True))
        result = self.reviewer.review(root, use_ai=use_ai)
        static = static_requirements(result)
        return _json(
            {
                "summary": result.summary,
                "requirements": [r.to_dict() for r in result.requirements],
                "static_analysis": static,
                "llm_error": result.llm_error,
                "counts": {
                    s: sum(1 for r in result.requirements if r.status == s)
                    for s in ("erfuellt", "teilweise", "fehlt", "unklar")
                },
            }
        )

    def _tool_ask_about_schematic(self, args: dict) -> str:
        design = self._design(args)
        question = str(args.get("question", "")).strip()
        if not question:
            raise ValueError("Parameter 'question' fehlt")
        config = LLMConfig.from_env()
        if config.backend in ("none", ""):
            raise ValueError("Kein KI-Backend konfiguriert (KICAD_AI_BACKEND setzen)")
        client = build_client(config)
        ok, detail = client.available()
        if not ok:
            raise ValueError(f"Backend nicht verfügbar: {detail}")
        ctx = build_context(design)
        messages = [
            {
                "role": "system",
                "content": (
                    "Du bist ein Elektronik-Experte für KiCad-Schaltpläne. "
                    "Beantworte die Frage präzise anhand des Kontexts. "
                    "Nenne konkrete Referenzdesignatoren und Netznamen. "
                    "Antworte auf Deutsch. Wenn der Kontext die Frage nicht beantwortet, "
                    "sage das explizit."
                ),
            },
            {"role": "user", "content": f"{ctx.text}\n\n---\nFrage: {question}"},
        ]
        response = client.chat(messages, format=None)
        if not response.ok:
            raise ValueError(response.error)
        return response.text

    def _tool_check_llm_status(self, args: dict) -> str:
        config = LLMConfig.from_env()
        info = {
            "backend": config.backend,
            "model": config.model,
            "base_url": config.base_url,
            "api_key_set": bool(config.api_key),
        }
        if config.backend in ("none", ""):
            info["available"] = False
            info["detail"] = "Kein Backend konfiguriert"
        else:
            try:
                ok, detail = build_client(config).available()
                info["available"] = ok
                info["detail"] = detail
            except Exception as exc:  # noqa: BLE001
                info["available"] = False
                info["detail"] = str(exc)
        return _json(info)

    # -- loop -------------------------------------------------------------- #

    def serve(self, stdin=None, stdout=None) -> None:
        """Run the JSON-RPC loop until EOF or shutdown."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                _write(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            response = self.handle(request)
            if response is not None:
                _write(stdout, response)
            stdout.flush()
            if not self._running:
                break


def static_requirements(result: ReviewResult) -> list[dict]:
    """Derive requirements deterministically from the design, without an LLM.

    These are factual statements about the design; they complement the
    LLM-derived requirements and are always available.
    """
    design = result.design
    reqs: list[dict] = []

    def add(rid: str, text: str, status: str, evidence: str = "", gap: str = "") -> None:
        reqs.append({"id": rid, "requirement": text, "status": status, "evidence": evidence, "gap": gap})

    for net in design.power_nets():
        if net.is_ground:
            continue
        caps = [
            r
            for r in net.refs
            if r.upper().startswith("C")
        ]
        status = "erfuellt" if caps else "fehlt"
        add(
            f"REQ-PWR-{len(reqs) + 1:03d}",
            f"Versorgungsnetz {net.name} wird lokal entkoppelt",
            status,
            f"{net.name} -> {', '.join(sorted(net.refs)[:8])}",
            "" if caps else f"Kein Abblockkondensator an {net.name}",
        )
        if net.has_source:
            add(
                f"REQ-PWR-SRC-{len(reqs) + 1:03d}",
                f"Versorgungsnetz {net.name} besitzt eine treibende Quelle",
                "erfuellt" if net.has_source else "fehlt",
                ", ".join(f"{r}.{p}" for r, p, t, _ in net.nodes if t.is_source),
            )
        else:
            add(
                f"REQ-PWR-SRC-{len(reqs) + 1:03d}",
                f"Versorgungsnetz {net.name} besitzt eine treibende Quelle",
                "fehlt",
                net.describe(),
                "Kein POWER_OUT-Pin am Netz",
            )

    connectors = [s for s in design.component_symbols if s.is_connector]
    for conn in connectors:
        unnamed = [p for p in conn.pins if not p.name.strip()]
        add(
            f"REQ-CONN-{len(reqs) + 1:03d}",
            f"Connector {conn.ref} ist mit sprechenden Pin-Namen dokumentiert",
            "erfuellt" if not unnamed else "teilweise",
            f"{conn.ref} ({conn.value}), {len(unnamed)} unbenannte Pins",
            "" if not unnamed else "Pin-Namen ergänzen",
        )

    no_fp = [s for s in design.component_symbols if not s.footprint.strip() and not s.exclude_from_bom]
    add(
        f"REQ-BOM-{len(reqs) + 1:03d}",
        "Jedes Bauteil hat ein zugewiesenes Footprint",
        "erfuellt" if not no_fp else "teilweise",
        f"{len(no_fp)} Bauteile ohne Footprint",
        ", ".join(s.ref for s in no_fp[:10]) if no_fp else "",
    )

    add(
        f"REQ-TEST-{len(reqs) + 1:03d}",
        "Testpunkte für Versorgung und kritische Signale sind vorhanden",
        "erfuellt" if any(s.is_testpoint for s in design.component_symbols) else "unklar",
        f"{sum(1 for s in design.component_symbols if s.is_testpoint)} Testpunkte",
    )

    counts = {s: sum(1 for r in reqs if r["status"] == s) for s in ("erfuellt", "teilweise", "fehlt", "unklar")}
    return reqs


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _write(stream, data: dict) -> None:
    stream.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")


def _log(message: str) -> None:
    """Diagnostics go to stderr; stdout is reserved for JSON-RPC."""
    print(f"[kicad-ai-review] {message}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kicad-ai-mcp",
        description="MCP-Server für KiCad-Schaltplan-Review",
    )
    parser.add_argument("--project", help="Standardprojekt, falls der Client keins sendet")
    parser.add_argument("--list-tools", action="store_true", help="Werkzeuge auflisten und beenden")
    args = parser.parse_args(argv)

    if args.list_tools:
        print(_json(TOOLS()))
        return 0

    _log("MCP-Server startet auf stdio")
    MCPServer(args.project).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())