"""Command line interface for the KiCad AI Review plugin.

Examples::

    python -m kicad_ai review ./projekt.kicad_sch
    python -m kicad_ai review ./projekt --no-ai --format markdown
    python -m kicad_ai check-llm
    python -m kicad_ai mcp --project ./projekt
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .llm import LLMConfig, build_client
from .report import render_report
from .reviewer import SchematicReviewer
from .rules import Severity

__all__ = ["main"]

_SEVERITY_CHOICES = ["error", "warning", "info", "hint"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kicad-ai",
        description="KI-gestütztes Schaltplan-Review für KiCad",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="Schaltplan prüfen")
    review.add_argument("project", help="Pfad zu .kicad_sch/.kicad_pro/.kicad_pcb oder Ordner")
    review.add_argument("--no-ai", action="store_true", help="Nur deterministische Regeln ausführen")
    review.add_argument("--backend", choices=["ollama", "openai"], help="KI-Backend überschreiben")
    review.add_argument("--model", help="Modellname überschreiben")
    review.add_argument("--base-url", help="API-Endpunkt (OpenAI-kompatibel)")
    review.add_argument("--api-key", help="API-Schlüssel")
    review.add_argument("--min-severity", choices=_SEVERITY_CHOICES, default="hint")
    review.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    review.add_argument("--output", "-o", help="Ausgabe in Datei schreiben")
    review.add_argument("--fail-on", choices=_SEVERITY_CHOICES, default="error",
                        help="Exit-Code 1 ab diesem Schweregrad (für CI)")
    review.add_argument("--no-fail", action="store_true", help="Exit-Code immer 0")
    review.add_argument("--instruction", default="", help="Zusätzliche Aufgabe für die KI")
    review.add_argument("--requirements-only", action="store_true",
                        help="Nur Anforderungen ausgeben")

    status = sub.add_parser("check-llm", help="KI-Backend prüfen")
    status.add_argument("--backend", choices=["ollama", "openai"])
    status.add_argument("--base-url")
    status.add_argument("--list-models", action="store_true")

    mcp = sub.add_parser("mcp", help="MCP-Server auf stdio starten")
    mcp.add_argument("--project", help="Standardprojekt")

    tools = sub.add_parser("list-tools", help="MCP-Werkzeuge auflisten")

    rules = sub.add_parser("list-rules", help="Verfügbare Prüfregeln auflisten")
    return parser


def _apply_llm_overrides(args: argparse.Namespace) -> LLMConfig:
    config = LLMConfig.from_env()
    if getattr(args, "backend", None):
        config.backend = args.backend
    if getattr(args, "model", None):
        config.model = args.model
    if getattr(args, "base_url", None):
        config.base_url = args.base_url.rstrip("/")
    if getattr(args, "api_key", None):
        config.api_key = args.api_key
    return config


def _cmd_review(args: argparse.Namespace) -> int:
    config = _apply_llm_overrides(args)
    reviewer = SchematicReviewer(llm_config=config)

    def progress(message: str, percent: int) -> None:
        if args.format == "text":
            print(f"\r[{percent:3d}%] {message}", end="", file=sys.stderr, flush=True)

    result = reviewer.review(
        args.project,
        use_ai=not args.no_ai,
        progress=progress,
        extra_instruction=args.instruction,
    )
    if args.format == "text":
        print("\r" + " " * 70 + "\r", end="", file=sys.stderr)

    min_sev = Severity.parse(args.min_severity)
    findings = result.filtered(min_severity=min_sev)

    if args.format == "json":
        payload = result.to_dict()
        payload["findings"] = [f.to_dict() for f in findings]
        output = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        output = render_report(
            result,
            findings=findings,
            fmt=args.format,
            requirements_only=args.requirements_only,
        )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output)
        print(f"Bericht geschrieben: {args.output}", file=sys.stderr)
    else:
        print(output)

    if args.no_fail:
        return 0
    threshold = Severity.parse(args.fail_on)
    return 1 if any(f.severity >= threshold for f in result.findings) else 0


def _cmd_check_llm(args: argparse.Namespace) -> int:
    config = _apply_llm_overrides(args)
    print(f"Backend : {config.backend}")
    print(f"Modell  : {config.model}")
    print(f"Endpunkt: {config.base_url}")
    print(f"API-Key : {'gesetzt' if config.api_key else 'nicht gesetzt'}")
    print()

    if config.backend in ("none", ""):
        print("Kein Backend konfiguriert.")
        print("Setze KICAD_AI_BACKEND=ollama oder starte Ollama und versuche es erneut.")
        return 1

    try:
        client = build_client(config)
        ok, detail = client.available()
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: {exc}")
        return 1

    print(f"Status  : {'OK' if ok else 'NICHT ERREICHBAR'} - {detail}")
    if ok and args.list_models:
        try:
            models = client.models()
            print("\nVerfügbare Modelle:")
            for name in models:
                marker = " <- konfiguriert" if name == config.model else ""
                print(f"  - {name}{marker}")
        except Exception as exc:  # noqa: BLE001
            print(f"Modelle nicht abrufbar: {exc}")
    if not ok and config.backend == "ollama":
        print("\nOllama starten:  ollama serve")
        print("Modell laden:    ollama pull qwen2.5-coder:14b")
    return 0 if ok else 1


def _cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import main as mcp_main

    argv = ["--project", args.project] if args.project else []
    return mcp_main(argv)


def _cmd_list_tools(_args: argparse.Namespace) -> int:
    from .mcp_server import TOOLS

    print(json.dumps(TOOLS(), ensure_ascii=False, indent=2))
    return 0


def _cmd_list_rules(_args: argparse.Namespace) -> int:
    from .rules import RULES

    width = max(len(k) for k in RULES)
    for rule, description in RULES.items():
        print(f"{rule:<{width}}  {description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers: dict[str, Any] = {
        "review": _cmd_review,
        "check-llm": _cmd_check_llm,
        "mcp": _cmd_mcp,
        "list-tools": _cmd_list_tools,
        "list-rules": _cmd_list_rules,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())