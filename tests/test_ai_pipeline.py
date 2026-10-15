"""End-to-end test of the AI stage with a stubbed LLM backend.

Verifies that the full pipeline (load -> rules -> AI -> merge -> report) works
and that a realistic model response is parsed into findings and requirements.
No network access required.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import kicad_ai.llm as llm_module  # noqa: E402
from kicad_ai.llm import LLMConfig, LLMResponse  # noqa: E402
from kicad_ai.report import render_report  # noqa: E402
from kicad_ai.reviewer import SchematicReviewer  # noqa: E402
from kicad_ai.rules import Severity  # noqa: E402

DEMO = r"C:\Program Files\KiCad\10.0\share\kicad\demos\simulation\amplifier-ac\amplifier-ac.kicad_sch"

FAKE_REPLY = """{
  "summary": "Der Verstaerker ist sauber aufgebaut, aber die Simulation nutzt keine entkoppelte Versorgung.",
  "findings": [
    {
      "rule": "supply decoupling",
      "severity": "warning",
      "title": "+12V ohne Abblockung",
      "detail": "Die Rail speist mehrere Transistoren ohne 100nF-Kondensator.",
      "category": "power",
      "refs": ["R1", "V3"],
      "nets": ["+12V"],
      "fix": "100nF parallel zu +12V/GND ergaenzen"
    },
    {
      "rule": "bias network",
      "severity": "info",
      "title": "Bias-Netzwerk ohne Bypass",
      "detail": "Die Basisspannung ist nicht entkoppelt.",
      "category": "power",
      "nets": ["/VBASE"],
      "fix": "Abblockkondensator am Bias-Netz"
    }
  ],
  "requirements": [
    {"id": "REQ-001", "requirement": "+12V wird entkoppelt", "status": "fehlt",
     "evidence": "+12V -> R1, R3", "gap": "Kein Abblockkondensator"},
    {"id": "REQ-002", "requirement": "Bias-Netzwerk vorhanden", "status": "erfuellt",
     "evidence": "/VBASE mit R5, Q1", "gap": ""}
  ],
  "questions": ["Soll der Ausgang symmetrisch zu +/-12V aufgebaut werden?"]
}"""


class StubClient:
    """Stands in for OllamaClient / OpenAIClient."""

    backend = "stub"
    calls = []

    def __init__(self, config):
        self.config = config
        self.received = None

    def available(self):
        return True, "stub"

    def chat(self, messages, **kwargs):
        StubClient.calls.append(messages)
        self.received = messages
        return LLMResponse(text=FAKE_REPLY, model="stub-1", backend="stub")


class TestAIEndToEnd(unittest.TestCase):
    def setUp(self):
        # Patch per test so a failure in one case cannot leak into the next.
        self._original = llm_module.build_client
        llm_module.build_client = StubClient
        StubClient.calls.clear()
        if not os.path.isfile(DEMO):
            llm_module.build_client = self._original
            self.skipTest("KiCad demo not available")

    def tearDown(self):
        llm_module.build_client = self._original

    def _review(self):
        reviewer = SchematicReviewer(llm_config=LLMConfig(backend="ollama", model="stub-1"))
        return reviewer.review(DEMO, use_ai=True)

    def test_ai_findings_are_merged(self):
        result = self._review()
        self.assertTrue(result.llm_used, result.llm_error)
        ai = [f for f in result.findings if f.source == "ai"]
        self.assertEqual(len(ai), 2)
        titles = {f.title for f in ai}
        self.assertIn("+12V ohne Abblockung", titles)

    def test_requirements_parsed(self):
        result = self._review()
        self.assertEqual(len(result.requirements), 2)
        statuses = {r.id: r.status for r in result.requirements}
        self.assertEqual(statuses["REQ-001"], "fehlt")
        self.assertEqual(statuses["REQ-002"], "erfuellt")

    def test_questions_parsed(self):
        result = self._review()
        self.assertEqual(len(result.questions), 1)

    def test_summary_replaced(self):
        result = self._review()
        self.assertIn("Verstaerker", result.summary)

    def test_findings_sorted_by_severity(self):
        result = self._review()
        severities = [int(f.severity) for f in result.findings]
        self.assertEqual(severities, sorted(severities, reverse=True))

    def test_prompt_contains_design_facts(self):
        self._review()
        self.assertTrue(StubClient.calls)
        user_message = StubClient.calls[0][-1]["content"]
        self.assertIn("=== NETZE ===", user_message)
        self.assertIn("GND", user_message)
        # The prompt must instruct structured JSON output.
        system = StubClient.calls[0][0]["content"]
        self.assertIn("JSON", system)

    def test_reports_render_with_ai_results(self):
        result = self._review()
        for fmt in ("text", "markdown", "html", "json"):
            out = render_report(result, fmt=fmt)
            self.assertIn("REQ-001", out, f"{fmt} missing requirements")

    def test_unavailable_backend_is_reported_not_raised(self):
        class DeadClient(StubClient):
            def available(self):
                return False, "Verbindung verweigert"

        llm_module.build_client = DeadClient
        reviewer = SchematicReviewer(llm_config=LLMConfig(backend="ollama"))
        result = reviewer.review(DEMO, use_ai=True)
        self.assertFalse(result.llm_used)
        self.assertIn("nicht verfügbar", result.llm_error)
        self.assertTrue(result.findings, "rule findings must survive")

    def test_malformed_json_is_reported(self):
        class GarbageClient(StubClient):
            def chat(self, messages, **kwargs):
                return LLMResponse(text="Ich kann dir nicht helfen.", backend="stub")

        llm_module.build_client = GarbageClient
        reviewer = SchematicReviewer(llm_config=LLMConfig(backend="ollama"))
        result = reviewer.review(DEMO, use_ai=True)
        self.assertFalse(result.llm_used)
        self.assertIn("JSON", result.llm_error)
        self.assertTrue(result.findings)

    def test_no_ai_skips_backend(self):
        before = len(StubClient.calls)
        reviewer = SchematicReviewer(llm_config=LLMConfig(backend="ollama", model="stub-1"))
        result = reviewer.review(DEMO, use_ai=False)
        self.assertEqual(len(StubClient.calls), before, "backend must not be called")
        self.assertFalse(result.llm_used)


if __name__ == "__main__":
    unittest.main(verbosity=2)