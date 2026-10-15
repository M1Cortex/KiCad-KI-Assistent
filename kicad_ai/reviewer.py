"""Orchestration: load design, run rules, optionally consult the LLM, report."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable

from .context import DesignContext, build_context, parse_review_response, render_prompt
from .loader import KicadCli, SchematicLoader
from . import llm as _llm_module
from .llm import LLMConfig, LLMError
from .model import Design
from .rules import Finding, RuleContext, Severity, check_all

__all__ = ["ReviewResult", "Requirement", "SchematicReviewer", "ReviewCancelled"]

ProgressFn = Callable[[str, int], None]


class ReviewCancelled(Exception):
    """Raised when the user aborts a running review."""


@dataclass
class Requirement:
    """A requirement derived from the schematic."""

    id: str
    requirement: str
    status: str  # erfuellt | teilweise | fehlt | unklar
    evidence: str = ""
    gap: str = ""
    source: str = "ai"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement": self.requirement,
            "status": self.status,
            "evidence": self.evidence,
            "gap": self.gap,
            "source": self.source,
        }


@dataclass
class ReviewResult:
    """Everything a review produced."""

    design: Design
    findings: list[Finding] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    summary: str = ""
    erc_violations: list[dict] = field(default_factory=list)
    llm_used: bool = False
    llm_backend: str = ""
    llm_model: str = ""
    llm_error: str = ""
    duration_s: float = 0.0
    tool_calls: list[str] = field(default_factory=list)

    # -- aggregation ------------------------------------------------------ #

    def counts(self) -> dict[str, int]:
        out = {"error": 0, "warning": 0, "info": 0, "hint": 0}
        for f in self.findings:
            out[f.severity.name.lower()] = out.get(f.severity.name.lower(), 0) + 1
        return out

    def filtered(self, min_severity: Severity = Severity.HINT, source: str | None = None) -> list[Finding]:
        return [
            f
            for f in self.findings
            if f.severity >= min_severity and (source is None or f.source == source)
        ]

    def to_dict(self, include_design: bool = False) -> dict:
        data = {
            "summary": self.summary,
            "statistics": self.design.stats(),
            "counts": self.counts(),
            "findings": [f.to_dict() for f in self.findings],
            "requirements": [r.to_dict() for r in self.requirements],
            "questions": self.questions,
            "llm": {
                "used": self.llm_used,
                "backend": self.llm_backend,
                "model": self.llm_model,
                "error": self.llm_error,
            },
            "duration_s": round(self.duration_s, 2),
        }
        if include_design:
            data["design"] = build_context(self.design).data
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def exit_code(self, fail_on: str = "error") -> int:
        """Return a process exit code suitable for CI gating."""
        threshold = Severity.parse(fail_on)
        return 1 if any(f.severity >= threshold for f in self.findings) else 0


class SchematicReviewer:
    """High level entry point for reviews."""

    def __init__(
        self,
        cli: KicadCli | None = None,
        llm_config: LLMConfig | None = None,
    ) -> None:
        self.cli = cli or KicadCli()
        self.llm_config = llm_config

    # -- loading ---------------------------------------------------------- #

    def load(self, path: str, progress: ProgressFn | None = None) -> Design:
        _tick(progress, "Schaltplan wird gelesen...", 5)
        loader = SchematicLoader(self.cli)
        design = loader.load(path)
        _tick(progress, f"{len(design.component_symbols)} Bauteile, {len(design.nets)} Netze", 25)
        return design

    # -- rules ------------------------------------------------------------ #

    def run_rules(self, design: Design, progress: ProgressFn | None = None) -> tuple[list[Finding], list[dict]]:
        _tick(progress, "Regelprüfung läuft...", 35)
        erc: list[dict] = []
        try:
            erc = self.cli.run_erc(design.root_file, severity="all")
        except Exception:
            erc = []
        ctx = RuleContext(
            design=design,
            erc_violations=erc,
            no_connects=set(design.no_connects),
        )
        findings = check_all(ctx)
        _tick(progress, f"{len(findings)} deterministische Befunde", 50)
        return findings, erc

    # -- AI --------------------------------------------------------------- #

    def run_ai(
        self,
        design: Design,
        progress: ProgressFn | None = None,
        extra_instruction: str = "",
        focus_refs: list[str] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> tuple[list[Finding], list[Requirement], list[str], str, str]:
        """Run the AI stage.

        Returns ``(findings, requirements, questions, summary, error)``.
        """
        config = self.llm_config or LLMConfig.from_env()
        if config.backend in ("none", "", None):
            return [], [], [], "", "Kein KI-Backend konfiguriert"

        # Resolve the factory through the module so it can be swapped in tests and
# by embedders without reloading this file.
        client = _llm_module.build_client(config)
        ok, detail = client.available()
        if not ok:
            return [], [], [], "", f"Backend nicht verfügbar: {detail}"

        _tick(progress, f"Kontext wird erstellt ({config.backend}/{config.model})", 55)
        ctx = build_context(design, focus_refs=focus_refs)
        messages = render_prompt(ctx, extra_instruction)

        _tick(progress, "Modell wird abgefragt - das kann dauern...", 70)
        if cancel and cancel():
            raise ReviewCancelled()
        started = time.time()
        try:
            response = client.chat(messages)
        except LLMError as exc:
            return [], [], [], "", str(exc)
        if cancel and cancel():
            raise ReviewCancelled()
        if not response.ok:
            return [], [], [], "", response.error

        _tick(progress, f"Antwort ausgewertet ({time.time() - started:.1f}s)", 90)
        payload = parse_review_response(response.text)
        if not payload:
            return [], [], [], "", "Modellantwort war kein gültiges JSON"

        findings = [_ai_to_finding(item) for item in payload.get("findings", [])]
        findings = [f for f in findings if f is not None]
        requirements = [
            Requirement(
                id=str(item.get("id", f"REQ-{i + 1:03d}")),
                requirement=str(item.get("requirement", "")),
                status=str(item.get("status", "unklar")),
                evidence=str(item.get("evidence", "")),
                gap=str(item.get("gap", "")),
            )
            for i, item in enumerate(payload.get("requirements", []))
            if isinstance(item, dict) and item.get("requirement")
        ]
        questions = [str(q) for q in payload.get("questions", []) if q]
        summary = str(payload.get("summary", "")).strip()
        return findings, requirements, questions, summary, ""

    @staticmethod
    def _suppress_duplicates(findings: list[Finding]) -> None:
        """Remove findings that merely restate a KiCad ERC violation.

        ERC is authoritative for pin connectivity, so our own PIN-FLOATING
        scan would otherwise report the same unconnected pin a second time.
        """
        erc_refs: set[str] = set()
        for finding in findings:
            if finding.source == "erc":
                erc_refs.update(finding.refs)
        if not erc_refs:
            return
        findings[:] = [
            f
            for f in findings
            if f.source == "erc"
            or f.rule != "PIN-FLOATING"
            or not any(ref in erc_refs for ref in f.refs)
        ]

    # -- full pipeline ---------------------------------------------------- #

    def review(
        self,
        path: str,
        use_ai: bool = True,
        progress: ProgressFn | None = None,
        extra_instruction: str = "",
        focus_refs: list[str] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> ReviewResult:
        started = time.time()
        design = self.load(path, progress)
        findings, erc = self.run_rules(design, progress)
        self._suppress_duplicates(findings)

        result = ReviewResult(design=design, findings=findings, erc_violations=erc)
        result.summary = _auto_summary(result)

        if use_ai:
            ai_findings, reqs, questions, summary, error = self.run_ai(
                design, progress, extra_instruction, focus_refs, cancel
            )
            result.findings.extend(ai_findings)
            result.findings.sort(key=lambda f: (-int(f.severity), f.source, f.rule))
            result.requirements = reqs
            result.questions = questions
            if summary:
                result.summary = summary
            result.llm_used = bool(ai_findings or reqs or summary)
            result.llm_error = error
            config = self.llm_config or LLMConfig.from_env()
            result.llm_backend = config.backend
            result.llm_model = config.model

        result.duration_s = time.time() - started
        _tick(progress, "Fertig", 100)
        return result


def _ai_to_finding(item: dict) -> Finding | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title", "")).strip()
    detail = str(item.get("detail", "")).strip()
    if not title and not detail:
        return None
    severity = Severity.parse(str(item.get("severity", "info")))
    return Finding(
        rule=str(item.get("rule", "AI-FINDING")).strip().upper().replace(" ", "-") or "AI-FINDING",
        severity=severity,
        title=title or "KI-Hinweis",
        detail=detail,
        category=str(item.get("category", "general")),
        refs=[str(r) for r in item.get("refs", []) if r],
        nets=[str(n) for n in item.get("nets", []) if n],
        fix=str(item.get("fix", "")).strip(),
        source="ai",
        confidence=0.7,
    )


def _auto_summary(result: ReviewResult) -> str:
    counts = result.counts()
    stats = result.design.stats()
    parts = [
        f"{stats['components']} Bauteile auf {stats['sheets']} Blatt(ern), "
        f"{stats['nets']} Netze. "
        f"Deterministische Prüfung: {counts['error']} Fehler, {counts['warning']} Warnungen, "
        f"{counts['info']} Hinweise."
    ]
    if not result.llm_used:
        parts.append("KI-Analyse wurde nicht ausgeführt.")
    return " ".join(parts)


def _tick(progress: ProgressFn | None, message: str, percent: int) -> None:
    if progress is not None:
        progress(message, percent)