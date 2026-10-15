"""Full check of the Ollama integration against a running Ollama.

Goes from "is anything there" to "does a real review work", reporting timings
and a clear pass/fail per stage:

    python tools/test_ollama.py
    python tools/test_ollama.py --project C:\\Pfad\\board.kicad_sch
    python tools/test_ollama.py --full        # also run the real AI review

Exit code 0 means every executed stage passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from kicad_ai.config import AIConfig, config_path  # noqa: E402
from kicad_ai.llm import LLMConfig, LLMError, OllamaClient  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"

DEFAULT_PROJECT = r"C:\Users\stani\Documents\KiCad\Projekte\RC_Filter"


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.checks = 0

    def ok(self, name: str, detail: str = "") -> None:
        self.checks += 1
        print(f"  {GREEN}[OK]{RESET}   {name}")
        if detail:
            print(f"        {DIM}{detail}{RESET}")

    def fail(self, name: str, detail: str = "") -> None:
        self.checks += 1
        self.failures += 1
        print(f"  {RED}[FEHL]{RESET} {name}")
        if detail:
            print(f"        {detail}")

    def warn(self, name: str, detail: str = "") -> None:
        self.checks += 1
        print(f"  {YELLOW}[HINW]{RESET} {name}")
        if detail:
            print(f"        {DIM}{detail}{RESET}")

    def stage(self, title: str) -> None:
        print()
        print(f"--- {title} ---")


def stage_config(report: Report) -> LLMConfig:
    report.stage("1. Konfiguration")
    path = config_path()
    stored = AIConfig.load()
    cfg = LLMConfig.from_env()

    if stored.backend == "ollama":
        report.ok("Backend ist Ollama")
    else:
        report.warn("Backend ist nicht Ollama", f"backend={stored.backend}")

    if path.is_file():
        report.ok(f"Konfigurationsdatei gelesen", str(path))
    else:
        report.warn("Keine Konfigurationsdatei", "Es gelten die Standardwerte")

    report.ok("Modell konfiguriert", cfg.model)
    report.ok("Endpunkt konfiguriert", cfg.base_url)
    return cfg


def stage_connect(report: Report, client: OllamaClient) -> bool:
    report.stage("2. Verbindung")
    ok, detail = client.available()
    if ok:
        report.ok("Server erreichbar", detail)
        return True
    report.fail("Server nicht erreichbar", detail)
    return False


def stage_models(report: Report, client: OllamaClient, cfg: LLMConfig) -> None:
    report.stage("3. Modelle")
    try:
        installed = client.models()
    except Exception as exc:  # noqa: BLE001
        report.fail("Modelliste nicht abrufbar", str(exc))
        return

    if not installed:
        report.fail("Keine Modelle installiert", "ollama pull qwen2.5-coder:7b")
        return

    report.ok(f"{len(installed)} Modell(e) installiert")

    for name in installed:
        info = client.model_details(name)
        size_gb = info.get("size_bytes", 0) / (1024 ** 3)
        report.ok(
            name,
            f"{size_gb:.1f} GB"
            + (f", {info['parameter_size']} {info['quantization']}" if info.get("parameter_size") else ""),
        )

    if client.is_installed(cfg.model):
        report.ok("Konfiguriertes Modell ist vorhanden", cfg.model)
    else:
        best = client.recommend()
        report.warn(
            "Konfiguriertes Modell fehlt",
            f"{cfg.model} -> Empfehlung: {best}",
        )


def stage_capabilities(report: Report, client: OllamaClient) -> None:
    report.stage("4. Fähigkeiten des konfigurierten Modells")
    try:
        info = client.show()
    except Exception as exc:  # noqa: BLE001
        report.fail("Modellinformationen nicht abrufbar", str(exc))
        return

    context = info.get("context_length", 0)
    if context:
        report.ok("Kontextfenster", f"{context:,} Token".replace(",", "."))
        if context < 8192:
            report.warn(
                "Kontextfenster ist knapp",
                "Bei großen Schaltplänen Review-Kontext kürzen oder "
                "größeres Modell verwenden",
            )
    else:
        report.warn("Kontextfenster unbekannt")

    caps = info.get("capabilities", [])
    if caps:
        report.ok("Fähigkeiten", ", ".join(caps[:6]))
    if "tools" in caps:
        report.ok("Tool-Unterstützung vorhanden")


def stage_inference(report: Report, client: OllamaClient) -> None:
    report.stage("5. Einfache Inferenz (Antwortet das Modell?)")
    messages = [
        {"role": "system", "content": "Antworte ausschließlich mit JSON."},
        {
            "role": "user",
            "content": 'Antworte mit {"ok": true, "componenten": 7}. '
            "Kein Text drumherum.",
        },
    ]

    seen = {"chars": 0}
    started = time.time()
    try:
        response = client.chat(messages, progress=lambda n, text: seen.__setitem__("chars", len(text)))
    except LLMError as exc:
        report.fail("Inferenz fehlgeschlagen", str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        report.fail("Unerwarteter Fehler bei der Inferenz", f"{type(exc).__name__}: {exc}")
        return

    duration = time.time() - started
    report.ok(
        "Modell antwortet",
        f"{duration:.1f}s, {len(response.text)} Zeichen, "
        f"{response.prompt_tokens} Prompt- / {response.completion_tokens} Antwort-Token",
    )
    if seen["chars"]:
        report.ok("Streaming aktiv", f"{seen['chars']} Zeichen gestreamt")

    try:
        from kicad_ai.context import parse_review_response

        parsed = parse_review_response(response.text)
    except Exception as exc:  # noqa: BLE001
        report.fail("Antwort nicht lesbar", str(exc))
        return

    if isinstance(parsed, dict) and parsed:
        report.ok("Antwort ist gültiges JSON", json.dumps(parsed, ensure_ascii=False)[:70])
    else:
        report.warn("Antwort war kein JSON", response.text[:120])
        report.warn(
            "Hinweis",
            "Das Modell kann dann keine strukturierten Befunde liefern. "
            "Ein Coding-Modell verhält sich hier deutlich zuverlässiger.",
        )


def stage_review(report: Report, project: str) -> None:
    report.stage("6. Vollständiges Review mit KI")
    from kicad_ai.reviewer import SchematicReviewer

    started = time.time()
    result = SchematicReviewer().review(project, use_ai=True)
    duration = time.time() - started

    counts = result.counts()
    report.ok(
        "Review abgeschlossen",
        f"{duration:.1f}s, {sum(counts.values())} Befunde, "
        f"{len(result.requirements)} Anforderungen",
    )

    if result.llm_used:
        report.ok("KI-Beteiligung", f"{result.llm_backend} / {result.llm_model}")
        report.ok("Zusammenfassung vorhanden", result.summary[:80])
    else:
        report.fail("KI lieferte kein Ergebnis", result.llm_error or "unbekannt")

    for finding in [f for f in result.findings if f.source == "ai"][:3]:
        report.ok("KI-Befund", f"{finding.rule}: {finding.title[:50]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ollama-Anbindung testen")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--full", action="store_true", help="auch das KI-Review ausführen")
    args = parser.parse_args()

    print()
    print("=" * 66)
    print("  Ollama-Anbindung testen")
    print("=" * 66)

    report = Report()
    cfg = stage_config(report)

    client = OllamaClient(cfg)
    if not stage_connect(report, client):
        print()
        print(f"{RED}Abbruch: Ollama ist nicht erreichbar.{RESET}")
        print("  1. Ollama starten:      ollama serve")
        print("  2. Modell laden:        ollama pull qwen2.5-coder:7b")
        return 1

    stage_models(report, client, cfg)
    stage_capabilities(report, client)
    stage_auto_repair(report, client, cfg)
    stage_inference(report, client)

    if args.full:
        if os.path.isdir(args.project):
            stage_review(report, args.project)
        else:
            report.warn("Projekt nicht gefunden", args.project)
    else:
        print()
        print(f"{DIM}Hinweis: --full fuehrt zusaetzlich das komplette KI-Review aus.{RESET}")

    print()
    print("=" * 66)
    if report.failures == 0:
        print(f"  {GREEN}ALLE {report.checks} PRUEFUNGEN BESTANDEN{RESET}")
    else:
        print(f"  {RED}{report.failures} von {report.checks} Pruefungen fehlgeschlagen{RESET}")
    print("=" * 66)
    return 1 if report.failures else 0


def stage_auto_repair(report: Report, client: OllamaClient, cfg: LLMConfig) -> None:
    """Verify that a missing model is swapped for a usable one."""
    report.stage("7. Automatische Modelwahl")
    missing = "__does_not_exist__"
    config = LLMConfig(
        backend="ollama",
        model=missing,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
    )
    from kicad_ai.reviewer import SchematicReviewer

    reviewer = SchematicReviewer(llm_config=config)
    repaired = reviewer._ensure_model(config)

    if repaired:
        report.ok("Fehlendes Modell erkannt und ersetzt", f"{missing} -> {repaired}")
        if config.model == repaired:
            report.ok("Konfiguration im Speicher aktualisiert")
    elif client.is_installed(missing):
        report.ok("Modell war vorhanden, kein Wechsel nötig")
    else:
        report.fail("Kein Ersatzmodell gefunden")

    best = client.recommend()
    if best:
        report.ok("Empfehlung berechnet", best)


if __name__ == "__main__":
    raise SystemExit(main())