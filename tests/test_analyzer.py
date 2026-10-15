"""Tests for the analyzer: parser, loader, rules, reports and the AI stage.

Run with KiCad's bundled interpreter::

    python tests/test_analyzer.py
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from kicad_ai import KicadCli, LLMConfig, SchematicLoader, Severity  # noqa: E402
from kicad_ai.context import build_context, parse_review_response  # noqa: E402
from kicad_ai.llm import OllamaClient, OpenAIClient  # noqa: E402
from kicad_ai.model import Design, Net, PinType, SymInstance  # noqa: E402
from kicad_ai.report import render_report  # noqa: E402
from kicad_ai.reviewer import ReviewResult, SchematicReviewer, _ai_to_finding  # noqa: E402
from kicad_ai.rules import RuleContext, check_all  # noqa: E402
from kicad_ai.sexpr import parse  # noqa: E402

DEMOS = r"C:\Program Files\KiCad\10.0\share\kicad\demos"
HIERARCHY = os.path.join(DEMOS, "complex_hierarchy", "complex_hierarchy.kicad_sch")
AMPLIFIER = os.path.join(DEMOS, "simulation", "amplifier-ac", "amplifier-ac.kicad_sch")


class TestSexpr(unittest.TestCase):
    """The parser returns the outer list, so helpers address its children.

    ``parse("(symbol (lib_id \\"Device:R\\"))")`` -> ``value_of("lib_id")``.
    """

    def test_parses_quoted_string_values(self):
        tree = parse('(symbol (lib_id "Device:R"))')
        self.assertEqual(tree.head(), "symbol")
        self.assertEqual(tree.value_of("lib_id"), "Device:R")

    def test_parses_bare_atom_values(self):
        tree = parse("(symbol (lib_id Device:R))")
        self.assertEqual(tree.value_of("lib_id"), "Device:R")

    def test_parses_numbers(self):
        tree = parse("(sym (at 143.51 71.12 0))")
        self.assertEqual(tree.xy("at"), (143.51, 71.12))

    def test_escapes_in_strings(self):
        tree = parse(r'(sym (text "a\"b\\c"))')
        self.assertEqual(tree.value_of("text"), 'a"b\\c')

    def test_keyval_reads_direct_atom_pairs(self):
        tree = parse('(property "Reference" "R1")')
        self.assertEqual(tree.keyval(), ("Reference", "R1"))

    def test_nested_lists(self):
        tree = parse("(a (b 1) (b 2))")
        self.assertEqual(len(list(tree.all("b"))), 2)
        self.assertEqual(len(list(tree.deep_all("b"))), 2)

    def test_rejects_garbage(self):
        with self.assertRaises(Exception):
            parse("not a list")


class TestModel(unittest.TestCase):
    def test_pin_type_flags(self):
        self.assertTrue(PinType.POWER_OUT.is_source)
        self.assertTrue(PinType.POWER_IN.is_sink)
        self.assertFalse(PinType.PASSIVE.is_source)

    def test_net_detection(self):
        power = Net(name="+3V3", nodes=[("U1", "1", PinType.POWER_OUT, "")])
        self.assertTrue(power.is_power)
        self.assertTrue(power.has_source)

        gnd = Net(name="GND", nodes=[("C1", "2", PinType.PASSIVE, "")])
        self.assertTrue(gnd.is_ground)

    def test_designator_prefix(self):
        self.assertEqual(SymInstance(ref="R12", value="", lib_id="", sheet_path="/").designator_prefix, "R")
        self.assertEqual(SymInstance(ref="#PWR01", value="", lib_id="", sheet_path="/").designator_prefix, "PWR")


def _have(path: str) -> bool:
    return os.path.isfile(path)


class TestLoader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = KicadCli()
        cls.design = SchematicLoader(cls.cli).load(HIERARCHY) if _have(HIERARCHY) else None

    @unittest.skipUnless(_have(HIERARCHY), "KiCad demo not available")
    def test_loads_hierarchy(self):
        self.assertIsNotNone(self.design)
        self.assertGreaterEqual(len(self.design.sheets), 2)
        self.assertGreater(len(self.design.component_symbols), 20)

    @unittest.skipUnless(_have(HIERARCHY), "KiCad demo not available")
    def test_symbols_have_refs_and_values(self):
        for sym in self.design.component_symbols:
            self.assertTrue(sym.ref, "symbol without reference")
            self.assertNotIn("?", sym.ref)
        self.assertTrue(any(s.footprint for s in self.design.component_symbols))

    @unittest.skipUnless(_have(HIERARCHY), "KiCad demo not available")
    def test_nets_have_nodes(self):
        self.assertTrue(self.design.nets)
        for net in self.design.nets:
            self.assertGreater(net.degree, 0, f"net {net.name} has no nodes")

    @unittest.skipUnless(_have(HIERARCHY), "KiCad demo not available")
    def test_hierarchical_net_names(self):
        names = [n.name for n in self.design.nets]
        self.assertTrue(
            any("/" in n[1:] for n in names),
            f"expected hierarchical net names, got {names[:10]}",
        )

    @unittest.skipUnless(_have(HIERARCHY), "KiCad demo not available")
    def test_erc_runs(self):
        violations = self.cli.run_erc(self.design.root_file, severity="all")
        self.assertIsInstance(violations, list)


class TestRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = SchematicLoader(KicadCli()).load(AMPLIFIER) if _have(AMPLIFIER) else None

    @unittest.skipUnless(_have(AMPLIFIER), "KiCad demo not available")
    def test_finds_missing_footprints(self):
        findings = check_all(RuleContext(design=self.design))
        rules = {f.rule for f in findings}
        self.assertIn("BOM-NO-FOOTPRINT", rules)

    @unittest.skipUnless(_have(AMPLIFIER), "KiCad demo not available")
    def test_findings_are_sorted_and_populated(self):
        findings = check_all(RuleContext(design=self.design))
        self.assertTrue(findings)
        severities = [int(f.severity) for f in findings]
        self.assertEqual(severities, sorted(severities, reverse=True))
        for finding in findings:
            self.assertTrue(finding.title)
            self.assertTrue(finding.detail)
            self.assertIn(finding.source, ("rule", "erc", "ai"))

    def test_detects_short_net(self):
        design = Design(root_file="mem.kicad_sch")
        design.nets = [Net(name="SIG", nodes=[("R1", "1", PinType.PASSIVE, "")])]
        rules = {f.rule for f in check_all(RuleContext(design=design))}
        self.assertIn("NET-SINGLE-PIN", rules)

    def test_detects_two_sources(self):
        design = Design(root_file="mem.kicad_sch")
        design.nets = [
            Net(name="VBUS", nodes=[
                ("U1", "1", PinType.POWER_OUT, ""),
                ("U2", "1", PinType.POWER_OUT, ""),
                ("C1", "1", PinType.PASSIVE, ""),
            ])
        ]
        rules = {f.rule for f in check_all(RuleContext(design=design))}
        self.assertIn("NET-MULTI-SOURCE", rules)

    def test_detects_missing_decoupling(self):
        design = Design(root_file="mem.kicad_sch")
        design.nets = [
            Net(name="+3V3", nodes=[
                ("U1", "1", PinType.POWER_IN, ""),
                ("U1", "2", PinType.PASSIVE, ""),
            ])
        ]
        rules = {f.rule for f in check_all(RuleContext(design=design))}
        self.assertIn("POWER-NO-DECOUPLING", rules)

    def test_detects_duplicate_ref(self):
        """Same reference and same unit on one sheet is a real annotation clash."""
        design = Design(root_file="mem.kicad_sch")
        props = {"Footprint": "R_0805", "Datasheet": "http://x"}
        design.symbols = [
            SymInstance(ref="R1", value="10k", lib_id="Device:R", sheet_path="/", properties=props),
            SymInstance(ref="R1", value="22k", lib_id="Device:R", sheet_path="/", properties=props),
        ]
        rules = {f.rule for f in check_all(RuleContext(design=design))}
        self.assertIn("SYM-DUPLICATE-REF", rules)

    def test_multi_unit_symbol_is_not_duplicate(self):
        """A multi-unit op amp placed twice is legitimate, not a duplicate ref."""
        design = Design(root_file="mem.kicad_sch")
        props = {"Footprint": "SOIC-8", "Datasheet": "http://x"}
        design.symbols = [
            SymInstance(ref="U1", value="TL072", lib_id="Opamp", sheet_path="/",
                        properties=props, unit=1),
            SymInstance(ref="U1", value="TL072", lib_id="Opamp", sheet_path="/",
                        properties=props, unit=2),
        ]
        rules = {f.rule for f in check_all(RuleContext(design=design))}
        self.assertNotIn("SYM-DUPLICATE-REF", rules)

    def test_same_unit_twice_is_duplicate(self):
        design = Design(root_file="mem.kicad_sch")
        props = {"Footprint": "SOIC-8", "Datasheet": "http://x"}
        design.symbols = [
            SymInstance(ref="U1", value="TL072", lib_id="Opamp", sheet_path="/",
                        properties=props, unit=1),
            SymInstance(ref="U1", value="TL072", lib_id="Opamp", sheet_path="/",
                        properties=props, unit=1),
        ]
        rules = {f.rule for f in check_all(RuleContext(design=design))}
        self.assertIn("SYM-DUPLICATE-REF", rules)

    def test_detects_prefix_only_value(self):
        """"R" on R1 is a placeholder: the BOM would not be orderable."""
        design = Design(root_file="mem.kicad_sch")
        props = {"Footprint": "R_0805", "Datasheet": "http://x"}
        design.symbols = [
            SymInstance(ref="R1", value="R", lib_id="Device:R", sheet_path="/", properties=props),
            SymInstance(ref="R2", value="10k", lib_id="Device:R", sheet_path="/", properties=props),
        ]
        rules = {f.rule for f in check_all(RuleContext(design=design))}
        self.assertIn("BOM-GENERIC-VALUE", rules)

    def test_concrete_value_is_accepted(self):
        design = Design(root_file="mem.kicad_sch")
        props = {"Footprint": "R_0805", "Datasheet": "http://x"}
        design.symbols = [
            SymInstance(ref="R1", value="10k", lib_id="Device:R", sheet_path="/", properties=props),
            SymInstance(ref="C1", value="100nF", lib_id="Device:C", sheet_path="/", properties=props),
        ]
        rules = {f.rule for f in check_all(RuleContext(design=design))}
        self.assertNotIn("BOM-GENERIC-VALUE", rules)

    def test_value_example_is_type_specific(self):
        from kicad_ai.rules import _value_example

        self.assertIn("10k", _value_example("R"))
        self.assertIn("100nF", _value_example("C"))
        self.assertIn("10uH", _value_example("L"))

    def test_no_connect_suppresses_floating_pin(self):
        design = Design(root_file="mem.kicad_sch")
        design.symbols = [
            SymInstance(ref="U1", value="MCU", lib_id="MCU", sheet_path="/",
                        properties={"Footprint": "LQFP", "Datasheet": "http://x"}),
        ]
        design.symbols[0].pins = []
        design.nets = []
        design.no_connects.add(("U1", "5"))
        rules = {f.rule for f in check_all(RuleContext(design=design))}
        self.assertNotIn("PIN-FLOATING", rules)


class TestContext(unittest.TestCase):
    def test_parses_plain_json(self):
        data = parse_review_response('{"summary":"ok","findings":[]}')
        self.assertEqual(data["summary"], "ok")

    def test_parses_fenced_json(self):
        data = parse_review_response('```json\n{"summary":"ok"}\n```')
        self.assertEqual(data["summary"], "ok")

    def test_parses_json_with_prose(self):
        data = parse_review_response('Hier das Ergebnis: {"summary":"ok"} - fertig')
        self.assertEqual(data["summary"], "ok")

    def test_rejects_non_json(self):
        self.assertEqual(parse_review_response("kein json"), {})

    @unittest.skipUnless(_have(AMPLIFIER), "KiCad demo not available")
    def test_builds_context(self):
        design = SchematicLoader(KicadCli()).load(AMPLIFIER)
        ctx = build_context(design)
        self.assertIn("components", ctx.data)
        self.assertIn("nets", ctx.data)
        self.assertGreater(len(ctx.data["components"]), 5)
        json.loads(ctx.to_json())


class TestAIStage(unittest.TestCase):
    def test_maps_ai_payload_to_findings(self):
        finding = _ai_to_finding({
            "rule": "decoupling",
            "severity": "warning",
            "title": "Fehlender Abblock",
            "detail": "Kein 100nF an U1",
            "category": "power",
            "refs": ["U1"],
            "nets": ["+3V3"],
            "fix": "100nF ergänzen",
        })
        self.assertIsNotNone(finding)
        self.assertEqual(finding.severity, Severity.WARNING)
        self.assertEqual(finding.source, "ai")
        self.assertEqual(finding.refs, ["U1"])

    def test_maps_ai_requirements(self):
        design = Design(root_file="mem.kicad_sch")
        result = ReviewResult(design=design)
        from kicad_ai.reviewer import Requirement

        result.requirements = [Requirement(id="REQ-1", requirement="x", status="fehlt")]
        self.assertEqual(result.counts()["error"], 0)


class TestReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = SchematicLoader(KicadCli()).load(AMPLIFIER) if _have(AMPLIFIER) else Design(root_file="m.kicad_sch")

    def _result(self) -> ReviewResult:
        findings, _ = SchematicReviewer().run_rules(self.design)
        result = ReviewResult(design=self.design, findings=findings, summary="Testzusammenfassung")
        return result

    def test_text_report(self):
        out = render_report(self._result(), fmt="text")
        self.assertIn("KiCad AI Review", out)
        self.assertIn("Testzusammenfassung", out)

    def test_markdown_report(self):
        out = render_report(self._result(), fmt="markdown")
        self.assertIn("# KiCad Schaltplan-Review", out)
        self.assertIn("| Bauteile |", out)

    def test_html_report(self):
        out = render_report(self._result(), fmt="html")
        self.assertIn("<!DOCTYPE html>", out)
        self.assertIn("</html>", out)

    def test_json_report(self):
        payload = json.loads(render_report(self._result(), fmt="json"))
        self.assertIn("findings", payload)
        self.assertIn("statistics", payload)


class TestLLMClients(unittest.TestCase):
    def test_ollama_url_from_env(self):
        config = LLMConfig.from_env()
        self.assertTrue(config.base_url)

    def test_openai_headers(self):
        client = OpenAIClient(LLMConfig(backend="openai", api_key="secret"))
        self.assertIn("Authorization", client._headers())
        self.assertEqual(client._headers()["Authorization"], "Bearer secret")

    def test_ollama_payload_shape(self):
        client = OllamaClient(LLMConfig(backend="ollama", model="m"))
        self.assertEqual(client.backend, "ollama")
        self.assertIn("/api/chat", f"{client.api}/api/chat")


class TestExitCodes(unittest.TestCase):
    def test_exit_code_gating(self):
        design = Design(root_file="m.kicad_sch")
        result = ReviewResult(design=design)
        self.assertEqual(result.exit_code("error"), 0)
        from kicad_ai.rules import Finding

        result.findings = [Finding(rule="X", severity=Severity.ERROR, title="t", detail="d")]
        self.assertEqual(result.exit_code("error"), 1)
        self.assertEqual(result.exit_code("info"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)